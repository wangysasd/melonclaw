#!/usr/bin/env bash
# 一键启动 MelonClaw 本地开发环境：后端 FastAPI + 前端 Vite。
# 用法: scripts/start.sh
# 可用环境变量: MELONCLAW_HOST / MELONCLAW_PORT / MELONCLAW_FRONTEND_HOST /
# MELONCLAW_FRONTEND_PORT / UV_CACHE_DIR
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${TMPDIR:-/tmp}/melonclaw-dev"
mkdir -p "$RUN_DIR"

BACKEND_HOST="${MELONCLAW_HOST:-127.0.0.1}"
BACKEND_PORT="${MELONCLAW_PORT:-8000}"
FRONTEND_HOST="${MELONCLAW_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${MELONCLAW_FRONTEND_PORT:-8001}"
UV_CACHE_DIR="${UV_CACHE_DIR:-$RUN_DIR/uv-cache}"
mkdir -p "$UV_CACHE_DIR"

port_busy() {
  lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1
}

check_ports_free() {
  local occupied=0
  if port_busy "$BACKEND_PORT"; then
    echo "启动失败：后端端口 ${BACKEND_PORT} 已被占用。请先执行 scripts/shutdown.sh 关闭前后端服务。"
    occupied=1
  fi
  if port_busy "$FRONTEND_PORT"; then
    echo "启动失败：前端端口 ${FRONTEND_PORT} 已被占用。请先执行 scripts/shutdown.sh 关闭前后端服务。"
    occupied=1
  fi
  if [ "$occupied" -ne 0 ]; then
    return 1
  fi
}

start_backend() {
  echo "启动后端: http://$BACKEND_HOST:$BACKEND_PORT ..."
  (
    cd "$ROOT" &&
      nohup env UV_CACHE_DIR="$UV_CACHE_DIR" uv run melonclaw-web \
        >"$RUN_DIR/backend.log" 2>&1 &
    echo $! >"$RUN_DIR/backend.pid"
  )
}

start_frontend() {
  echo "启动前端: http://$FRONTEND_HOST:$FRONTEND_PORT ..."
  (
    cd "$ROOT/frontend" &&
      nohup npm run dev -- \
        --host "$FRONTEND_HOST" \
        --port "$FRONTEND_PORT" \
        --strictPort \
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

wait_for_backend() { # url 名称 超时秒数
  local url="$1" name="$2" timeout="${3:-30}" i=0
  until curl -sf "$url" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'; do
    i=$((i + 1))
    if [ "$i" -ge "$timeout" ]; then
      echo "等待 $name 就绪超时（${timeout}s）。"
      return 1
    fi
    sleep 1
  done
  return 0
}

show_backend_status() {
  local url="http://$BACKEND_HOST:$BACKEND_PORT/api/status"
  echo "后端当前状态："
  curl -sS --max-time 3 "$url" || echo "无法读取后端状态。"
  echo
}

check_ports_free
start_backend
start_frontend

backend_ok=0
frontend_ok=0
wait_for_backend "http://$BACKEND_HOST:$BACKEND_PORT/api/status" "后端" 30 && backend_ok=1
wait_for "http://$FRONTEND_HOST:$FRONTEND_PORT" "前端" 30 && frontend_ok=1

echo
if [ "$backend_ok" -eq 1 ] && [ "$frontend_ok" -eq 1 ]; then
  echo "MelonClaw 开发环境已就绪："
  echo "  前端: http://$FRONTEND_HOST:$FRONTEND_PORT"
  echo "  后端 API: http://$BACKEND_HOST:$BACKEND_PORT"
else
  echo "部分服务未就绪，请查看日志: $RUN_DIR/{backend,frontend}.log"
  if [ "$backend_ok" -eq 0 ]; then
    show_backend_status
  fi
  exit 1
fi
echo "日志目录: $RUN_DIR"
echo "停止环境: scripts/shutdown.sh"
