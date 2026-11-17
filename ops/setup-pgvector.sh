#!/usr/bin/env bash
# 知识库 pgvector 准备：默认只读体检；只有显式 --apply 才做幂等变更。
#
# 脚本本身的用法看 --help。首次安装与生产升级的完整流程见 ops/PGVECTOR.md。
#
# 给 AI / 运维的调用约定：
#   1. 体检：bash ops/setup-pgvector.sh --check
#      只读，不改任何东西。
#   2. 变更：bash ops/setup-pgvector.sh --apply [--install] [--admin-user <角色>]
#      目标库名从应用配置自动解析，不需要人另行提供或回填。
#   3. 本机 macOS/Homebrew 加 --install 可由脚本安装 pgvector。
#      Linux 服务器由管理员按 --check 打印的命令安装；脚本不静默 sudo 编译。
#   4. 认证由 libpq 标准方式提供（本机 peer、.pgpass、PGPASSWORD 或 PG* 变量）。
#      脚本不接收密码参数，不输出密码、连接串或配置文件内容。
#   5. 脚本只做一件数据库变更：在现有目标库执行
#      CREATE EXTENSION IF NOT EXISTS vector。不建库、不删库、不改已有库数据，
#      不执行 melonclaw-db-init / melonclaw-db-update，不备份、不停服。
#   6. 生产上线前仍需人决定停写窗口与备份，见 ops/PGVECTOR.md。

set -Eeuo pipefail
trap 'echo "脚本执行失败；未删除任何数据，已完成的幂等变更可安全重跑。" >&2' ERR

PGVECTOR_VERSION="0.8.7"
MODE="check"
DO_INSTALL=0
DB_NAME=""
ADMIN_USER=""

usage() {
  cat <<'EOF'
用法：
  bash ops/setup-pgvector.sh --check
  bash ops/setup-pgvector.sh --apply [--install] [--admin-user <角色>] [--db <库名>]
  bash ops/setup-pgvector.sh --help

  --check        默认。只读体检：PG 版本、pgvector 扩展文件、目标库连接、
                 扩展版本、当前角色权限、vector(1024) 是否可用。
  --apply        幂等变更：在当前应用库执行 CREATE EXTENSION IF NOT EXISTS vector，并复验。
  --install      --apply 时若缺 pgvector，在 macOS/Homebrew 执行 brew install pgvector。
                 Linux 只打印管理员应执行的命令，不自动 sudo。
  --db           显式指定目标库。默认从应用配置自动解析，通常不需要给。
  --admin-user   执行 CREATE EXTENSION 的数据库角色。默认用当前 libpq 连接角色。

目标库名解析顺序：--db > 环境变量 DATABASE_URL >
  MELONCLAW_ENV_FILE 指向的配置文件 > 项目根 .env。
只提取库名，不输出连接串、密码或文件其它内容。

首次安装与生产升级的完整流程见 ops/PGVECTOR.md。
脚本不输出凭据，不删库、不建库、不改已有库数据，不执行数据库初始化或升级。
EOF
}

die() {
  echo "错误：$1" >&2
  echo "未删除或改写任何数据。" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h) usage; exit 0 ;;
      --check) MODE="check" ;;
      --apply) MODE="apply" ;;
      --install) DO_INSTALL=1 ;;
      --db)
        [[ $# -ge 2 ]] || die "--db 后必须给出库名。"
        DB_NAME="$2"; shift
        ;;
      --admin-user)
        [[ $# -ge 2 ]] || die "--admin-user 后必须给出角色名。"
        ADMIN_USER="$2"; shift
        ;;
      *) usage >&2; die "未知参数：$1" ;;
    esac
    shift
  done
  case "$DB_NAME" in
    postgres|template0|template1)
      die "不能把 $DB_NAME 作为目标库；请在配置里指向应用数据库。"
      ;;
  esac
}

