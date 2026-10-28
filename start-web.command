#!/bin/bash
# confluence-dl Web UI launcher (macOS / Linux).
#
# Responsibilities:
#   * kill any stale instance from a previous run
#   * build the Rust CLI in release mode if needed
#   * boot the aiohttp web server (PID file under .runtime/web.pid)
#   * open the UI in the default browser

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# Finder-launched Terminal sessions may not inherit the shell PATH.
export PATH="$HOME/.cargo/bin:$PATH"

HOST="${CONFLUENCE_WEB_HOST:-127.0.0.1}"
PORT="${CONFLUENCE_WEB_PORT:-18770}"
RUNTIME="$ROOT/.runtime"
PID_FILE="$RUNTIME/web.pid"
LOG_FILE="$RUNTIME/web.log"
mkdir -p "$RUNTIME"

say() { printf '\n%s\n' "$*"; }

# --- Pre-flight: kill any previous instance --------------------------------

kill_stale() {
  if [[ -f "$PID_FILE" ]]; then
    local old_pid
    old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
      say "停止上一次运行的 Web 服务（PID ${old_pid}）……"
      kill "$old_pid" 2>/dev/null || true
      sleep 1
      kill -9 "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  # Belt-and-suspenders: nuke anything still bound to our port.
  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      say "释放端口 ${PORT} 上的旧进程……"
      kill $pids 2>/dev/null || true
      sleep 1
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

# --- Pre-flight: build the Rust binary -------------------------------------

build_app() {
  if ! command -v cargo >/dev/null 2>&1; then
    say "未检测到 cargo。请先安装 Rust：https://rustup.rs/"
    exit 1
  fi
  say "正在检查并构建 confluence-dl（release 模式）……"
  RUSTUP_TOOLCHAIN=stable cargo +stable \
    --config 'source.crates-io.replace-with="rsproxy"' \
    --config 'source.rsproxy.registry="sparse+https://rsproxy.cn/index/"' \
    build --release
}

# --- venv bootstrap --------------------------------------------------------

ensure_venv() {
  if [[ -x "$ROOT/.venv-web/bin/python" ]]; then
    return
  fi
  if ! command -v uv >/dev/null 2>&1; then
    say "未检测到 uv。请先安装 uv：https://docs.astral.sh/uv/"
    exit 1
  fi
  say "创建 Python 虚拟环境并安装 aiohttp……"
  uv venv "$ROOT/.venv-web" --python 3.11
  uv pip install --python "$ROOT/.venv-web/bin/python" aiohttp
}

# --- Run -------------------------------------------------------------------

kill_stale
build_app
ensure_venv

say "启动 Web 服务： http://$HOST:$PORT"
nohup "$ROOT/.venv-web/bin/python" "$ROOT/web_server.py" --host "$HOST" --port "$PORT" \
  >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

# Wait for readiness before asking macOS to open the page.
ready=false
for _ in $(seq 1 40); do
  if curl -sf "http://$HOST:$PORT/" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 0.25
done

URL="http://$HOST:$PORT/"
if [[ "$ready" != true ]]; then
  say "Web 服务启动失败，未能在 10 秒内访问 $URL。"
  say "请检查日志：$LOG_FILE"
  exit 1
fi

say "正在默认浏览器中打开 $URL"
if ! /usr/bin/open -u "$URL"; then
  say "无法自动打开浏览器，请手动访问：$URL"
fi

cat <<'NEXT'

服务已在后台运行。要停止它：
  双击项目目录中的 stop-web.command

日志：.runtime/web.log

按回车键关闭此窗口（Web 服务会继续在后台运行）……
NEXT
read -r _
exit 0
