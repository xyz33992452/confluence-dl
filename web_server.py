"""Local web frontend for the confluence-dl CLI.

A thin aiohttp app that:
  * serves a single-page HTML/JS UI at ``/``
  * accepts a download request at ``POST /api/run`` and streams
    ``text/event-stream`` progress events read from the Rust CLI's stderr
    (the CLI prints one JSON Lines record per milestone when invoked with
    ``--json-events``).

The web layer only knows how to spawn the compiled ``confluence-dl`` binary
and re-broadcast its events — all Confluence I/O stays inside the Rust code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional
from uuid import uuid4

from aiohttp import web

ROOT = Path(__file__).resolve().parent
BIN_NAME = "confluence-dl.exe" if os.name == "nt" else "confluence-dl"
BIN = ROOT / "target" / "release" / BIN_NAME
INDEX_HTML = ROOT / "web" / "index.html"
LOG_DIR = ROOT / ".runtime" / "logs"
LOG_RETENTION = 20


def _find_binary() -> Path:
  """Locate the compiled ``confluence-dl`` binary.

  Uses an explicitly configured ``CONFLUENCE_DL_BIN`` first, then prefers
  ``target/release/confluence-dl`` next to the repo, then checks ``$PATH``.
  """
  env_bin = os.environ.get("CONFLUENCE_DL_BIN")
  if env_bin and Path(env_bin).exists() and os.access(env_bin, os.X_OK):
    return Path(env_bin)
  if BIN.exists() and os.access(BIN, os.X_OK):
    return BIN
  on_path = shutil.which("confluence-dl")
  if on_path:
    return Path(on_path)
  raise FileNotFoundError(
    "Could not locate the confluence-dl binary. Build it with "
    "`cargo build --release` or set CONFLUENCE_DL_BIN to the executable."
  )


def default_output_dir() -> str:
  """Return the same Desktop export directory shown as the UI default."""
  desktop = _windows_desktop_dir() if os.name == "nt" else Path.home() / "Desktop"
  return str(desktop / "Confluence Export")


def _windows_desktop_dir() -> Path:
  """Read Windows' configured Desktop path, including OneDrive redirection."""
  try:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
      desktop, _ = winreg.QueryValueEx(key, "Desktop")
    return Path(os.path.expandvars(desktop))
  except (ImportError, OSError):
    return Path.home() / "Desktop"


async def _drain(
  stream: asyncio.StreamReader, queue: asyncio.Queue[tuple[str, Optional[str]]], stream_name: str
) -> None:
  """Read one process stream and enqueue lines with their source."""
  try:
    while True:
      line = await stream.readline()
      if not line:
        queue.put_nowait((stream_name, None))
        return
      queue.put_nowait((stream_name, line.decode("utf-8", errors="replace").rstrip("\n")))
  except Exception:
    queue.put_nowait((stream_name, None))


def build_cli_invocation(
  binary: str,
  site: str,
  user: str,
  token: str,
  page: str,
  output: str,
  attachments: bool,
  max_depth: Optional[int],
  max_bandwidth_mb_s: Optional[float],
  overwrite: bool = False,
) -> tuple[list[str], dict[str, str]]:
  """Build CLI arguments and credentials separately to keep the token out of argv."""
  cmd = [
    binary,
    "--json-events",
    "--url",
    site,
    "--user",
    user,
    "--output",
    output,
    "--children",
  ]
  if overwrite:
    cmd.append("--overwrite")
  if attachments:
    cmd.append("--attachments")
  if max_depth is not None:
    cmd += ["--max-depth", str(int(max_depth))]
  if max_bandwidth_mb_s is not None:
    cmd += ["--max-bandwidth-mb-s", str(max_bandwidth_mb_s)]
  cmd.append(page)
  return cmd, {
    "CONFLUENCE_URL": site,
    "CONFLUENCE_USER": user,
    "CONFLUENCE_TOKEN": token,
  }


async def _stream_cli_events(
  cmd: list[str], cwd: Path, child_env: dict[str, str]
) -> AsyncIterator[tuple[str, dict]]:
  """Yield ``(kind, payload)`` tuples parsed from the CLI's output streams.

  stderr carries structured events; stdout carries the human-readable log
  we forward for the UI to display. Once both streams hit EOF, a final
  ``("exit", {"code": N})`` is yielded so the UI can disable controls.
  """
  proc = await asyncio.create_subprocess_exec(
    *cmd,
    cwd=str(cwd),
    env={**os.environ, **child_env},
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
  )
  if proc.stderr is None or proc.stdout is None:
    raise RuntimeError("Failed to capture CLI output streams")

  output_queue: asyncio.Queue[tuple[str, Optional[str]]] = asyncio.Queue()
  stream_tasks = [
    asyncio.create_task(_drain(proc.stderr, output_queue, "stderr")),
    asyncio.create_task(_drain(proc.stdout, output_queue, "stdout")),
  ]

  open_streams = {"stderr", "stdout"}
  while open_streams:
    stream_name, line = await output_queue.get()
    if line is None:
      open_streams.discard(stream_name)
    elif stream_name == "stderr":
      try:
        yield "event", json.loads(line)
      except json.JSONDecodeError:
        yield "stderr", {"message": line}
    else:
      yield "stdout", {"message": line}

  rc = await proc.wait()
  yield "exit", {"code": rc}

  await asyncio.gather(*stream_tasks)


def _redact_secret(value, secret: str):
  """Replace the API token anywhere in a diagnostic payload before saving it."""
  if isinstance(value, str):
    return value.replace(secret, "[REDACTED]") if secret else value
  if isinstance(value, list):
    return [_redact_secret(item, secret) for item in value]
  if isinstance(value, dict):
    return {key: _redact_secret(item, secret) for key, item in value.items()}
  return value


