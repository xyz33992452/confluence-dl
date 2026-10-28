import unittest
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

import web_server
from web_server import BIN, _find_binary, _windows_desktop_dir, build_cli_invocation, default_output_dir


class DownloadInvocationTests(unittest.TestCase):
  def test_default_output_is_desktop_export_folder(self):
    with patch("web_server.Path.home", return_value=__import__("pathlib").Path("/Users/test")):
      self.assertEqual(default_output_dir(), "/Users/test/Desktop/Confluence Export")

  def test_windows_desktop_uses_registry_path(self):
    key = object()
    registry = SimpleNamespace(
      HKEY_CURRENT_USER=object(),
      OpenKey=lambda *_args: __import__("contextlib").nullcontext(key),
      QueryValueEx=lambda *_args: (r"C:\Users\test\OneDrive\Desktop", 1),
    )
    with patch.dict(sys.modules, {"winreg": registry}):
      self.assertEqual(str(_windows_desktop_dir()), r"C:\Users\test\OneDrive\Desktop")

  def test_explicit_binary_override_takes_precedence(self):
    with patch("web_server.BIN", BIN), patch.dict("os.environ", {"CONFLUENCE_DL_BIN": "/tmp/custom-cli"}), patch(
      "pathlib.Path.exists", return_value=True
    ), patch("web_server.os.access", return_value=True):
      self.assertEqual(str(_find_binary()), "/tmp/custom-cli")

  def test_token_is_passed_through_environment_not_arguments(self):
    token = "secret-test-token"

    command, env = build_cli_invocation(
      binary="/tmp/confluence-dl",
      site="https://example.atlassian.net",
      user="user@example.com",
      token=token,
      page="123",
      output="/tmp/export",
      attachments=True,
      max_depth=0,
      max_bandwidth_mb_s=0.5,
    )

    self.assertNotIn(token, command)
    self.assertEqual(env["CONFLUENCE_TOKEN"], token)
    self.assertEqual(command[command.index("--max-depth") + 1], "0")
    self.assertEqual(command[command.index("--max-bandwidth-mb-s") + 1], "0.5")

  def test_overwrite_flag_is_only_passed_when_requested(self):
    args = {
      "binary": "/tmp/confluence-dl",
      "site": "https://example.atlassian.net",
      "user": "user@example.com",
      "token": "secret",
      "page": "123",
      "output": "/tmp/export",
      "attachments": False,
      "max_depth": None,
      "max_bandwidth_mb_s": None,
    }

    skip_existing, _ = build_cli_invocation(**args)
    overwrite_existing, _ = build_cli_invocation(**args, overwrite=True)

    self.assertNotIn("--overwrite", skip_existing)
    self.assertIn("--overwrite", overwrite_existing)


