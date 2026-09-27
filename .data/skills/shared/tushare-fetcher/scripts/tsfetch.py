#!/usr/bin/env python3
"""Tushare Pro 数据获取客户端（自包含，纯标准库；Parquet/DataFrame 需 pandas+pyarrow）。

本脚本随 tushare-fetcher skill 一起分发，既可当命令行用，也可当库被取数脚本 import。

Token 解析顺序（**不允许硬编码**）:
  1) 显式传入 token=
  2) 环境变量 TUSHARE_MCP_TOKEN      ← 本 skill 的约定来源
  3) 环境变量 TUSHARE_TOKEN          ← 兼容别名
解析失败会抛 TokenMissingError，并在信息里说明该导出哪个变量。

命令行用法:
  tsfetch.py check
      一条命令自检：token / API 连通 / 文档站 / 缓存目录 / 落盘能力。

  tsfetch.py call --api daily \\
      --params '{"ts_code":"000001.SZ","start_date":"20240101","end_date":"20240131"}' \\
      --fields ts_code,trade_date,open,high,low,close \\
      --out daily_000001.csv

  tsfetch.py call --api daily --params-file params.json --out daily.parquet
  tsfetch.py call --api daily --params '{"trade_date":"20240102"}' --paginate \\
      --page-size 5000 --max-pages 10 --sleep 0.5 --out all.csv
  tsfetch.py call --api daily --params '{"trade_date":"20240102"}' --paginate \\
      --out all.csv --append          # 流式翻页直写，内存占用与数据量无关
  tsfetch.py call --api daily --params '{}' --strict-fields   # 请求字段缺失即报错
  tsfetch.py call --api stock_basic --params '{}' --raw       # 打印原始返回信封

库用法:
  import sys; sys.path.insert(0, "<skill_dir>/scripts")
  from tsfetch import TushareClient, TushareError, FieldMismatchError

  cli = TushareClient()                       # 自动读 TUSHARE_MCP_TOKEN
  rows = cli.query("daily", fields="ts_code,trade_date,close",
                   ts_code="000001.SZ", start_date="20240101", end_date="20240131")
  cli.save(rows, "daily_000001.csv")

  for page, chunk in cli.iter_paginate("daily", params={"trade_date": "20240102"}):
      cli.save(chunk, "all.jsonl", append=(page > 0))   # 流式落盘

  cli = TushareClient(rate_per_min=180)       # 批量循环时自动节流
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

API_URL = "https://api.tushare.pro"
USER_AGENT = "Mozilla/5.0 (tushare-fetcher skill)"
TOKEN_ENV_PRIMARY = "TUSHARE_MCP_TOKEN"
TOKEN_ENV_ALIASES = ("TUSHARE_TOKEN",)


class TokenMissingError(RuntimeError):
    """环境变量里没有 Tushare token。"""


class FieldMismatchError(RuntimeError):
    """请求的字段未出现在返回结果里（strict 模式）。"""

    def __init__(self, api, missing, requested):
        self.api = api
        self.missing = missing
        self.requested = requested
        super().__init__(
            f"接口 {api} 未返回请求的字段 {missing}（请求：{requested}）。"
            "Tushare 会静默丢弃无效字段名，请核对文档 outputs。")


# 常见错误码 -> 处置建议（以 Tushare 返回的 msg 为最终依据）
def _hint_for(code, msg: str | None) -> str | None:
    m = (msg or "").lower()
    if code == 40101:
        if "token" in m:
            return "token 无效或未设置：检查环境变量 TUSHARE_MCP_TOKEN 是否导出且有效"
        return "接口名或参数有误：核对 api_name 拼写（以文档正文的「接口：」行为准）"
    if code == 40203:
        return "积分/权限不足：查看文档 meta 里的积分要求，或改用低门槛接口"
    if code == -2001:
        return "参数错误：核对参数名与取值格式（日期 YYYYMMDD、代码带后缀）"
    if code == 40001:
        return "请求不被接受：确认 api_name 与必选参数"
    return None


class TushareError(RuntimeError):
    """Tushare 返回 code != 0。"""

    def __init__(self, code, msg, detail="", api=None):
        self.code = code
        self.msg = msg
        self.detail = detail
        self.api = api
        self.hint = _hint_for(code, msg)
        text = f"Tushare 接口 {api or '?'} 调用失败: code={code} msg={msg}"
        if detail:
            text += f" detail={detail}"
        if self.hint:
            text += f"\n  提示: {self.hint}"
        super().__init__(text)


def build_ssl_context(insecure: bool = False) -> ssl.SSLContext:
    """优先用 certifi 的 CA 包（部分 Python 环境证书链不完整），否则用系统默认。"""
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def resolve_token(token: str | None = None) -> str:
    """按 显式传入 -> TUSHARE_MCP_TOKEN -> 别名 的顺序取 token。"""
    if token:
        return token.strip()
    for name in (TOKEN_ENV_PRIMARY,) + TOKEN_ENV_ALIASES:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    raise TokenMissingError(
        f"未找到 Tushare token：请先导出环境变量 {TOKEN_ENV_PRIMARY}，例如\n"
        f"  export {TOKEN_ENV_PRIMARY}=<your_token>\n"
        "（也可在构造客户端时显式传 token=，但不要把它写进代码或提交到仓库。）"
    )


def mask_token(token: str) -> str:
    """日志里只显示首尾各 4 位。"""
    if len(token) <= 10:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}(len={len(token)})"


def _norm_fields(fields) -> str:
    if not fields:
        return ""
    if isinstance(fields, str):
        return fields
    return ",".join(fields)


class TushareClient:
    """极简 Tushare Pro 客户端。

    query() 返回 list[dict]（列名 -> 值）；call() 返回原始信封 dict。
    """

    def __init__(self, token: str | None = None, timeout: int = 30, retries: int = 2,
                 insecure: bool = False, sleep: float = 0.4, verbose: bool = False,
                 rate_per_min: float | None = None):
        self.token = resolve_token(token)
        self.timeout = timeout
        self.retries = retries
        self.insecure = insecure
        self.sleep = sleep
        self.verbose = verbose
        self.rate_per_min = rate_per_min
        self._min_interval = (60.0 / rate_per_min) if rate_per_min else 0.0
        self._last_call = 0.0
        self.last_field_mismatch = None
        self._ctx = build_ssl_context(insecure)

    # ---------------------------------------------------------------- 底层
    def call(self, api_name: str, params: dict | None = None, fields=None) -> dict:
        """调一次接口，返回 Tushare 原始信封 {code, msg, data, ...}。"""
        if self._min_interval:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

        payload = json.dumps({
            "api_name": api_name,
            "token": self.token,
            "params": params or {},
            "fields": _norm_fields(fields),
        }, ensure_ascii=False).encode("utf-8")

        last_err = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                API_URL, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    body = resp.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code < 500:
                    raise RuntimeError(f"HTTP {e.code} 调用 {api_name} 失败: {e.reason}") from e
            except (urllib.error.URLError, socket.timeout) as e:
                last_err = e
                if isinstance(e, urllib.error.URLError) and isinstance(e.reason, ssl.SSLError):
                    raise RuntimeError(
                        f"TLS 证书校验失败: {e.reason}\n"
                        "提示：pip install certifi，或临时加 --insecure 重试。"
                    ) from e
            if attempt < self.retries:
                wait = 1.5 ** attempt
                if self.verbose:
                    print(f"[tsfetch] 第 {attempt + 1} 次请求失败（{last_err}），{wait:.1f}s 后重试",
                          file=sys.stderr)
                time.sleep(wait)
        else:
            raise RuntimeError(f"调用 {api_name} 失败（已重试 {self.retries} 次）: {last_err}")

        try:
            envelope = json.loads(body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Tushare 返回非 JSON（前 200 字符）: {body[:200]!r}") from e

        if not isinstance(envelope, dict):
            raise RuntimeError(f"Tushare 返回结构异常: {envelope!r}")

        code = envelope.get("code")
        if code != 0:
            raise TushareError(code, envelope.get("msg"), envelope.get("detail", ""), api_name)
        return envelope

    # ---------------------------------------------------------------- 查询
    def _check_fields(self, api_name: str, fields, returned: list[str],
                      strict: bool = False) -> None:
        """比对请求字段与返回列：Tushare 对无效字段静默丢弃，必须显式发现。"""
        if not fields or not returned:
            return
        requested = [f.strip() for f in _norm_fields(fields).split(",") if f.strip()]
        missing = [f for f in requested if f not in returned]
        extra = [f for f in returned if f not in requested]
        if missing:
            self.last_field_mismatch = {"api": api_name, "missing": missing, "extra": extra}
            msg = (f"[tsfetch] 警告：{api_name} 未返回请求的字段 {missing}"
                   f"（返回列 {returned}）；Tushare 会静默丢弃无效字段名，"
                   f"请核对文档 outputs")
            if strict:
                raise FieldMismatchError(api_name, missing, requested)
            print(msg, file=sys.stderr)
        elif extra:
            self.last_field_mismatch = {"api": api_name, "missing": [], "extra": extra}
            if self.verbose:
                print(f"[tsfetch] 提示：{api_name} 额外返回未请求字段 {extra}", file=sys.stderr)
        else:
            self.last_field_mismatch = None

    def query(self, api_name: str, fields=None, params: dict | None = None,
              check_fields: bool = True, strict_fields: bool = False, **kwargs) -> list[dict]:
        """返回行记录 list[dict]；params 与关键字参数会合并（关键字优先）。"""
        merged = dict(params or {})
        merged.update(kwargs)
        envelope = self.call(api_name, merged, fields)
        data = envelope.get("data") or {}
        if check_fields:
            self._check_fields(api_name, fields, list(data.get("fields") or []), strict_fields)
        return self._to_records(data)

    def query_raw(self, api_name: str, fields=None, params: dict | None = None,
                  **kwargs) -> dict:
        """返回 {fields, items, has_more, records, envelope}，便于排查。"""
        merged = dict(params or {})
        merged.update(kwargs)
        envelope = self.call(api_name, merged, fields)
        data = envelope.get("data") or {}
        return {
            "fields": data.get("fields") or [],
            "items": data.get("items") or [],
            "has_more": data.get("has_more"),
            "records": self._to_records(data),
            "envelope": envelope,
        }

    @staticmethod
    def _to_records(data) -> list[dict]:
        if not data:
            return []
        cols = data.get("fields") or []
        rows = data.get("items") or []
        return [dict(zip(cols, row)) for row in rows]

    # ---------------------------------------------------------------- 分页
    def iter_paginate(self, api_name: str, fields=None, params: dict | None = None,
                      page_size: int = 5000, max_pages: int = 10,
                      offset_param: str = "offset", limit_param: str = "limit",
                      sleep: float | None = None, check_fields: bool = True,
                      strict_fields: bool = False, **kwargs):
        """按 offset/limit 翻页，逐页 yield (页码, 行列表)，避免一次性吃内存。

        注意：只有官方文档里声明支持 offset/limit 的接口才有效；其余接口传了会被忽略，
        表现为每页返回相同数据 —— 遇到重复页会自动停止并告警。
        """
        merged = dict(params or {})
        merged.update(kwargs)
        pause = self.sleep if sleep is None else sleep
        seen_first: list[dict] | None = None

        for page in range(max_pages):
            p = dict(merged)
            p[limit_param] = page_size
            p[offset_param] = page * page_size
            envelope = self.call(api_name, p, fields)
            data = envelope.get("data") or {}
            if page == 0 and check_fields:
                self._check_fields(api_name, fields, list(data.get("fields") or []),
                                   strict=strict_fields)
            chunk = self._to_records(data)

            if page == 0:
                seen_first = chunk
            elif chunk == seen_first:
                print(f"[tsfetch] 警告：{api_name} 第 {page + 1} 页与首页相同，"
                      f"该接口可能不支持 {offset_param}/{limit_param}，已停止翻页。",
                      file=sys.stderr)
                break

            yield page, chunk
            if len(chunk) < page_size:
                break
            if page < max_pages - 1:
                time.sleep(pause)

    def paginate(self, api_name: str, fields=None, params: dict | None = None,
                 page_size: int = 5000, max_pages: int = 10,
                 offset_param: str = "offset", limit_param: str = "limit",
                 sleep: float | None = None, strict_fields: bool = False,
                 **kwargs) -> list[dict]:
        """翻页并汇总为 list[dict]（内存版；大表请用 iter_paginate 或 CLI --append）。"""
        out: list[dict] = []
        for _, chunk in self.iter_paginate(
                api_name, fields=fields, params=params, page_size=page_size,
                max_pages=max_pages, offset_param=offset_param, limit_param=limit_param,
                sleep=sleep, strict_fields=strict_fields, **kwargs):
            out.extend(chunk)
        return out

    # ---------------------------------------------------------------- 落盘
    @staticmethod
    def save(records: list[dict], path: str, append: bool = False) -> str:
        """按扩展名落盘：.csv / .json / .jsonl / .parquet（需 pandas+pyarrow）。

        append=True 时追加写入（仅 .csv / .jsonl 支持），用于流式翻页落盘与增量更新；
        CSV 追加时若文件已存在则不重复写表头。
        """
        if not records:
            print(f"[tsfetch] 警告：0 行数据，未写文件（{path}）", file=sys.stderr)
            return path
        ext = os.path.splitext(path)[1].lower()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        if append and ext not in (".csv", ".jsonl", ".ndjson"):
            raise ValueError(
                f"--append 只支持 .csv/.jsonl（当前 {ext}）；"
                "整表格式请先落成 .csv 再转换，或去掉 --append")

        if ext == ".csv":
            cols: list[str] = []
            for r in records:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            need_header = not (append and os.path.exists(path)
                               and os.path.getsize(path) > 0)
            with open(path, "a" if append else "w", newline="",
                      encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                if need_header:
                    w.writeheader()
                w.writerows(records)
        elif ext == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        elif ext in (".jsonl", ".ndjson"):
            with open(path, "a" if append else "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        elif ext == ".parquet":
            try:
                import pandas as pd  # type: ignore
            except ImportError as e:
                raise RuntimeError("写 parquet 需要 pandas + pyarrow：pip install pandas pyarrow") from e
            pd.DataFrame(records).to_parquet(path, index=False)
        else:
            raise ValueError(f"不支持的输出格式：{ext}（支持 .csv/.json/.jsonl/.parquet）")
        return path

    @staticmethod
    def to_dataframe(records: list[dict]):
        """records -> pandas.DataFrame（数值列自动转数值）。"""
        import pandas as pd  # type: ignore
        df = pd.DataFrame(records)
        for c in df.columns:
            if df[c].dtype == object:
                conv = pd.to_numeric(df[c], errors="coerce")
                if conv.notna().sum() == df[c].notna().sum() and df[c].notna().any():
                    df[c] = conv
        return df


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _load_params(args) -> dict:
    if args.params_file:
        try:
            with open(args.params_file, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            raise ValueError(f"参数文件不存在：{args.params_file}")
        except json.JSONDecodeError as e:
            raise ValueError(f"参数文件 {args.params_file} 不是合法 JSON：{e}")
    if args.params:
        try:
            return json.loads(args.params)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"参数 JSON 解析失败：{e}\n"
                f"  收到: {args.params}\n"
                '  示例: --params \'{"ts_code":"000001.SZ","start_date":"20240101","end_date":"20240131"}\''
            )
    return {}


def _cmd_check(cli: TushareClient, args) -> int:
    """一条命令回答「这台机器上能不能用」：token / API / 文档站 / 缓存 / 落盘。"""
    import datetime as _dt

    print(f"token    : {mask_token(cli.token)}（来源：环境变量或显式传入）")
    print(f"endpoint : {API_URL}")

    failures = []
    warnings = []

    # 1) API + token
    end = _dt.date.today().strftime("%Y%m%d")
    start = (_dt.date.today() - _dt.timedelta(days=10)).strftime("%Y%m%d")
    try:
        rows = cli.query("daily", ts_code="000001.SZ", start_date=start, end_date=end)
        print(f"API 取数 : OK（daily 探针 {len(rows)} 行，字段 {list(rows[0]) if rows else '—'}）")
    except TushareError as e:
        print(f"API 取数 : 失败 code={e.code} msg={e.msg}")
        if e.hint:
            print(f"           提示: {e.hint}")
        failures.append("api")
    except Exception as e:  # noqa: BLE001
        print(f"API 取数 : 失败 {e}")
        failures.append("api")

    # 2) 文档站（走同目录的 tsdoc.py，验证第 1 步工作流是否可用）
    tsdoc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tsdoc.py")
    if os.path.isfile(tsdoc):
        try:
            proc = subprocess.run([sys.executable, tsdoc, "doc", "27", "--compact"],
                                  capture_output=True, text=True, timeout=90)
            if proc.returncode == 0 and '"daily"' in proc.stdout:
                print("文档检索 : OK（tsdoc.py 可解析接口文档）")
            else:
                print(f"文档检索 : 失败（exit {proc.returncode}）{(proc.stderr or proc.stdout).strip()[:160]}")
                warnings.append("doc")
        except Exception as e:  # noqa: BLE001
            print(f"文档检索 : 失败 {e}")
            warnings.append("doc")
    else:
        print("文档检索 : 跳过（未找到同目录 tsdoc.py）")

    # 3) 文档缓存目录可写
    cache_dir = os.environ.get("TSDOC_CACHE_DIR") or os.path.join(
        os.path.expanduser("~/.cache"), "tushare-fetcher", "docs")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        probe = os.path.join(cache_dir, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        print(f"文档缓存 : OK（{cache_dir} 可写）")
    except OSError as e:
        print(f"文档缓存 : 不可写（{e}）→ 仍可用，只是每次查文档都走网络")
        warnings.append("cache")

    # 4) 落盘能力
    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
        print("Parquet  : OK（pandas + pyarrow 可用）")
    except ImportError:
        print("Parquet  : 不可用（缺 pandas/pyarrow）→ .csv/.json/.jsonl 仍可用")
        warnings.append("parquet")

    tmp = os.path.join(tempfile.gettempdir(), ".tsfetch_check.csv")
    try:
        TushareClient.save([{"a": 1, "b": "x"}], tmp)
        with open(tmp, encoding="utf-8-sig") as f:
            ok_csv = "a,b" in f.readline()
        os.remove(tmp)
        print(f"CSV 落盘 : {'OK' if ok_csv else '异常（表头不符）'}")
        if not ok_csv:
            warnings.append("csv")
    except Exception as e:  # noqa: BLE001
        print(f"CSV 落盘 : 失败 {e}")
        failures.append("csv")

    if failures:
        print(f"结论     : 不可用（{', '.join(failures)} 失败）")
        return 1
    if warnings:
        print(f"结论     : 可用（{', '.join(warnings)} 有告警，见上）")
    else:
        print("结论     : 全部通过，可以取数")
    return 0


def _cmd_call(cli: TushareClient, args) -> int:
    params = _load_params(args)

    if args.raw:
        env = cli.call(args.api, params, args.fields)
        print(json.dumps(env, ensure_ascii=False, indent=None if args.compact else 2))
        return 0

    # 流式翻页直写文件：内存占用与数据量无关
    if args.paginate and args.out and args.append:
        total = 0
        preview_rows: list[dict] = []
        for page, chunk in cli.iter_paginate(
                args.api, fields=args.fields, params=params, page_size=args.page_size,
                max_pages=args.max_pages, sleep=args.sleep,
                strict_fields=args.strict_fields):
            if page == 0:
                preview_rows = chunk[: (args.preview if args.preview is not None else 3)]
            cli.save(chunk, args.out, append=(page > 0))
            total += len(chunk)
        print(f"[tsfetch] {args.api}: 共 {total} 行，流式追加写入 "
              f"{os.path.abspath(args.out)}", file=sys.stderr)
        if total == 0:
            print("[tsfetch] 0 行数据：核对参数与权限；未写任何文件。", file=sys.stderr)
            return 5
        if preview_rows:
            print(json.dumps(preview_rows, ensure_ascii=False,
                             indent=None if args.compact else 2))
        return 0

    if args.paginate:
        rows = cli.paginate(args.api, fields=args.fields, params=params,
                            page_size=args.page_size, max_pages=args.max_pages,
                            sleep=args.sleep, strict_fields=args.strict_fields)
    else:
        rows = cli.query(args.api, fields=args.fields, params=params,
                         strict_fields=args.strict_fields)

    cols = list(rows[0].keys()) if rows else []
    print(f"[tsfetch] {args.api}: {len(rows)} 行, {len(cols)} 列 {cols}", file=sys.stderr)

    if not rows:
        print(f"[tsfetch] 0 行数据：先核对参数（日期格式 YYYYMMDD、代码后缀、必选参数）"
              f"与接口权限；未写任何文件。", file=sys.stderr)
        return 5

    if args.out:
        path = cli.save(rows, args.out, append=args.append)
        print(f"[tsfetch] 已写入 {os.path.abspath(path)}", file=sys.stderr)
        n = args.preview if args.preview is not None else 3
        if n:
            print(json.dumps(rows[:n], ensure_ascii=False, indent=None if args.compact else 2))
    else:
        n = args.preview if args.preview is not None else min(len(rows), 20)
        print(json.dumps(rows[:n], ensure_ascii=False, indent=None if args.compact else 2))
        if n < len(rows):
            print(f"[tsfetch] 仅打印前 {n} 行（共 {len(rows)} 行；要全部数据请加 --out，"
                  f"或 --preview N 指定行数）", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    # 子命令里的同名全局选项用 default=SUPPRESS：放在子命令【前】的全局选项
    # 已由主解析器写入 namespace，子解析器不再用默认值覆盖（argparse parents 经典坑，
    # 实测 --token X call ... 会被静默忽略）。两处位置现在等价。
    def common_parser(suppress: bool) -> argparse.ArgumentParser:
        d = argparse.SUPPRESS if suppress else None
        p = argparse.ArgumentParser(add_help=False)
        p.add_argument("--token", default=d, help="显式 token（默认读 TUSHARE_MCP_TOKEN）")
        p.add_argument("--timeout", type=int, default=30 if not suppress else d)
        p.add_argument("--retries", type=int, default=2 if not suppress else d)
        p.add_argument("--insecure", action="store_true",
                       default=argparse.SUPPRESS if suppress else False,
                       help="跳过 TLS 证书校验")
        p.add_argument("--verbose", action="store_true",
                       default=argparse.SUPPRESS if suppress else False)
        p.add_argument("--compact", action="store_true",
                       default=argparse.SUPPRESS if suppress else False,
                       help="JSON 单行输出")
        p.add_argument("--rate-per-min", type=float, default=d,
                       help="每分钟调用上限（批量循环时自动节流，缺省不限）")
        return p

    common = common_parser(suppress=False)
    common_sub = common_parser(suppress=True)

    p = argparse.ArgumentParser(description="Tushare Pro 数据获取（token 取自环境变量）",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="自检：token / API / 文档站 / 缓存 / 落盘",
                   parents=[common_sub])

    pc = sub.add_parser("call", help="调用一个接口", parents=[common_sub])
    pc.add_argument("--api", required=True, help="接口名，如 daily / stock_basic")
    pc.add_argument("--params", help="JSON 字符串参数")
    pc.add_argument("--params-file", help="JSON 文件路径参数")
    pc.add_argument("--fields", help="逗号分隔字段，缺省=全部字段")
    pc.add_argument("--out", help="落盘路径（.csv/.json/.jsonl/.parquet）")
    pc.add_argument("--append", action="store_true",
                    help="追加写入（.csv/.jsonl；配合 --paginate --out 可流式落盘）")
    pc.add_argument("--strict-fields", action="store_true",
                    help="请求的字段未返回时报错（默认仅告警）")
    pc.add_argument("--preview", type=int, default=None,
                    help="打印前 N 行；缺省时 --out 打 3 行、无 --out 打前 20 行"
                         "（0 = 不打印）")
    pc.add_argument("--raw", action="store_true", help="打印原始返回信封")
    pc.add_argument("--paginate", action="store_true", help="按 offset/limit 翻页")
    pc.add_argument("--page-size", type=int, default=5000)
    pc.add_argument("--max-pages", type=int, default=10)
    pc.add_argument("--sleep", type=float, default=0.4, help="翻页间隔秒数")

    args = p.parse_args(argv)
    try:
        cli = TushareClient(token=args.token, timeout=args.timeout, retries=args.retries,
                            insecure=args.insecure, verbose=args.verbose,
                            rate_per_min=args.rate_per_min)
    except TokenMissingError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 4

    try:
        if args.cmd == "check":
            return _cmd_check(cli, args)
        return _cmd_call(cli, args)
    except TokenMissingError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 4
    except TushareError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
