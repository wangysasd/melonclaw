---
name: tushare-fetcher
description: Tushare 数据获取技能（自包含，不依赖任何其他 skill）。先用内置文档检索脚本确认接口名/输入参数/输出字段/限量与积分门槛，再生成并运行取数脚本，通过 Tushare Pro API 拉取数据并落盘为 CSV/JSON/Parquet。Token 一律从环境变量 TUSHARE_MCP_TOKEN 读取。当用户要求"取/拉/下载 Tushare 数据""调某接口拿数据""把日线、财务、资金流、指数成分等存成 csv/parquet""写个脚本调 tushare api"时触发。
requirements:
  python: 3.9+
  packages: []
  optional_packages: [certifi, pandas, pyarrow]
  env: [TUSHARE_MCP_TOKEN]
  network_access: true
---

# tushare-fetcher

Tushare 数据获取的**端到端**技能：**先查文档 → 再写脚本 → 再调 API 取数落盘**。

本 skill **自带全部脚本，独立完整**，不依赖、也不需要加载其他文档类 skill。

## 能力边界

做：
- 按需求定位接口，拿到权威的输入参数、输出字段、限量、积分门槛
- 生成可复现的取数脚本（或直接用 CLI）调用 `https://api.tushare.pro`
- 分页拉取、失败重试、落盘 CSV/JSON/JSONL/Parquet，并做基本校验

不做：
- 数据解读、建模、回测（取数完成即交付；分析类任务另找分析 skill）
- 非 Tushare 数据源

## 前置条件：Token

Token **只从环境变量读**，顺序为 `TUSHARE_MCP_TOKEN` → `TUSHARE_TOKEN`（兼容别名）。

```bash
export TUSHARE_MCP_TOKEN=<your_token>
python3 scripts/tsfetch.py check     # 先自检：token 有效性 + 接口连通性
```

铁律：
- **禁止**把 token 硬编码进脚本、写进配置文件、或提交到仓库
- **禁止**在输出里打印完整 token（脚本内置 `mask_token()`，日志只显示首尾 4 位）
- 未设置环境变量时，脚本会报 `TokenMissingError` 并明确提示该导出哪个变量——遇到就直接告诉用户去设置，不要自己去找 token

## 目录结构

```
tushare-fetcher/
├── SKILL.md                       本文件：工作流与规范
├── scripts/
│   ├── tsdoc.py                   文档检索（自包含，纯标准库）
│   └── tsfetch.py                 取数客户端 + CLI（token 从环境变量读）
├── templates/
│   └── fetch_script.py            取数脚本模板（改四处即可用）
└── references/
    └── tushare-api.md             API 约定：请求/响应结构、错误码、分页、限流、落盘
```

下文所有 `scripts/...` 都相对于**本 skill 的 base directory**（会话里给出的 skill 基础目录）。

## 标准工作流（四步，不可跳步）

### 第 1 步：查文档，确认接口细节

**永远先查文档再写代码**，不要凭记忆猜接口名、参数名或字段名。

```bash
# 已知接口名 → 直接定位（推荐）
python3 scripts/tsdoc.py doc --name daily

# 只要"参数模板 + 可粘贴骨架"，不要整篇文档
python3 scripts/tsdoc.py doc --name daily --skeleton

# 只有需求 → 关键字搜索，再从候选里挑
python3 scripts/tsdoc.py search 资金流
python3 scripts/tsdoc.py search 涨停
python3 scripts/tsdoc.py search 指数成分

# 浏览分类目录
python3 scripts/tsdoc.py list 行情
python3 scripts/tsdoc.py tree 财务
```

从文档里**必须**读出来并落实：
1. `api`：接口名（以正文为准，**不是**搜索结果页显示的名字）
2. `inputs`：每个参数读 `名称` / `类型` / `必选`(Y/N) / `描述`
3. `outputs`：每个字段读 `名称` / `类型` / `默认显示`(Y/N) / `描述`
4. `meta`：限量、积分门槛、入库时间等；键名随接口而变（常见 `限量` / `权限` / `调取说明` / `数据说明` / `描述`），**有则必读**
5. `examples`：官方调用示例（保留参数写法）

限量与积分信息可能只写在 `meta.调取说明` 或 `meta.描述` 里（如 daily 的"基础积分每分钟内可调取500次，每次6000条数据"），逐行读完再动手。

