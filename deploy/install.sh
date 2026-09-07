#!/usr/bin/env bash
# MelonClaw Ubuntu 24.04 部署脚本（需 root 执行）。
#
# 前置条件：项目源码已放置到 /opt/melonclaw（或设置 GIT_REPO 由脚本拉取），
# 且 .env 已填写完成；脚本不会写入任何密钥，只负责环境、依赖、托管和反向代理。
#
# 可选环境变量：
#   APP_DIR=/opt/melonclaw              应用目录
#   GIT_REPO=<git url>                  非空时先 clone/拉取源码
#   GIT_REF=main                        clone 分支
#   RUN_DB_INIT=1                       是否执行 melonclaw-db-init
#   BASIC_AUTH_USER / BASIC_AUTH_PASSWORD  设置后为 Nginx 开启 HTTP Basic 认证
#   SKIP_START=1                        只安装不启动服务

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/melonclaw}"
APP_USER="${APP_USER:-melonclaw}"
APP_HOME="${APP_HOME:-/home/${APP_USER}}"
GIT_REPO="${GIT_REPO:-}"
GIT_REF="${GIT_REF:-main}"
RUN_DB_INIT="${RUN_DB_INIT:-1}"
SKIP_START="${SKIP_START:-0}"
UV_INSTALL_DIR="${UV_INSTALL_DIR:-/usr/local/bin}"

export DEBIAN_FRONTEND=noninteractive

log() { printf '\n==> %s\n' "$*"; }
warn() { printf '\n[!] %s\n' "$*" >&2; }

if [ "$(id -u)" -ne 0 ]; then
  echo "请使用 root 执行：sudo bash $0" >&2
  exit 1
fi

log "1/9 安装系统依赖"
apt-get update -y
apt-get install -y curl git nginx python3 python3-venv python3-dev build-essential ca-certificates

log "2/9 准备运行用户 ${APP_USER}"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${APP_HOME}" --create-home --shell /bin/bash "${APP_USER}"
fi
mkdir -p "${APP_HOME}/.melonclaw/workspaces"

log "3/9 准备源码目录 ${APP_DIR}"
if [ -n "${GIT_REPO}" ]; then
  if [ -d "${APP_DIR}/.git" ]; then
    git -C "${APP_DIR}" fetch --all --prune
    git -C "${APP_DIR}" checkout "${GIT_REF}"
    git -C "${APP_DIR}" pull --ff-only origin "${GIT_REF}" || warn "拉取失败，保留现有代码"
  else
    git clone --branch "${GIT_REF}" "${GIT_REPO}" "${APP_DIR}"
  fi
fi

if [ ! -f "${APP_DIR}/pyproject.toml" ]; then
  echo "未在 ${APP_DIR} 找到 pyproject.toml，请先上传源码或设置 GIT_REPO。" >&2
  exit 1
fi

log "4/9 安装 uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="${UV_INSTALL_DIR}" sh
fi
uv --version

log "5/9 同步 Python 依赖"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
runuser -u "${APP_USER}" -- env HOME="${APP_HOME}" bash -lc \
  "cd '${APP_DIR}' && uv sync --locked --python /usr/bin/python3"

log "6/9 检查 .env"
if [ ! -f "${APP_DIR}/.env" ]; then
  if [ -f "${APP_DIR}/.env.example" ]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    warn "已生成 ${APP_DIR}/.env，请填写后重新执行本脚本（DATABASE_URL、DEEPSEEK_API_KEY、DEEPSEEK_MODEL 等）。"
  else
    echo "缺少 ${APP_DIR}/.env，且没有 .env.example 可复制。" >&2
    exit 1
  fi
fi
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
chmod 600 "${APP_DIR}/.env"

if ! grep -q '^DATABASE_URL=.\+' "${APP_DIR}/.env"; then
  warn "DATABASE_URL 仍为空，跳过数据库初始化；填写后执行：bash ${APP_DIR}/deploy/db-init.sh"
  RUN_DB_INIT=0
fi

log "7/9 安装 systemd 单元"
cp "${APP_DIR}/deploy/melonclaw-web.service" /etc/systemd/system/melonclaw-web.service
systemctl daemon-reload

if [ "${RUN_DB_INIT}" = "1" ]; then
  log "8/9 初始化数据库表"
  runuser -u "${APP_USER}" -- env HOME="${APP_HOME}" bash -lc \
    "cd '${APP_DIR}' && uv run melonclaw-db-init"
else
  log "8/9 跳过数据库初始化"
fi

log "9/9 配置并重载 Nginx"
cp "${APP_DIR}/deploy/nginx-melonclaw.conf" /etc/nginx/sites-available/melonclaw
ln -sf /etc/nginx/sites-available/melonclaw /etc/nginx/sites-enabled/melonclaw
rm -f /etc/nginx/sites-enabled/default

if [ -n "${BASIC_AUTH_USER:-}" ] && [ -n "${BASIC_AUTH_PASSWORD:-}" ]; then
  apt-get install -y apache2-utils
  printf '%s' "${BASIC_AUTH_PASSWORD}" | htpasswd -bc -i /etc/nginx/.melonclaw_htpasswd "${BASIC_AUTH_USER}"
  chown root:www-data /etc/nginx/.melonclaw_htpasswd
  chmod 640 /etc/nginx/.melonclaw_htpasswd
  sed -i 's|^    #auth_basic|    auth_basic|' /etc/nginx/sites-available/melonclaw
  echo "已启用 Nginx Basic 认证，用户：${BASIC_AUTH_USER}"
fi

nginx -t
systemctl enable nginx
systemctl reload nginx

if [ "${SKIP_START}" != "1" ]; then
  systemctl enable melonclaw-web
  systemctl restart melonclaw-web
  sleep 3
  systemctl --no-pager status melonclaw-web || true
fi

log "完成"
echo "访问地址：http://<服务器公网IP>/"
echo "查看日志：journalctl -u melonclaw-web -f"