class WebRunIntegrationTests(unittest.IsolatedAsyncioTestCase):
  async def test_empty_output_uses_desktop_export_directory(self):
    with tempfile.TemporaryDirectory() as tmp:
      fake_cli = os.path.join(tmp, "fake-cli")
      with open(fake_cli, "w", encoding="utf-8") as script:
        script.write(
          "#!/usr/bin/env python3\n"
          "import json, sys\n"
          "print(json.dumps({'kind':'output_arg','value':sys.argv[sys.argv.index('--output') + 1]}), file=sys.stderr, flush=True)\n"
          "print(json.dumps({'kind':'overwrite_arg','enabled':'--overwrite' in sys.argv}), file=sys.stderr, flush=True)\n"
        )
      os.chmod(fake_cli, 0o755)

      with patch.dict(os.environ, {"CONFLUENCE_DL_BIN": fake_cli}), patch(
        "web_server.LOG_DIR", Path(tmp) / "logs"
      ):
        client = TestClient(TestServer(web_server.build_app()))
        await client.start_server()
        try:
          response = await client.post(
            "/api/run",
            json={
              "page": "123",
              "site": "https://example.atlassian.net",
              "user": "user@example.com",
              "token": "secret",
              "output": "",
            },
          )
          stream = await response.text()
          self.assertEqual(response.status, 200)
          self.assertIn(default_output_dir(), stream)
          self.assertIn('"kind": "overwrite_arg", "enabled": false', stream)
        finally:
          await client.close()

  async def test_sse_progress_and_child_token_environment(self):
    with tempfile.TemporaryDirectory() as tmp:
      fake_cli = os.path.join(tmp, "fake-cli")
      with open(fake_cli, "w", encoding="utf-8") as script:
        script.write(
          "#!/usr/bin/env python3\n"
          "import json, os, sys\n"
          "assert '--overwrite' in sys.argv\n"
          "assert '--max-depth' in sys.argv and sys.argv[sys.argv.index('--max-depth') + 1] == '0'\n"
          "assert '--max-bandwidth-mb-s' in sys.argv and sys.argv[sys.argv.index('--max-bandwidth-mb-s') + 1] == '0.5'\n"
          "print(json.dumps({'kind':'tree','total':3}), file=sys.stderr, flush=True)\n"
          "print(json.dumps({'kind':'page_done','index':1,'total':3}), file=sys.stderr, flush=True)\n"
          "print(json.dumps({'kind':'token_present','ok':os.environ.get('CONFLUENCE_TOKEN') == 'secret'}), file=sys.stderr, flush=True)\n"
        )
      os.chmod(fake_cli, 0o755)

      with patch.dict(os.environ, {"CONFLUENCE_DL_BIN": fake_cli}), patch(
        "web_server.LOG_DIR", Path(tmp) / "logs"
      ):
        client = TestClient(TestServer(web_server.build_app()))
        await client.start_server()
        try:
          response = await client.post(
            "/api/run",
            json={
              "page": "123",
              "site": "https://example.atlassian.net",
              "user": "user@example.com",
              "token": "secret",
              "output": tmp,
              "max_depth": 0,
              "max_bandwidth_mb_s": 0.5,
              "overwrite": True,
            },
          )
          stream = await response.text()
          self.assertEqual(response.status, 200)
          self.assertIn('event: started\ndata: {"page": "123"', stream)
          self.assertIn('"kind": "tree", "total": 3', stream)
          self.assertIn('"kind": "page_done", "index": 1, "total": 3', stream)
          self.assertIn('"kind": "token_present", "ok": true', stream)
          self.assertIn('event: exit\ndata: {"code": 0}', stream)
          self.assertNotIn("secret", stream)
        finally:
          await client.close()

  async def test_failed_run_is_saved_as_redacted_downloadable_diagnostic_log(self):
    with tempfile.TemporaryDirectory() as tmp:
      fake_cli = os.path.join(tmp, "fake-cli")
      log_dir = os.path.join(tmp, "logs")
      with open(fake_cli, "w", encoding="utf-8") as script:
        script.write(
          "#!/usr/bin/env python3\n"
          "import json, sys\n"
          "print(json.dumps({'kind':'page_start','title':'A page'}), file=sys.stderr, flush=True)\n"
          "print('Error: failed with secret-test-token', file=sys.stderr, flush=True)\n"
          "print('last successful output line', flush=True)\n"
          "sys.exit(1)\n"
        )
      os.chmod(fake_cli, 0o755)

      with patch.dict(os.environ, {"CONFLUENCE_DL_BIN": fake_cli}), patch("web_server.LOG_DIR", Path(log_dir)):
        client = TestClient(TestServer(web_server.build_app()))
        await client.start_server()
        try:
          response = await client.post(
            "/api/run",
            json={
              "page": "123",
              "site": "https://example.atlassian.net",
              "user": "user@example.com",
              "token": "secret-test-token",
              "output": tmp,
            },
          )
          stream = await response.text()
          self.assertIn('event: exit\ndata: {"code": 1}', stream)

          files = os.listdir(log_dir)
          self.assertEqual(len(files), 1)
          with open(os.path.join(log_dir, files[0]), encoding="utf-8") as saved:
            log_text = saved.read()
          self.assertIn('"kind": "page_start"', log_text)
          self.assertIn("last successful output line", log_text)
          self.assertIn("exit", log_text)
          self.assertIn("[REDACTED]", log_text)
          self.assertNotIn("secret-test-token", log_text)

          download = await client.get("/api/logs/latest")
          self.assertEqual(download.status, 200)
          self.assertIn("attachment; filename=", download.headers["Content-Disposition"])
          self.assertEqual(await download.text(), log_text)
        finally:
          await client.close()


if __name__ == "__main__":
  unittest.main()
