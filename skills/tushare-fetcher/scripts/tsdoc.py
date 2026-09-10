#!/usr/bin/env python3
"""Tushare 官方接口文档检索（自包含，无第三方依赖；certifi 可选）。

本脚本随 tushare-fetcher skill 一起分发，独立可用，不依赖任何外部 skill。

用法:
  tsdoc.py search QUERY            关键字搜索接口
  tsdoc.py doc DOC_ID              按 doc_id 拉取并解析单个接口文档
  tsdoc.py doc --name API_NAME     按接口名定位并拉取文档（推荐）
  tsdoc.py doc --name API --skeleton   只输出参数模板 + CLI/脚本骨架
  tsdoc.py tree [FILTER]           解析文档目录树（分类/子分类/接口层级）
  tsdoc.py list CATEGORY           列出某分类下的接口清单

全局选项:
  --insecure     跳过 TLS 证书校验（仅在本地证书链异常时使用）
  --compact      JSON 单行输出（默认缩进 2）
  --no-cache     不使用本地缓存，强制走网络

缓存:
  文档正文默认缓存到 ~/.cache/tushare-fetcher/docs（TTL 7 天），
  搜索/目录树缓存 TTL 1 天。可用 TSDOC_CACHE_DIR / TSDOC_CACHE_TTL 覆盖。

端点:
  目录树   https://tushare.pro/document/2
  搜索     https://tushare.pro/document/search?q=QUERY
  正文     https://tushare.pro/wctapi/documents/{doc_id}.md
"""

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DOC_URL = "https://tushare.pro/document/2"
SEARCH_URL = "https://tushare.pro/document/search?q={}"
MD_URL = "https://tushare.pro/wctapi/documents/{}.md"
USER_AGENT = "Mozilla/5.0 (tushare-fetcher skill)"

DEFAULT_TTL = int(os.environ.get("TSDOC_CACHE_TTL", 7 * 86400))
SEARCH_TTL = int(os.environ.get("TSDOC_SEARCH_TTL", 86400))
PER_ATTEMPT_TIMEOUT = 10

_INSECURE = False
_NO_CACHE = False


def ssl_context() -> ssl.SSLContext:
    """优先用 certifi 的 CA 包（很多 Python 环境自带证书链不完整），否则用系统默认。"""
    if _INSECURE:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi 缺失或不可用则回落系统证书
        return ssl.create_default_context()