# 只从配置里提取库名。不打印 URL、密码或文件其它行。
resolve_db_name() {
  local env_file project_root
  project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  env_file="${MELONCLAW_ENV_FILE:-}"
  if [[ -z "$env_file" && -r "$project_root/.env" ]]; then
    env_file="$project_root/.env"
  fi
  python3 - "$env_file" <<'PY'
import os
import sys
import urllib.parse

env_file = sys.argv[1]
url = os.environ.get("DATABASE_URL", "").strip()
if not url and env_file:
    try:
        with open(env_file, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'").strip()
                    break
    except OSError:
        pass
url = url.strip()
if not url:
    sys.exit(1)
name = urllib.parse.urlsplit(url).path.lstrip("/").strip()
if not name or name in ("postgres", "template0", "template1"):
    sys.exit(1)
if any(ch.isspace() for ch in name):
    sys.exit(1)
print(name)
PY
}

# 非交互环境（AI 调用）禁用密码提示，避免脚本卡住；交互终端保留提示。
psql_opts=()
if [[ ! -t 0 ]]; then
  psql_opts=(-w)
fi

run_psql() {
  local db="$1"; shift
  if [[ -n "$ADMIN_USER" ]]; then
    psql -X "${psql_opts[@]}" -v ON_ERROR_STOP=1 -U "$ADMIN_USER" -d "$db" "$@"
  else
    psql -X "${psql_opts[@]}" -v ON_ERROR_STOP=1 -d "$db" "$@"
  fi
}

pg_major() {
  pg_config --version | awk '{print $2}' | cut -d. -f1
}

extension_files_present() {
  local pkglib sharedir
  pkglib="$(pg_config --pkglibdir)"
  sharedir="$(pg_config --sharedir)"
  ls "$pkglib"/vector.* >/dev/null 2>&1 || return 1
  [[ -f "$sharedir/extension/vector.control" ]] || return 1
  return 0
}

print_install_hint() {
  local major
  major="$(pg_major)"
  echo "pgvector 扩展文件未就位。按平台安装（需要时输入管理员密码）："
  echo ""
  case "$(uname -s)" in
    Darwin)
      echo "  brew install pgvector"
      echo ""
      echo "  本机可在 --apply 时加 --install，由脚本执行上面这条命令；--check 始终只读。"
      ;;
    Linux)
      echo "  sudo apt-get install -y postgresql-${major}-pgvector"
      echo ""
      echo "  若发行版没有该包，需先装 postgresql-server-dev-${major}，再源码编译："
      echo "    git clone --branch v${PGVECTOR_VERSION} https://github.com/pgvector/pgvector.git /tmp/pgvector"
      echo "    cd /tmp/pgvector"
      echo "    make PG_CONFIG=\"\$(command -v pg_config)\""
      echo "    sudo make install PG_CONFIG=\"\$(command -v pg_config)\""
      ;;
    *)
      echo "  请按当前系统的 PostgreSQL ${major} 安装 pgvector ${PGVECTOR_VERSION}。"
      ;;
  esac
  echo ""
  echo "装完重新运行 --check 确认扩展文件落在当前 pg_config 指向的目录。"
}

install_if_requested() {
  [[ "$DO_INSTALL" -eq 1 ]] || return 1
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Linux 不自动安装；请按上面的命令由管理员执行。" >&2
    return 1
  fi
  command -v brew >/dev/null || die "未找到 brew。"
  echo "执行 brew install pgvector ..."
  brew install pgvector
}

preflight() {
  local command
  for command in psql pg_config python3; do
    command -v "$command" >/dev/null || die "缺少命令：$command。"
  done
  echo "== PostgreSQL 环境 =="
  echo "  psql:      $(psql --version)"
  echo "  pg_config: $(pg_config --version)"
  echo "  pkglibdir: $(pg_config --pkglibdir)"
  echo "  sharedir:  $(pg_config --sharedir)"
  echo "  PG 主版本: $(pg_major)"
  echo ""
  if extension_files_present; then
    echo "  pgvector 扩展文件: 已就位"
    return 0
  fi
  echo "  pgvector 扩展文件: 未找到"
  return 1
}

