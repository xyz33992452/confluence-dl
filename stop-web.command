#!/bin/bash
# Stop the confluence-dl Web service started from this project folder.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/.runtime/web.pid"

if [[ ! -f "$PID_FILE" ]]; then
  printf '没有找到服务 PID 文件，服务可能已经停止。\n'
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
  rm -f "$PID_FILE"
  printf 'PID 文件无效，已清理。\n'
  exit 0
fi

if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  printf '服务进程已停止，已清理旧 PID 文件。\n'
  exit 0
fi

command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
if [[ "$command_line" != *"$ROOT/web_server.py"* || "$command_line" != *"--port "* ]]; then
  printf 'PID %s 当前不属于本项目的 Web 服务；为避免误停其他程序，未执行停止。\n' "$pid" >&2
  exit 1
fi

printf '正在停止本项目 Web 服务（PID %s）……\n' "$pid"
# The web server owns the active confluence-dl download process. Stop its
# direct children too so a download cannot outlive the stopped service.
children="$(pgrep -P "$pid" 2>/dev/null || true)"
if [[ -n "$children" ]]; then
  printf '发现进行中的下载任务，将一并停止。\n'
  kill -TERM $children 2>/dev/null || true
  sleep 0.3
  for child in $children; do
    if kill -0 "$child" 2>/dev/null; then
      kill -KILL "$child" 2>/dev/null || true
    fi
  done
fi

kill -TERM "$pid" 2>/dev/null || true
for _ in $(seq 1 30); do
  state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
  if [[ -z "$state" || "$state" == Z* ]]; then
    break
  fi
  sleep 0.2
done

state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
if [[ -n "$state" && "$state" != Z* ]]; then
  printf '服务未及时退出，正在强制停止……\n'
  kill -KILL "$pid" 2>/dev/null || true
fi

rm -f "$PID_FILE"
printf '服务及其下载进程已停止。\n'
