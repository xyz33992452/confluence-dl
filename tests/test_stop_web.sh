#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
SERVICE_PID=""
UNRELATED_PID=""

cleanup() {
  if [[ -n "$SERVICE_PID" ]]; then
    kill "$SERVICE_PID" 2>/dev/null || true
    wait "$SERVICE_PID" 2>/dev/null || true
  fi
  if [[ -n "$UNRELATED_PID" ]]; then
    kill "$UNRELATED_PID" 2>/dev/null || true
    wait "$UNRELATED_PID" 2>/dev/null || true
  fi
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT

mkdir -p "$TEMP_ROOT/.runtime"
cp "$ROOT/stop-web.command" "$TEMP_ROOT/stop-web.command"
cat > "$TEMP_ROOT/web_server.py" <<'PY'
import os
import subprocess
import time

child = subprocess.Popen(["sleep", "60"])
with open(os.path.join(os.path.dirname(__file__), "child.pid"), "w") as pid_file:
    pid_file.write(str(child.pid))
try:
    time.sleep(60)
finally:
    child.terminate()
PY

python3 "$TEMP_ROOT/web_server.py" --host 127.0.0.1 --port 18770 &
SERVICE_PID=$!
printf '%s\n' "$SERVICE_PID" > "$TEMP_ROOT/.runtime/web.pid"
sleep 0.1

sleep 60 &
UNRELATED_PID=$!
for _ in $(seq 1 20); do
  [[ -f "$TEMP_ROOT/child.pid" ]] && break
  sleep 0.05
done
CHILD_PID="$(cat "$TEMP_ROOT/child.pid")"

bash "$TEMP_ROOT/stop-web.command"
wait "$SERVICE_PID" 2>/dev/null || true

if kill -0 "$SERVICE_PID" 2>/dev/null; then
  echo "Expected the matching Web service process to stop" >&2
  exit 1
fi
if ! kill -0 "$UNRELATED_PID" 2>/dev/null; then
  echo "The stop script terminated an unrelated process" >&2
  exit 1
fi
if kill -0 "$CHILD_PID" 2>/dev/null; then
  echo "Expected the service's child process to stop" >&2
  exit 1
fi

echo "PASS: stopped the Web service and its child, leaving unrelated processes running"
