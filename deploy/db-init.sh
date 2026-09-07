#!/usr/bin/env bash
# 单独执行数据库初始化或结构升级（需 root 执行）。
# 业务表、LangGraph Checkpointer 和 Memory Store 表都由 melonclaw-db-init 幂等创建。

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/melonclaw}"
APP_USER="${APP_USER:-melonclaw}"
APP_HOME="${APP_HOME:-/home/${APP_USER}}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 执行：sudo bash $0" >&2
  exit 1
fi

runuser -u "${APP_USER}" -- env HOME="${APP_HOME}" bash -lc \
  "cd '${APP_DIR}' && uv run melonclaw-db-init"
