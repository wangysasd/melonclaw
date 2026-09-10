#!/usr/bin/env python3
"""Tushare 取数脚本模板（改下面 4 个常量即可）。

用法:
    export TUSHARE_MCP_TOKEN=<your_token>
    python3 fetch_script.py

token 只从环境变量读取，禁止写死在本文件里。
"""

from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------- #
# 0. 定位 tsfetch.py（本 skill 自带）。
#    把 SKILL_SCRIPTS_OVERRIDE 填成实际的 <skill>/scripts 路径最省事；
#    也可以不填，用环境变量 TUSHARE_FETCHER_SCRIPTS，或直接在 skill 目录内原地运行。
# --------------------------------------------------------------------------- #
SKILL_SCRIPTS_OVERRIDE = ""      # 例: "/Users/me/.agents/skills/tushare-fetcher/scripts"


def _find_scripts() -> str:
    override = SKILL_SCRIPTS_OVERRIDE.strip() or os.environ.get("TUSHARE_FETCHER_SCRIPTS")
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        override,
        os.path.join(os.path.dirname(here), "scripts"),          # 脚本放在 skill 根目录下
        os.path.join(here, "scripts"),                           # 脚本放在 skill 根目录的上一级
        os.path.expanduser("~/.agents/skills/tushare-fetcher/scripts"),
        os.path.join(os.getcwd(), "scripts"),
    ]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "tsfetch.py")):
            return c
    sys.exit("找不到 tsfetch.py。把本文件顶部的 SKILL_SCRIPTS_OVERRIDE 填成 "
             "<skill>/scripts 路径，或设置环境变量 TUSHARE_FETCHER_SCRIPTS。已尝试：\n  "
             + "\n  ".join(c for c in candidates if c))


SKILL_SCRIPTS = _find_scripts()
sys.path.insert(0, SKILL_SCRIPTS)

from tsfetch import TushareClient, TushareError, TokenMissingError  # noqa: E402

# --------------------------------------------------------------------------- #
# 1. 改这四处：接口名 / 参数 / 字段 / 输出文件
#    全部来自 `python3 tsdoc.py doc --name <接口名>` 的正文，逐字照抄。
# --------------------------------------------------------------------------- #
API = "daily"
PARAMS = {
    "ts_code": "000001.SZ",
    "start_date": "20240101",
    "end_date": "20240131",
}
FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount"
OUT = "daily_000001SZ_20240101_20240131.csv"   # .csv/.json/.jsonl/.parquet

# 需要翻页时（文档声明支持 offset/limit）：PAGE_SIZE = 5000
PAGE_SIZE = 0          # 0 = 不翻页

# 可选：批量循环时限制调用频率（每分钟次数），None = 不限
RATE_PER_MIN = None
# 可选：True = 请求字段未返回时直接报错（默认仅告警）
STRICT_FIELDS = False


def main() -> int:
    cli = TushareClient(rate_per_min=RATE_PER_MIN)   # token 自动取 TUSHARE_MCP_TOKEN

    try:
        if PAGE_SIZE:
            rows = cli.paginate(API, fields=FIELDS, params=PARAMS,
                                page_size=PAGE_SIZE, max_pages=20)
        else:
            rows = cli.query(API, fields=FIELDS, params=PARAMS,
                             strict_fields=STRICT_FIELDS)
    except TokenMissingError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return 4
    except TushareError as e:
        print(f"[接口错误] code={e.code} msg={e.msg} detail={e.detail}", file=sys.stderr)
        if e.code == 40101:
            print("  → 检查环境变量 TUSHARE_MCP_TOKEN 是否设置且有效", file=sys.stderr)
        return 1

    # ----------------------------------------------------------------- 校验
    if not rows:
        print(f"[校验失败] {API} 返回 0 行：先检查参数（日期格式 YYYYMMDD、代码后缀、"
              f"必选参数是否遗漏），再确认接口权限。未写文件。", file=sys.stderr)
        return 5

    cols = list(rows[0].keys())
    print(f"[取数成功] {API}: {len(rows)} 行 × {len(cols)} 列")
    print(f"[字段] {cols}")

    for date_key in ("trade_date", "date", "end_date", "ann_date", "cal_date"):
        if date_key in cols:
            vals = sorted(str(r[date_key]) for r in rows if r.get(date_key) is not None)
            if vals:
                print(f"[区间] {date_key}: {vals[0]} ~ {vals[-1]}")
            break

    missing = [c for c in cols if all(r.get(c) in (None, "") for r in rows)]
    if missing:
        print(f"[警告] 整列为空: {missing}（字段名写错？该参数下无数据？）", file=sys.stderr)

    # ----------------------------------------------------------------- 落盘
    path = cli.save(rows, OUT)
    print(f"[已落盘] {os.path.abspath(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