class _DiagnosticLog:
  """Write each run event to a local JSONL file without persisting the API token."""

  def __init__(self, token: str):
    self.token = token
    self.path: Optional[Path] = None
    try:
      LOG_DIR.mkdir(parents=True, exist_ok=True)
      stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
      self.path = LOG_DIR / f"confluence-dl-{stamp}-{uuid4().hex[:8]}.jsonl"
      self._prune_old_logs()
    except OSError:
      self.path = None

  def _prune_old_logs(self) -> None:
    """Keep the newest diagnostic logs so repeated runs do not grow without bound."""
    try:
      logs = sorted(LOG_DIR.glob("confluence-dl-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
      for old_log in logs[LOG_RETENTION:]:
        old_log.unlink(missing_ok=True)
    except OSError:
      pass

  def write(self, event: str, payload: dict) -> None:
    """Append one sanitized event; a log I/O error must not stop a download."""
    if self.path is None:
      return
    record = {
      "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
      "event": event,
      "data": _redact_secret(payload, self.token),
    }
    try:
      with self.path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
      self.path = None


async def handle_latest_log(_request: web.Request) -> web.StreamResponse:
  """Download the most recent persisted run log."""
  try:
    logs = sorted(LOG_DIR.glob("confluence-dl-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
  except OSError:
    logs = []
  if not logs:
    return web.json_response({"error": "No diagnostic logs are available yet"}, status=404)
  return web.FileResponse(
    logs[0],
    headers={"Content-Disposition": f'attachment; filename="{logs[0].name}"'},
  )


def _sse(payload: dict, event: Optional[str] = None) -> bytes:
  """Format a payload as a single SSE message."""
  data = json.dumps(payload, ensure_ascii=False)
  head = f"event: {event}\n" if event else ""
  return f"{head}data: {data}\n\n".encode("utf-8")


async def handle_run(request: web.Request) -> web.StreamResponse:
  """Start a download and stream its progress as Server-Sent Events."""
  try:
    body = await request.json()
  except json.JSONDecodeError:
    return web.json_response({"error": "Invalid JSON body"}, status=400)

  page = (body.get("page") or "").strip()
  site = (body.get("site") or "").strip()
  user = (body.get("user") or "").strip()
  token = (body.get("token") or "").strip()
  output = (body.get("output") or "").strip() or default_output_dir()
  download_attachments = bool(body.get("attachments"))
  max_depth = body.get("max_depth")
  overwrite = bool(body.get("overwrite"))
  raw_bandwidth = body.get("max_bandwidth_mb_s")
  try:
    max_bandwidth_mb_s = float(raw_bandwidth) if raw_bandwidth not in (None, "") else None
  except (TypeError, ValueError):
    return web.json_response({"error": "max_bandwidth_mb_s must be a number"}, status=400)
  if max_bandwidth_mb_s is not None and (not math.isfinite(max_bandwidth_mb_s) or max_bandwidth_mb_s < 0.001):
    return web.json_response({"error": "max_bandwidth_mb_s must be at least 0.001 MB/s"}, status=400)

  if not page or not site or not user or not token:
    return web.json_response({"error": "page/site/user/token are required"}, status=400)

  try:
    binary = _find_binary()
  except FileNotFoundError as e:
    return web.json_response({"error": str(e)}, status=500)

  try:
    cmd, child_env = build_cli_invocation(
      str(binary), site, user, token, page, output, download_attachments, max_depth, max_bandwidth_mb_s, overwrite
    )
  except (TypeError, ValueError) as exc:
    return web.json_response({"error": f"Invalid download options: {exc}"}, status=400)

  response = web.StreamResponse(
    status=200,
    reason="OK",
    headers={
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    },
  )
  await response.prepare(request)

  diagnostic = _DiagnosticLog(token)
  started = {
    "page": page,
    "site": site,
    "user": user,
    "output": output,
    "attachments": download_attachments,
    "overwrite": overwrite,
    "max_depth": max_depth,
    "max_bandwidth_mb_s": max_bandwidth_mb_s,
    "log_file": diagnostic.path.name if diagnostic.path else None,
  }
  started = _redact_secret(started, token)
  diagnostic.write("started", started)
  await response.write(_sse(started, event="started"))

  try:
    async for kind, payload in _stream_cli_events(cmd, ROOT, child_env):
      sanitized = _redact_secret(payload, token)
      diagnostic.write(kind, sanitized)
      await response.write(_sse(sanitized, event=kind))
  except Exception as exc:  # noqa: BLE001 — surface anything to the UI
    error = _redact_secret({"message": str(exc)}, token)
    diagnostic.write("error", error)
    await response.write(_sse(error, event="error"))

  closed = {"done": True}
  diagnostic.write("closed", closed)
  await response.write(_sse(closed, event="closed"))
  return response


async def handle_index(_request: web.Request) -> web.Response:
  """Serve the single-page HTML UI."""
  if not INDEX_HTML.exists():
    return web.Response(text="index.html not found", status=500)
  return web.Response(text=INDEX_HTML.read_text(encoding="utf-8"), content_type="text/html")


def build_app() -> web.Application:
  app = web.Application()
  app.router.add_get("/", handle_index)
  app.router.add_post("/api/run", handle_run)
  app.router.add_get("/api/logs/latest", handle_latest_log)
  return app


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="confluence-dl web UI")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=18770)
  return parser.parse_args()


def main() -> None:
  args = parse_args()
  app = build_app()
  web.run_app(app, host=args.host, port=args.port, print=lambda *a, **kw: None)


if __name__ == "__main__":
  main()
