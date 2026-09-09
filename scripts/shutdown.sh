#!/usr/bin/env bash
# 一键停止 MelonClaw 本地开发环境：后端 FastAPI + 前端 Vite。
# 用法: scripts/shutdown.sh
# 可用环境变量: MELONCLAW_PORT / MELONCLAW_FRONTEND_PORT
# 只停止由 start.sh 记录的 PID 以及命令行匹配 MelonClaw/vite 的进程，
# 不会碰端口相同但无关的服务。
set -uo pipefail

if [ "$#" -gt 1 ]; then
  echo "用法: $0 [frontend]" >&2
  exit 2
fi

case "${1:-all}" in
  all)
    FRONTEND_ONLY=0
    ;;
  frontend|--frontend)
    FRONTEND_ONLY=1
    ;;
  -h|--help)
    echo "用法: $0 [frontend]"
    echo "  无参数    关闭前后端"
    echo "  frontend  只关闭前端"
    exit 0
    ;;
  *)
    echo "未知参数: $1。用法: $0 [frontend]" >&2
    exit 2
    ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${TMPDIR:-/tmp}/melonclaw-dev"

BACKEND_PORT="${MELONCLAW_PORT:-8000}"
FRONTEND_PORT="${MELONCLAW_FRONTEND_PORT:-8001}"

stop_pidfile() { # pidfile 名称
  local pidfile="$1" name="$2" pid
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "停止 ${name}（PID ${pid}）..."
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

kill_port() { # 端口 名称 进程匹配串 信号
  local port="$1" name="$2" pattern="$3" signal="${4:-TERM}" pid cmd pids=""
  pids="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true)"
  for pid in $pids; do
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [ -n "$cmd" ] && printf '%s' "$cmd" | grep -qi "$pattern"; then
      echo "停止 ${name}（PID ${pid}，端口 ${port}）..."
      kill -"$signal" "$pid" 2>/dev/null || true
    fi
  done
}

port_free() {
  ! lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1
}

# 先按 PID 文件优雅停止，再按端口兜底（uv/npm 的子进程由端口监听者兜住）。
if [ "$FRONTEND_ONLY" -eq 0 ]; then
  stop_pidfile "$RUN_DIR/backend.pid" "后端"
fi
stop_pidfile "$RUN_DIR/frontend.pid" "前端"
if [ "$FRONTEND_ONLY" -eq 0 ]; then
  kill_port "$BACKEND_PORT" "后端" "melonclaw"
fi
kill_port "$FRONTEND_PORT" "前端" "vite"

# 等待端口释放；超过 5s 仍有残留则强制结束。
for i in 1 2 3 4 5; do
  ports_free=1
  if [ "$FRONTEND_ONLY" -eq 0 ] && ! port_free "$BACKEND_PORT"; then
    ports_free=0
  fi
  if ! port_free "$FRONTEND_PORT"; then
    ports_free=0
  fi
  if [ "$ports_free" -eq 1 ]; then
    break
  fi
  sleep 1
done

if [ "$FRONTEND_ONLY" -eq 0 ] && ! port_free "$BACKEND_PORT"; then
  kill_port "$BACKEND_PORT" "后端" "melonclaw" KILL
fi
if ! port_free "$FRONTEND_PORT"; then
  kill_port "$FRONTEND_PORT" "前端" "vite" KILL
fi

echo
if [ "$FRONTEND_ONLY" -eq 1 ]; then
  if port_free "$FRONTEND_PORT"; then
    echo "MelonClaw 前端开发环境已停止（前端 ${FRONTEND_PORT} 端口已释放）。"
    echo "日志保留在: $RUN_DIR"
  else
    echo "仍有进程占用前端端口，请手动检查: lsof -i tcp:${FRONTEND_PORT}"
    exit 1
  fi
elif port_free "$BACKEND_PORT" && port_free "$FRONTEND_PORT"; then
  echo "MelonClaw 开发环境已停止（后端 ${BACKEND_PORT}、前端 ${FRONTEND_PORT} 端口已释放）。"
  echo "日志保留在: $RUN_DIR"
else
  echo "仍有进程占用端口，请手动检查: lsof -i tcp:${BACKEND_PORT} -i tcp:${FRONTEND_PORT}"
  exit 1
fi
