#!/usr/bin/env bash
# 常规发版：不升级数据库，不修改生产配置，不覆盖 Nginx 站点。

set -Eeuo pipefail

main() {
  local project_root service_touched=0
  project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  cd "$project_root"

  if [[ "${1:-}" == "--help" ]]; then
    echo "用法：bash ops/deploy.sh"
    echo "先停后端，再拉代码、安装依赖、构建前端、启动后端并检查就绪。"
    echo "仅用于无需数据库升级的版本。以 ubuntu 执行，不要 sudo 整个脚本。"
    echo "可设置 MELONCLAW_READY_URL（默认 http://127.0.0.1:8000/api/ready）。"
    return
  fi
  if [[ $# -ne 0 ]]; then
    echo "未知参数；使用 --help 查看用法。" >&2
    return 2
  fi
  if [[ $EUID -eq 0 ]]; then
    echo "请以 ubuntu 运行脚本，不要 sudo 整个脚本。" >&2
    return 1
  fi
  for command in git uv npm sudo systemctl curl python3 flock; do
    command -v "$command" >/dev/null || {
      echo "缺少命令：$command；服务未停止。" >&2
      return 1
    }
  done
  if [[ ! -r /etc/melonclaw/melonclaw.env ]]; then
    echo "无法读取 /etc/melonclaw/melonclaw.env；服务未停止。" >&2
    return 1
  fi
  local lock_directory
  lock_directory="$(git rev-parse --git-dir)"
  exec 9>"$lock_directory/melonclaw-deploy.lock"
  if ! flock -n 9; then
    echo "已有发版脚本运行；服务未停止。" >&2
    return 1
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "代码目录有本地修改或未跟踪文件；请先处理，服务未停止。" >&2
    return 1
  fi
  sudo -v

  # main 的函数体在 git pull 前已完整载入，避免脚本更新影响当前执行。
  trap 'if [[ $service_touched -eq 1 ]]; then
          sudo systemctl stop melonclaw || true
          echo "发版失败，后端保持停止。修复后重新执行；未自动回滚代码或前端。" >&2
        fi' ERR

  echo "[1/6] 停止后端"
  service_touched=1
  sudo systemctl stop melonclaw

  echo "[2/6] 拉取代码"
  git pull --ff-only

  echo "[3/6] 同步后端依赖"
  uv sync --locked --default-index https://pypi.org/simple

  echo "[4/6] 安装前端依赖"
  npm --prefix frontend ci

  echo "[5/6] 构建前端"
  npm --prefix frontend run build

  echo "[6/6] 启动后端并检查就绪"
  sudo systemctl start melonclaw
  local ready_url="${MELONCLAW_READY_URL:-http://127.0.0.1:8000/api/ready}"
  local attempt
  for attempt in {1..30}; do
    if systemctl is-active --quiet melonclaw &&
       curl -fsS --connect-timeout 2 --max-time 3 "$ready_url" 2>/dev/null |
         python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("ready") is True else 1)' 2>/dev/null; then
      trap - ERR
      echo "发版成功：后端已就绪。请刷新网页并验证聊天及本次修改的功能。"
      return 0
    fi
    sleep 1
  done
  echo "后端未在检查期限内就绪。请检查 /var/log/melonclaw/backend.log。" >&2
  false
}

main "$@"