> 偷懒但可靠的走法：`doc --name <api> --skeleton` 直接给出 `params_template`（必选/常用参数已填好示例值，**互斥参数已自动取舍**——如 `ts_code` 与 `trade_date` 只保留其一，取舍原因见返回的 `notes`）、`fields`（默认显示字段）、`optional_params`（其他可用参数名），以及可粘贴的 `cli` 与 `python` 两段代码——省掉手工转录字段名这一步，**粘贴即用**。

### 第 2 步：写取数脚本

优先用模板：复制 `templates/fetch_script.py`，把 `API` / `PARAMS` / `FIELDS` / `OUT` 四处改掉即可。
需要更复杂逻辑（多接口拼接、循环股票池、增量更新）时，import `tsfetch` 自己写。
模板会自动在若干常见位置寻找 `tsfetch.py`；若把它复制到别处运行，最省事的是把文件顶部
`SKILL_SCRIPTS_OVERRIDE` 填成本 skill 的 `<base dir>/scripts`（或设 `TUSHARE_FETCHER_SCRIPTS`），
也可以直接在 skill 目录内原地改。

也可以完全跳过写脚本，直接用 CLI（适合一次性取数）：

```bash
python3 scripts/tsfetch.py call \
  --api daily \
  --params '{"ts_code":"000001.SZ","start_date":"20240101","end_date":"20240131"}' \
  --fields ts_code,trade_date,open,high,low,close \
  --out daily_000001.csv
```

### 第 3 步：运行并校验

跑完**必须**核对，不要拿到文件就宣布成功：

| 检查项 | 怎么查 |
|---|---|
| 行数是否合理 | 打印行数；和日期区间/股票池规模对得上吗 |
| 日期范围 | 排序后看首尾日期，是否覆盖请求区间（区间内有停牌属正常） |
| 字段是否齐全 | 列名与文档 `outputs` 对齐；关键字段有没有整列空。**请求了不存在的字段时 Tushare 静默丢弃**（实测请求 `close` 但接口无该列，返回直接少一列）——`tsfetch` 已内置比对并打警告，加 `--strict-fields` 可直接报错 |
| 空值比例 | 关键字段空值过多 → 参数可能写错（如日期格式、代码后缀） |
| 是否被截断 | `has_more` 为 true 或行数正好等于限量 → 需要翻页 |
| 重复行 | 财务类接口同一 `(ts_code, end_date)` 可能返回多行（个别字段为 null）→ 按主键去重，见 `references/tushare-api.md` |
| 权限是否受限 | 无权限时 Tushare 直接报错，不会返回空表 |

```bash
python3 scripts/tsfetch.py call --api daily --params '...' --raw   # 看 has_more 与原始信封
```

### 第 4 步：落盘并汇报

文件命名建议 `{接口}_{主体}_{区间}.{扩展名}`，例如 `daily_000001SZ_20240101_20240131.csv`。
汇报时给出：接口名、实际参数、行数、字段数、时间范围、落盘路径、遇到的限制（积分/限量/翻页）。

## 脚本 A：文档检索 `scripts/tsdoc.py`

| 命令 | 用途 |
|---|---|
| `doc --name API` | 按接口名定位并解析正文（推荐） |
| `doc --name API --skeleton` | 只输出参数模板 + CLI/脚本骨架 |
| `doc DOC_ID` | 按 doc_id 拉取 |
| `search QUERY` | 关键字搜索，返回候选列表 |
| `list CATEGORY` | 列出某分类下的接口清单 |
| `tree [FILTER]` | 目录树（分类→子分类→接口） |

输出统一 JSON。`doc` 的结构（注意 `inputs`/`outputs` 的列名是中文）：

```json
{
  "doc_id": 27, "title": "A股日线行情", "api": "daily",
  "url": "https://tushare.pro/document/2?doc_id=27",
  "meta": {"描述": "...", "调取说明": "...", "数据说明": "..."},
  "inputs":  [{"名称": "ts_code", "类型": "str", "必选": "N", "描述": "..."}],
  "outputs": [{"名称": "ts_code", "类型": "str", "默认显示": "Y", "描述": "..."}],
  "examples": ["...官方 python 示例..."],
  "sample": "...数据样例..."
}
```

`search` 输出数组，每项：`doc_id` / `title` / `api` / `description` / `limit` / `permission` / `url`。
`list` 输出数组，每项：`path` / `doc_id` / `title`。