def _get_curl(url: str, timeout: int = 30) -> str:
    """urllib 反复 TLS 失败时的兜底：改用 curl（GET 无敏感信息）。"""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl 不可用")
    proc = subprocess.run(
        [curl, "-sS", "--fail", "-m", str(timeout), "-A", USER_AGENT, url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def http_get(url: str, retries: int = 3) -> str:
    """带重试的 GET。

    官方文档站偶发 TLS EOF / 连接重置（同一台机器上 curl 正常、Python OpenSSL 会中招），
    因此先按退避+抖动重试，仍失败则用 curl 兜底。单次尝试超时较短，尽快切兜底。
    """
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=PER_ATTEMPT_TIMEOUT,
                                        context=ssl_context()) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last_err = e
        except (urllib.error.URLError, socket.timeout) as e:
            last_err = e
        if attempt < retries - 1:
            time.sleep(0.4 * (2 ** attempt) + random.random() * 0.3)

    try:
        return _get_curl(url)
    except Exception as curl_err:  # noqa: BLE001
        print(f"[tsdoc] urllib 重试 {retries} 次失败（{last_err}），curl 兜底也失败：{curl_err}",
              file=sys.stderr)
        raise last_err


# --------------------------------------------------------------------------- #
# 本地缓存（文档站偶发慢/断连，缓存让重复查询瞬时返回）
# --------------------------------------------------------------------------- #

def _cache_dir() -> str:
    return os.environ.get("TSDOC_CACHE_DIR") or os.path.join(
        os.path.expanduser("~/.cache"), "tushare-fetcher", "docs")


def _cache_file(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:60]
    return os.path.join(_cache_dir(), f"{safe}.txt")


def cached_get(url: str, key: str, ttl: int = DEFAULT_TTL) -> str:
    """带磁盘缓存的 GET；缓存不可写时静默降级为纯网络。"""
    path = _cache_file(key)
    if not _NO_CACHE:
        try:
            if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl:
                with open(path, encoding="utf-8") as f:
                    return f.read()
        except OSError:
            pass

    text = http_get(url)

    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass  # 缓存目录不可写不影响主流程
    return text


def _strip_md(s: str) -> str:
    """去除 markdown 链接与强调语法，保留纯文本。"""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s


def _first_ident(s: str):
    """取字符串开头的一个标识符（接口名），如 'daily，可以通过...' -> 'daily'。"""
    m = re.match(r"\s*([A-Za-z_]\w*)", s or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# 目录树解析（jsTree HTML）
# --------------------------------------------------------------------------- #

class _TreeParser(HTMLParser):
    """解析 #jstree 块：<li> 嵌套即分类层级，叶子 <li> 为接口。"""

    def __init__(self):
        super().__init__()
        self.stack = []   # 打开的 <li> 节点栈
        self.roots = []   # 顶层分类节点
        self._in_link = False
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            node = {"type": "category", "doc_id": None, "title": "", "children": []}
            if self.stack:
                self.stack[-1]["children"].append(node)
            else:
                self.roots.append(node)
            self.stack.append(node)
        elif tag == "a":
            m = re.search(r"doc_id=(\d+)", attrs.get("href", ""))
            if m and self.stack:
                self.stack[-1]["doc_id"] = int(m.group(1))
                self._in_link = True
                self._text = []

    def handle_data(self, data):
        if self._in_link:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            self._in_link = False
            if self.stack:
                self.stack[-1]["title"] = "".join(self._text).strip()
        elif tag == "li":
            if self.stack:
                self.stack.pop()


def _classify(nodes):
    for n in nodes:
        if n["children"]:
            n["type"] = "category"
            _classify(n["children"])
        else:
            n["type"] = "interface"


def _tree_html() -> str:
    page = cached_get(DOC_URL, "tree", SEARCH_TTL)
    block = page.split('<div id="jstree">', 1)[1].split("</div>", 1)[0]
    return block


def parse_tree() -> list:
    parser = _TreeParser()
    parser.feed(_tree_html())
    _classify(parser.roots)
    return parser.roots


def _filter_tree(nodes, kw):
    out = []
    for n in nodes:
        if kw in n["title"]:
            out.append(n)
        elif n["type"] == "category":
            sub = _filter_tree(n["children"], kw)
            if sub:
                node = dict(n)
                node["children"] = sub
                out.append(node)
    return out


def list_category(nodes, kw) -> list:
    """按分类关键词匹配分类节点，扁平列出其下全部接口。"""
    hits = []

    def walk(ns, path):
        for n in ns:
            p = path + [n["title"]]
            if n["type"] == "category":
                if kw in n["title"]:
                    hits.append((n, p))
                walk(n["children"], p)

    walk(nodes, [])

    def leaves(node):
        res = []
        if node["type"] == "interface":
            res.append(node)
        for c in node.get("children", []):
            res.extend(leaves(c))
        return res

    out = []
    for cat, path in hits:
        for leaf in leaves(cat):
            out.append({"path": " / ".join(path), "doc_id": leaf["doc_id"], "title": leaf["title"]})
    return out


# --------------------------------------------------------------------------- #
# 搜索解析
# --------------------------------------------------------------------------- #

def search(query: str) -> list:
    url = SEARCH_URL.format(urllib.parse.quote(query))
    key = "search_" + hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
    page = cached_get(url, key, SEARCH_TTL)
    results = []
    for chunk in page.split('<h2 class="title">')[1:]:
        m = re.search(r'<a class="link" href="/document/(\d+)\?doc_id=(\d+)">(.*?)</a>', chunk, re.S)
        if not m:
            continue
        doc_type, doc_id, title = m.group(1), int(m.group(2)), _strip_md(m.group(3)).strip()
        if doc_type != "2":  # 仅保留数据接口（document/2），丢弃平台介绍页
            continue
        entry = {
            "doc_id": doc_id,
            "title": title,
            "api": None,
            "description": None,
            "limit": None,
            "permission": None,
            "url": f"{DOC_URL}?doc_id={doc_id}",
        }
        for key, out in (("接口：", "api"), ("描述：", "description"),
                         ("限量：", "limit"), ("权限：", "permission")):
            mv = re.search(re.escape(key) + r"([^\n]*)", chunk)
            if mv:
                entry[out] = _strip_md(mv.group(1)).strip()
        if entry["api"]:
            entry["api"] = _first_ident(entry["api"])
        results.append(entry)
    return results


# --------------------------------------------------------------------------- #
# 接口正文解析
# --------------------------------------------------------------------------- #

def _parse_table(s: str) -> list:
    """解析 markdown 表格（兼容行首无/有竖线的写法），跳过 --- 分隔行。"""
    rows = []
    for line in s.splitlines():
        line = line.strip()
        if line.startswith("|") or " | " in line:
            cells = [_strip_md(c.strip()) for c in line.strip("|").split("|")]
            rows.append(cells)
    sep = re.compile(r":?-+:?")
    data = [r for r in rows if not (len(r) > 0 and all(sep.fullmatch(c) for c in r))]
    if len(data) < 1:
        return []
    header = data[0]
    return [dict(zip(header, r)) for r in data[1:] if len(r) == len(header)]


def _parse_doc(doc_id: int, text: str) -> dict:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 头部：首个整行加粗标题之前的全部内容（meta 键值行）
    first_bold = re.search(r"^\*\*.+?\*\*$", text, re.M)
    header = text[:first_bold.start()] if first_bold else text

    # 各小节：从整行加粗标题取到下一个整行加粗标题为止
    sections = {}
    for key in ("输入参数", "输出参数", "接口示例", "数据样例"):
        m = re.search(
            rf"^\*\*{re.escape(key)}\*\*\s*(.*?)(?=^\*\*.+?\*\*|\Z)",
            text, re.M | re.S,
        )
        if m:
            sections[key] = m.group(1)

    mt = re.search(r"^#+\s*(.+)$", header, re.M)
    title = _strip_md(mt.group(1).strip()) if mt else ""

    meta = {}
    for line in header.splitlines():
        line = line.strip()
        kv = re.match(r"^([^：:：]+?)[：:]\s*(.*)$", line)
        if kv:
            meta[kv.group(1).strip()] = _strip_md(kv.group(2)).strip()

    api = _first_ident(meta.pop("接口", ""))

    sample = sections.get("数据样例", "").strip()
    sample = re.sub(r"^```\w*\s*|\s*```$", "", sample).strip()

    examples = re.findall(r"```\w*\n(.*?)```", sections.get("接口示例", ""), re.S)
    examples = [e.strip() for e in examples]

    return {
        "doc_id": int(doc_id),
        "title": title,
        "api": api,
        "url": f"{DOC_URL}?doc_id={doc_id}",
        "meta": meta,
        "inputs": _parse_table(sections.get("输入参数", "")),
        "outputs": _parse_table(sections.get("输出参数", "")),
        "examples": examples,
        "sample": sample,
    }


def fetch_doc(doc_id: int) -> dict:
    text = cached_get(MD_URL.format(doc_id), f"doc_{doc_id}", DEFAULT_TTL)
    return _parse_doc(doc_id, text)


def _index_path() -> str:
    return os.path.join(_cache_dir(), "_api_index.json")


def _load_index() -> dict:
    if _NO_CACHE:
        return {}
    try:
        with open(_index_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(idx: dict) -> None:
    if _NO_CACHE:
        return
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=0, sort_keys=True)
    except OSError:
        pass


def resolve_api(name: str) -> dict:
    """按接口名定位文档：优先命中本地 api→doc_id 索引，未命中再搜索比对。

    索引把首次解析成本一次性付清，之后同一接口只拉一次正文（而非逐个候选）。
    """
    idx = _load_index()
    cached_id = idx.get(name)
    if isinstance(cached_id, int):
        try:
            doc = fetch_doc(cached_id)
            if doc["api"] == name:
                return doc
        except Exception:  # noqa: BLE001 - 索引过期则回退搜索
            pass

    candidates = [r for r in search(name) if r["api"]]
    if not candidates:
        raise LookupError(f"未搜索到接口：{name}")
    for c in candidates:
        doc = fetch_doc(c["doc_id"])
        if doc["api"] == name:
            idx[name] = c["doc_id"]
            _save_index(idx)
            return doc
    raise LookupError(f"未找到接口 {name} 的精确文档，候选："
                      + "、".join(f"{c['title']}({c['doc_id']})" for c in candidates))


# --------------------------------------------------------------------------- #
# 骨架生成：把文档直接变成可粘贴的取数代码
# --------------------------------------------------------------------------- #

_PARAM_HINTS = {
    "ts_code": "000001.SZ", "index_code": "000300.SH", "hs_code": "000001.SZ",
    "trade_date": "20240102", "start_date": "20240101", "end_date": "20241231",
    "ann_date": "20240102", "cal_date": "20240102", "period": "20241231",
    "exchange": "SSE", "freq": "D", "symbol": "TS.CFX", "market": "主板",
    "list_status": "L", "is_open": "1", "limit": "100", "offset": "0",
}
_COMMON_OPTIONAL = ("ts_code", "trade_date", "start_date", "end_date", "index_code")


def build_skeleton(doc: dict) -> dict:
    """按文档生成参数模板 + CLI/脚本骨架。"""
    api = doc.get("api")
    inputs = doc.get("inputs") or []
    outputs = doc.get("outputs") or []

    required = [i for i in inputs if str(i.get("必选", "")).strip().upper() == "Y"]
    if not required:  # 无必选参数的接口（如 daily），给出常用日期/代码参数
        required = [i for i in inputs if i.get("名称") in _COMMON_OPTIONAL]

    params = {i["名称"]: _PARAM_HINTS.get(i["名称"], f"<{i['名称']}>") for i in required}

    # 标的参数与 trade_date 互斥：混传时 Tushare 优先 trade_date，
    # 会静默只返回单日数据。默认保留「单标的 + 日期区间」组合。
    notes = []
    code_key = next((k for k in ("ts_code", "index_code", "hs_code") if k in params), None)
    if code_key and "trade_date" in params and "start_date" in params:
        del params["trade_date"]
        notes.append(f"{code_key} 与 trade_date 互斥（混传时 Tushare 按 trade_date 优先，"
                     f"只返回单日）：已默认保留「{code_key} + start/end 区间」；"
                     "要取全市场某一天，把 params 换成 {\"trade_date\": \"YYYYMMDD\"}")

    display = [o["名称"] for o in outputs
               if str(o.get("默认显示", "")).strip().upper() == "Y"]
    if not display:
        display = [o["名称"] for o in outputs]
    fields = ",".join(display)

    meta = doc.get("meta") or {}
    cli = (f"python3 scripts/tsfetch.py call --api {api} \\\n"
           f"  --params '{json.dumps(params, ensure_ascii=False)}' \\\n"
           f"  --fields {fields} \\\n"
           f"  --out {api}.csv")
    python = (f'from tsfetch import TushareClient\n'
              f'cli = TushareClient()\n'
              f'rows = cli.query("{api}", fields="{fields}",\n'
              f'                 params={json.dumps(params, ensure_ascii=False)})\n'
              f'cli.save(rows, "{api}.csv")')
    return {
        "api": api,
        "title": doc.get("title"),
        "required_params": [i["名称"] for i in required],
        "optional_params": [i["名称"] for i in inputs if i not in required],
        "params_template": params,
        "fields": fields,
        "limit": meta.get("限量") or meta.get("调取说明"),
        "permission": meta.get("权限"),
        "notes": notes,
        "cli": cli,
        "python": python,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    # 子命令里的同名全局选项用 default=SUPPRESS：放在子命令【前】的全局选项
    # 已由主解析器写入 namespace，子解析器不再用默认值覆盖（argparse parents 经典坑）。
    def common_parser(suppress: bool) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(add_help=False)
        d = argparse.SUPPRESS if suppress else None
        p.add_argument("--insecure", action="store_true",
                       default=argparse.SUPPRESS if suppress else False,
                       help="跳过 TLS 证书校验")
        p.add_argument("--compact", action="store_true",
                       default=argparse.SUPPRESS if suppress else False,
                       help="JSON 单行输出")
        p.add_argument("--no-cache", action="store_true",
                       default=argparse.SUPPRESS if suppress else False,
                       help="不使用本地缓存")
        return p

    common = common_parser(suppress=False)
    common_sub = common_parser(suppress=True)

    p = argparse.ArgumentParser(description="Tushare 官方接口文档检索工具（自包含）",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("search", help="关键字搜索接口", parents=[common_sub])
    ps.add_argument("query")

    pd = sub.add_parser("doc", help="拉取并解析单个接口文档", parents=[common_sub])
    pd.add_argument("doc_id", nargs="?", type=int)
    pd.add_argument("--name")
    pd.add_argument("--skeleton", action="store_true",
                    help="输出参数模板 + 可粘贴的 CLI/脚本骨架（替代完整文档）")

    pt = sub.add_parser("tree", help="解析文档目录树", parents=[common_sub])
    pt.add_argument("filter", nargs="?")

    pl = sub.add_parser("list", help="列出某分类下的接口清单", parents=[common_sub])
    pl.add_argument("category")

    args = p.parse_args()

    global _INSECURE, _NO_CACHE
    _INSECURE = bool(args.insecure)
    _NO_CACHE = bool(args.no_cache)

    try:
        if args.cmd == "search":
            out = search(args.query)
        elif args.cmd == "doc":
            if args.name:
                out = resolve_api(args.name)
            elif args.doc_id is not None:
                out = fetch_doc(args.doc_id)
            else:
                p.error("doc 需要 DOC_ID 参数或 --name API_NAME")
            if args.skeleton:
                out = build_skeleton(out)
        elif args.cmd == "tree":
            nodes = parse_tree()
            out = _filter_tree(nodes, args.filter) if args.filter else nodes
        elif args.cmd == "list":
            out = list_category(parse_tree(), args.category)
        else:
            p.error("未知命令")
    except LookupError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(2)
    except urllib.error.URLError as e:
        print(f"网络请求失败：{e}\n提示：若为 TLS 证书错误，可加 --insecure 重试。", file=sys.stderr)
        sys.exit(3)
    except Exception as e:  # noqa: BLE001
        print(f"请求失败：{e}", file=sys.stderr)
        sys.exit(3)

    print(json.dumps(out, ensure_ascii=False, indent=None if args.compact else 2,
                     separators=(",", ":") if args.compact else None))


if __name__ == "__main__":
    main()
