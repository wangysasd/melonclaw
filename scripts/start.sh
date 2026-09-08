#!/usr/bin/env bash
# 一键启动 MelonClaw 本地开发环境：后端 FastAPI + 前端 Vite。
# 用法: scripts/start.sh
# 可用环境变量: MELONCLAW_HOST / MELONCLAW_PORT / MELONCLAW_FRONTEND_PORT
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${TMPDIR:-/tmp}/melonclaw-dev"
mkdir -p "$RUN_DIR"

BACKEND_HOST="${MELONCLAW_HOST:-127.0.0.1}"
BACKEND_PORT="${MELONCLAW_PORT:-8000}"
FRONTEND_PORT="${MELONCLAW_FRONTEND_PORT:-8001}"

port_busy() {
  lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1
}

start_backend() {
  if port_busy "$BACKEND_PORT"; then
    echo "后端已在运行（端口 ${BACKEND_PORT}），跳过启动。"
    return 0
  fi
  echo "启动后端: http://$BACKEND_HOST:$BACKEND_PORT ..."
  (
    cd "$ROOT" &&
      nohup uv run melonclaw-web \
        >"$RUN_DIR/backend.log" 2>&1 &
    echo $! >"$RUN_DIR/backend.pid"
  )
}

start_frontend() {
  if port_busy "$FRONTEND_PORT"; then
    echo "前端已在运行（端口 ${FRONTEND_PORT}），跳过启动。"
    return 0
  fi
  echo "启动前端: http://localhost:$FRONTEND_PORT ..."
  (
    cd "$ROOT/frontend" &&
      nohup npm run dev -- --port "$FRONTEND_PORT" --strictPort \
        >"$RUN_DIR/frontend.log" 2>&1 &
    echo $! >"$RUN_DIR/frontend.pid"
  )
}

wait_for() { # url 名称 超时秒数
  local url="$1" name="$2" timeout="${3:-30}" i=0
  until curl -sf -o /dev/null "$url"; do
    i=$((i + 1))
    if [ "$i" -ge "$timeout" ]; then
      echo "等待 $name 就绪超时（${timeout}s）。"
      return 1
    fi
    sleep 1
  done
  return 0
}

start_backend
start_frontend

backend_ok=0
frontend_ok=0
wait_for "http://$BACKEND_HOST:$BACKEND_PORT/api/status" "后端" 30 && backend_ok=1
wait_for "http://localhost:$FRONTEND_PORT" "前端" 30 && frontend_ok=1

echo
if [ "$backend_ok" -eq 1 ] && [ "$frontend_ok" -eq 1 ]; then
  echo "MelonClaw 开发环境已就绪："
  echo "  前端: http://localhost:$FRONTEND_PORT"
  echo "  后端 API: http://$BACKEND_HOST:$BACKEND_PORT"
else
  echo "部分服务未就绪，请查看日志: $RUN_DIR/{backend,frontend}.log"
  exit 1
fi
echo "日志目录: $RUN_DIR"
echo "停止环境: scripts/shutdown.sh"