要点：
- 搜索页的接口名**会丢下划线**（`cn_cpi` 显示成 `cncpi`），权威接口名一律以 `doc` 正文的 `接口：` 行 为准；`doc --name` 已内置比对
- 搜索结果里 `api` 为 `null` 的是分类页或正文片段，优先选有 `api` 的条目
- `tree` 中 `type=category` 的节点是分类，不是接口，不能调用
- **本地缓存**：文档正文缓存到 `~/.cache/tushare-fetcher/docs`（TTL 7 天），搜索/目录树 TTL 1 天。重复查同一接口几乎瞬时返回；要强制刷新用 `--no-cache`，改缓存位置用 `TSDOC_CACHE_DIR`
- **api→doc_id 索引**：首次解析成功的接口会记进 `_api_index.json`，之后 `doc --name` 直接命中，不再逐个候选拉正文
- 该脚本内置退避重试 + `curl` 兜底，用于应对文档站偶发的 TLS 中断（见"常见坑"）

## 脚本 B：取数 `scripts/tsfetch.py`

### CLI

```bash
python3 scripts/tsfetch.py check                       # 一条命令自检：token/API/文档站/缓存/落盘
python3 scripts/tsfetch.py call --api <接口> [选项]

选项：
  --params '{"k":"v"}'        JSON 参数（与 --params-file 二选一）
  --params-file p.json        参数文件
  --fields a,b,c              返回字段，缺省=文档全部字段
  --out path.{csv,json,jsonl,parquet}
  --append                    追加写入（.csv/.jsonl）；配合 --paginate --out 可流式落盘
  --strict-fields             请求的字段未返回时报错（默认仅告警）
  --preview N                 打印前 N 行；缺省时 --out 打 3 行、无 --out 打前 20 行（0 = 不打印）
  --raw                       打印 Tushare 原始返回信封（排查 has_more/msg）
  --paginate --page-size N --max-pages M --sleep S
                              按 offset/limit 翻页（仅文档声明支持时有效）
通用：--rate-per-min N（批量循环自动节流）--insecure --verbose --compact --token
      通用选项放在子命令前后均可（v1.2.0 修复：此前放在子命令前会被静默忽略）

退出码：`0` 成功且有数据、`1` Tushare 业务错误（code≠0）、`3` 网络/落盘/字段不匹配、
`4` 缺 token、`5` 返回 0 行（**不写文件**，便于脚本判断）。

### 库 API（写脚本时用）

```python
import sys, os
sys.path.insert(0, os.environ.get("TUSHARE_FETCHER_SCRIPTS",
                                  "<本 skill base dir>/scripts"))
from tsfetch import TushareClient, TushareError, FieldMismatchError

cli = TushareClient()                    # token 自动取 TUSHARE_MCP_TOKEN
rows = cli.query("daily",                # -> list[dict]
                 fields="ts_code,trade_date,close",
                 ts_code="000001.SZ", start_date="20240101", end_date="20240131")

raw = cli.query_raw("daily", ts_code="000001.SZ")   # -> {fields, items, has_more, records, envelope}
rows = cli.paginate("daily", params={"trade_date": "20240102"},
                    page_size=5000, max_pages=10)    # -> list[dict]
cli.save(rows, "out.parquet")            # .csv/.json/.jsonl/.parquet
cli.save(rows, "out.csv", append=True)   # 追加（仅 .csv/.jsonl）
df = cli.to_dataframe(rows)              # 数值列自动转数值

# 大表流式落盘：内存占用与数据量无关
for page, chunk in cli.iter_paginate("daily", params={"trade_date": "20240102"}):
    cli.save(chunk, "all.csv", append=(page > 0))

cli = TushareClient(rate_per_min=180)    # 批量循环时自动节流（默认不限）
rows = cli.query("daily", fields="ts_code,close", ts_code="000001.SZ",
                 strict_fields=True)     # 请求字段缺失时抛 FieldMismatchError
