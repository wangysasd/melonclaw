#!/usr/bin/env bash
#
# MelonClaw 本地检查。覆盖：后端编译、后端测试、后端 lint、前端 lint/类型检查/测试、锁文件一致性、空白错误。
#
# 用法：
#   scripts/check.sh            # 全部（前端依赖未安装时自动跳过前端）
#   scripts/check.sh backend    # 只跑后端
#   scripts/check.sh frontend   # 只跑前端
#
# pytest 与 ruff 在 pyproject.toml 的 dev 依赖分组中声明，因此可以安全使用 `uv run`。
# 不要写成裸 `pytest` / `ruff`：PATH 上可能存在其它项目的同名工具。

set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

scope="${1:-all}"
failures=0
log="$(mktemp -t melonclaw-check.XXXXXX)"
trap 'rm -f "$log"' EXIT

heading() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
pass()    { printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail()    { printf '  \033[31mFAIL\033[0m %s\n' "$1"; failures=$((failures + 1)); }
skip()    { printf '  \033[33mskip\033[0m %s\n' "$1"; }

run() {
  local desc="$1"
  shift
  if "$@" >"$log" 2>&1; then
    pass "$desc"
  else
    fail "$desc"
    tail -n 30 "$log" | sed 's/^/       /'
  fi
}

run_backend() {
  heading "后端"
  run "compileall（src 可编译）" uv run python -m compileall -q src
  run "pytest（含架构依赖与文档链接校验）" uv run pytest -q tests
  run "ruff（src 与 tests）" uv run ruff check src tests
  if [ -f uv.lock ]; then
    run "uv lock --check（锁文件与 pyproject 一致）" uv lock --check
  fi
}

run_frontend() {
  heading "前端"
  if [ ! -d frontend/node_modules ]; then
    skip "前端检查：frontend/node_modules 不存在，请先 npm install"
    return
  fi
  run "eslint" npm --prefix frontend run lint
  run "tsc --noEmit" npm --prefix frontend run typecheck
  run "vitest" npm --prefix frontend test
}

heading "工作区"
run "git diff --check（无行尾空白错误）" git diff --check

case "$scope" in
  backend)  run_backend ;;
  frontend) run_frontend ;;
  all)
    run_backend
    run_frontend
    ;;
  *)
    printf '未知参数：%s（可用：backend / frontend / all）\n' "$scope" >&2
    exit 2
    ;;
esac

printf '\n'
if [ "$failures" -eq 0 ]; then
  printf '\033[32m全部检查通过\033[0m\n'
else
  printf '\033[31m%d 项检查未通过\033[0m\n' "$failures"
fi
exit $(( failures > 0 ? 1 : 0 ))
