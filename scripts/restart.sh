#!/usr/bin/env bash
# 按当前状态启动或重启 MelonClaw 本地开发环境：后端 FastAPI + 前端 Vite。
# 用法: scripts/restart.sh [frontend] [--profile dev|prod]
# 可用环境变量: MELONCLAW_PORT / MELONCLAW_FRONTEND_PORT
set -euo pipefail

TARGET=all
PROFILE=dev
while [ "$#" -gt 0 ]; do
  case "$1" in
    all)
      TARGET=all
      shift
      ;;
    frontend|--frontend)
      TARGET=frontend
      shift
      ;;
    --profile)
      if [ "$#" -lt 2 ]; then
        echo "参数 --profile 需要 dev 或 prod。" >&2
        exit 2
      fi
      PROFILE="$2"
      shift 2
      ;;
    --profile=*)
      PROFILE="${1#*=}"
      shift
      ;;
    -h|--help)
      echo "用法: $0 [frontend] [--profile dev|prod]"
      echo "  默认启动或重启前后端，默认 profile 为 dev（读取 .env）"
      echo "  --profile prod 读取 .env.prod"
      echo "  frontend       只启动或重启前端"
      exit 0
      ;;
    *)
      echo "未知参数: $1。用法: $0 [frontend] [--profile dev|prod]" >&2
      exit 2
      ;;
  esac
done

case "$PROFILE" in
  dev|prod) ;;
  *)
    echo "无效 profile：$PROFILE（仅支持 dev 或 prod）。" >&2
    exit 2
    ;;
esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${MELONCLAW_ENV_FILE:-}" ]; then
  ENV_FILE="$MELONCLAW_ENV_FILE"
elif [ "$PROFILE" = "prod" ]; then
  ENV_FILE="$ROOT/.env.prod"
else
  ENV_FILE="$ROOT/.env"
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "重启失败：profile=$PROFILE 对应的环境文件不存在：$ENV_FILE" >&2
  exit 1
fi
BACKEND_PORT="${MELONCLAW_PORT:-8000}"
FRONTEND_PORT="${MELONCLAW_FRONTEND_PORT:-8001}"

port_busy() {
  lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1
}

backend_started=0
frontend_started=0
if [ "$TARGET" = all ]; then
  port_busy "$BACKEND_PORT" && backend_started=1
fi
port_busy "$FRONTEND_PORT" && frontend_started=1

if [ "$TARGET" = frontend ]; then
  if [ "$frontend_started" -eq 0 ]; then
    echo "前端未启动，启动 MelonClaw 前端..."
    exec "$ROOT/scripts/start.sh" frontend --profile "$PROFILE"
  fi

  echo "前端已启动，先停止再重启 MelonClaw 前端..."
  "$ROOT/scripts/shutdown.sh" frontend
  exec "$ROOT/scripts/start.sh" frontend --profile "$PROFILE"
fi

if [ "$backend_started" -eq 0 ] && [ "$frontend_started" -eq 0 ]; then
  echo "前后端均未启动，启动 MelonClaw 开发环境..."
  exec "$ROOT/scripts/start.sh" --profile "$PROFILE"
fi

if [ "$backend_started" -eq 1 ] && [ "$frontend_started" -eq 1 ]; then
  echo "前后端均已启动，先停止再重启 MelonClaw 开发环境..."
else
  echo "检测到前后端状态不一致，先清理残留服务再启动 MelonClaw 开发环境..."
fi

"$ROOT/scripts/shutdown.sh"
exec "$ROOT/scripts/start.sh" --profile "$PROFILE"