```

`query()` 的关键字参数会与 `params=` 合并，关键字优先；`fields` 可传逗号字符串或列表。
字段不匹配时 `cli.last_field_mismatch` 会保留 `{api, missing, extra}` 供脚本判断。

## 撰写取数脚本的规范

1. **参数与字段来自第 1 步的文档**，逐字照抄接口名、参数名、字段名
2. **token 只从环境变量读**，用 `TushareClient()` 即可，不要 `os.environ["..."]` 手动拼 HTTP
3. **显式写 `fields`**：不写会返回文档全部字段，容易拖慢和浪费
4. **日期统一 `YYYYMMDD`**，股票代码带后缀（`000001.SZ`、`600000.SH`、`000001.SZ` 与指数 `000300.SH`）
5. **异常要分类处理**：`TokenMissingError`（配置问题）、`TushareError`（业务/权限问题，看 code+msg，自带 `hint` 处置建议）、`FieldMismatchError`（字段名写错）、其他（网络/落盘）
6. **落盘前做行数断言**，0 行时不要写空文件覆盖旧数据
7. **大区间先小样本试跑**（取最近 5 个交易日），确认字段与参数无误再拉全量
8. **循环取多只/多日时开节流**：`TushareClient(rate_per_min=...)` 或 CLI `--rate-per-min`，避免撞频率限制

## 常见坑

| 现象 | 原因 / 处理 |
|---|---|
| 日期区间取数只返回 1 行 | `ts_code` 与 `trade_date` **互斥**：混传时 Tushare 按 `trade_date` 优先，静默只返回单日 → 删掉 `trade_date`（取单标的区间）或只留 `trade_date`（取全市场单日）。`--skeleton` 生成的模板已自动规避，取舍原因见其 `notes` |
| `code=40101 您的token不对` | 环境变量没设、设错或 token 失效 → 提示用户检查 `TUSHARE_MCP_TOKEN` |
| `code=40203 / msg 提到权限` | 积分不够 → 看文档 `meta` 里的积分要求，换低门槛接口或让用户升级 |
| 返回空表 | 参数写错最常见：日期格式、代码后缀、`ts_code` vs `trade_date` 二选一。CLI 此时退出码 `5` 且**不写文件**，不会用空表覆盖旧数据 |
| 行数正好等于限量 | 被截断 → 用 `--paginate` 或缩小日期区间分批拉 |
| 翻页结果每页一样 | 该接口不支持 `offset`/`limit` → 改按时间区间切分 |
| `has_more=true` | 还有数据 → 继续翻页 |
| 数值列是字符串 | JSON 里数值可能是字符串 → `to_dataframe()` 或 `pd.to_numeric` |
| TLS 证书校验失败 | `pip install certifi`（脚本已优先使用）；仍失败再加 `--insecure` |
| 文档站报 `SSL: UNEXPECTED_EOF_WHILE_READING` | 官方文档站对 Python OpenSSL 偶发 TLS 中断（同机 `curl` 正常）。`tsdoc.py` 已内置退避重试 + `curl` 兜底，重跑即可；连续大量调用会变慢，避免无谓的重复查询 |
| 请求了不存在的字段，返回却少了几列 | Tushare 静默丢弃无效 `fields`，不报错 → `tsfetch` 已自动比对并告警，`--strict-fields` 可升级为报错 |
| 撞到频率限制 | 循环取数时加 `--rate-per-min N`（或 `TushareClient(rate_per_min=N)`）自动节流 |
| 请求偶发超时 | 客户端自带重试；仍失败可调 `--timeout 60 --retries 3` |

更多 API 细节（请求/响应结构、错误码、限流、字段类型）见 `references/tushare-api.md`。

## Examples

- "取平安银行 2024 年日线" → `doc --name daily --skeleton` 拿参数模板 → CLI `call --api daily` 或模板脚本 → 校验行数/日期 → 落盘 `daily_000001SZ_20240101_20241231.csv`
- "把沪深300成分股列表拉下来" → `search 指数成分` / `doc --name index_weight` → 取数落盘
- "看看资金流有哪些接口" → `search 资金流` → 列出候选，等用户确认再取
- "帮我每天更新一份股票基础信息" → `doc --name stock_basic` → 模板脚本改成取全量 → `--out stock_basic.csv`（增量更新用 `--append`）
- "先确认这台机器能不能用" → `python3 scripts/tsfetch.py check`
- "这个接口要多少积分" → `doc --name <api>` → 读 `meta` 里的积分/权限说明

## When NOT to use

- 只想查文档、不取数 → 直接跑 `scripts/tsdoc.py` 即可（本 skill 内置，无需别的 skill）
- 要对取回的数据做分析/建模/出报告 → 先取数，再交给对应分析 skill
- 非 Tushare 数据源（Wind、iFinD、交易所）→ 不适用