check_database() {
  local db="$1" ext_version
  echo "== 目标库：$db =="
  if ! run_psql "$db" -Atc "SELECT 1" >/dev/null 2>&1; then
    echo "  无法连接目标库。确认库已创建、服务已启动。"
    echo "  跨主机请设置 PGHOST/PGPORT/PGUSER，认证用 .pgpass 或 PGPASSWORD。"
    return 1
  fi
  echo "  连接: 正常"
  echo "  当前角色/超级用户: $(run_psql "$db" -Atc "SELECT current_user || '/' || rolsuper::text FROM pg_roles WHERE rolname = current_user" 2>/dev/null)"
  ext_version="$(run_psql "$db" -Atc "SELECT extversion FROM pg_extension WHERE extname = 'vector'" 2>/dev/null || true)"
  if [[ -n "$ext_version" ]]; then
    echo "  vector 扩展: 已启用，版本 $ext_version"
  else
    echo "  vector 扩展: 未启用（--apply 会执行 CREATE EXTENSION IF NOT EXISTS）"
  fi
  if run_psql "$db" -Atc "SELECT '[1,2,3]'::vector::text" >/dev/null 2>&1 \
     && run_psql "$db" -c "CREATE TEMP TABLE _kb_probe (id int, embedding vector(1024))" >/dev/null 2>&1; then
    echo "  vector 类型与 vector(1024): 可用"
  else
    echo "  vector 类型与 vector(1024): 不可用（扩展文件可能未装，或扩展未启用）"
  fi
  return 0
}

apply_changes() {
  local db="$1"
  if ! run_psql "$db" -Atc "SELECT 1" >/dev/null 2>&1; then
    die "无法连接目标库 $db。确认库已创建、服务已启动；跨主机请设置 PGHOST/PGPORT/PGUSER 并提供认证。"
  fi
  echo "目标库 $db 已存在，直接在该库启用扩展；不建库、不改现有数据。"
  if ! run_psql "$db" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null; then
    die "CREATE EXTENSION 失败：当前角色可能没有权限。请让管理员执行，或用 --admin-user 指定管理员角色。"
  fi
  echo "复验 ..."
  run_psql "$db" -Atc "SELECT 'vector 版本: ' || extversion FROM pg_extension WHERE extname = 'vector'"
  run_psql "$db" -Atc "SELECT 'vector 类型: ' || '[1,2,3]'::vector::text"
  run_psql "$db" -c "CREATE TEMP TABLE _kb_probe (id int, embedding vector(1024))" >/dev/null
  echo "  vector(1024): 可用"
  echo ""
  echo "完成。目标库：$db（从应用配置解析，无需回填）。"
  echo "脚本不执行建表或数据库升级；业务表由应用的可审计升级步骤创建。"
}

main() {
  parse_args "$@"
  if [[ -z "$DB_NAME" ]]; then
    DB_NAME="$(resolve_db_name || true)"
  fi
  local files_ok=0
  if preflight; then files_ok=1; fi

  if [[ "$files_ok" -eq 0 ]]; then
    echo ""
    if [[ "$MODE" == "apply" ]]; then
      if ! install_if_requested; then
        print_install_hint
        die "扩展文件未就位，未做任何变更。"
      fi
      if ! extension_files_present; then
        print_install_hint
        die "安装后仍未找到扩展文件（可能装到了另一个 PG 版本目录），未做数据库变更。"
      fi
    else
      print_install_hint
    fi
  fi

  echo ""
  if [[ "$MODE" == "check" ]]; then
    if [[ -n "$DB_NAME" ]]; then
      check_database "$DB_NAME" || true
    else
      echo "未能从应用配置解析目标库名，跳过目标库检查；可用 --db 显式指定。"
    fi
    echo ""
    echo "RESULT: 体检完成，未做任何变更。启用扩展请显式运行 --apply。"
    return 0
  fi

  [[ -n "$DB_NAME" ]] || die "无法解析目标库名；请显式指定 --db，或检查 MELONCLAW_ENV_FILE / 项目根 .env。"
  apply_changes "$DB_NAME"
}

main "$@"
