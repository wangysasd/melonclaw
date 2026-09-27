# Tushare Pro API 约定

取数脚本必须遵守的底层细节。文档检索用 `scripts/tsdoc.py`，取数用 `scripts/tsfetch.py`。

## 1. 请求

```
POST https://api.tushare.pro
Content-Type: application/json

{
  "api_name": "daily",              // 接口名，来自文档正文的 接口： 行
  "token":    "<TUSHARE_MCP_TOKEN>", // 只从环境变量读，禁止硬编码
  "params":   {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240131"},
  "fields":   "ts_code,trade_date,close"   // 逗号分隔；空字符串 = 返回文档全部字段
}
```

`params` 的键名、类型、必选性一律以文档 `输入参数` 表为准；`fields` 的列名以 `输出参数` 表为准。

⚠️ **标的参数与单日参数互斥**：`ts_code`（或 `index_code`）与 `trade_date` 不应同时传——Tushare 不报错，但按 `trade_date` 优先，**静默只返回单日数据**（实测取平安银行全年日线混传 `trade_date` 后只回来 1 行）。取单标的区间就只传 `ts_code + start_date/end_date`；取全市场单日就只传 `trade_date`。`tsdoc.py --skeleton` 生成的模板已自动取舍并在 `notes` 里说明。

⚠️ **`fields` 里的无效列名会被静默丢弃**，不报错。实测 `index_dailybasic` 请求 `ts_code,trade_date,close,turnover_rate,pe_ttm`，返回只有 4 列——该接口没有 `close`，Tushare 直接忽略。所以**必须**把返回列名与文档 `outputs` 逐列比对，否则会拿到少列的数据却毫无提示。`tsfetch` 已内置该比对：默认打警告，`--strict-fields` / `strict_fields=True` 时直接报错。

## 2. 响应

成功：

```json
{
  "request_id": "...",
  "code": 0,
  "msg": null,
  "data": {
    "fields": ["ts_code", "trade_date", "close"],
    "items":  [["000001.SZ", "20240102", 9.35], ...],
    "has_more": false
  }
}
```

失败：

```json
{"request_id": "", "code": 40101, "data": null, "msg": "您的token不对，请确认。", "detail": ""}
```

判定规则：**`code == 0` 才是成功**，其余全部按错误处理，`msg` 原样呈现给用户（`detail` 常有补充说明）。
`items` 是**二维数组**（行→列），不是对象数组；列名在 `data.fields`，需要自行 `zip(fields, row)` 组装——`tsfetch.py` 已封装。

## 3. 错误码

| code | 含义 | 处理 |
|---|---|---|
| `0` | 成功 | — |
| `40101` | token 不对 / 无效 | 检查环境变量 `TUSHARE_MCP_TOKEN` 是否导出、是否失效 |
| 其他非 0 | 权限/积分不足、参数错误、频率超限等 | **以 `msg` 为准**：无权限时 msg 会写明接口与所需积分；参数错时 msg 会指出字段 |

`tsfetch.py` 抛 `TushareError`，带 `.code` / `.msg` / `.detail` / `.api` 四个属性，脚本里据此分支。

## 4. 权限与积分

文档 `meta.权限` 行写明接口所需积分（如"用户需要至少 2000 积分才可以调取"）。
账号积分不足时接口**直接报错**，不会返回空表——所以"返回 0 行"几乎总是参数问题，不是权限问题。

## 5. 限量、截断与分页

- 文档 `meta.限量` 行写明单次返回行数上限（常见 2000 / 5000 / 6000）
- 行数**正好等于上限**、或响应 `has_more=true` → 数据被截断，必须继续取
- 支持分页的接口用 `offset` + `limit` 参数循环：`tsfetch.py --paginate --page-size N --max-pages M`
- **不支持 offset/limit 的接口**（如多数按 `trade_date` 取单日的接口）只能按时间区间或标的切分，逐批拉取
- 翻页失败特征：每页内容完全相同 → 该接口忽略了分页参数，脚本会自动停止并告警
- 大表用流式落盘避免吃内存：`--paginate --out all.csv --append`（CLI）或 `iter_paginate()`（库）

## 6. 频率限制

Tushare 按"每接口每分钟调用次数"限流（各接口门槛不同，见文档 `调取说明`）。
实践建议：
- 循环取数时每次调用之间 `sleep 0.3~1.0` 秒（`TushareClient(sleep=...)` 或 `--sleep`）
- 批量循环建议直接开节流：`--rate-per-min 180`（CLI）或 `TushareClient(rate_per_min=180)`，客户端会保证调用间隔 ≥ 60/N 秒
- 批量拉多只股票/多个日期时，先用 2~3 个样本试跑，确认速率与字段
- 收到限流相关 msg 时退避重试（客户端已有指数退避重试）

## 7. 字段类型与取值

- `ts_code` 一律是带后缀字符串：`000001.SZ` / `600000.SH` / `000300.SH` / `000001.SH`
- 日期参数统一 `YYYYMMDD`（如 `20240101`）；返回的 `trade_date` 也是 `YYYYMMDD` 字符串
- 数值字段在 JSON 中可能是字符串也可能是数字，落盘后统一用 `to_dataframe()` 或 `pd.to_numeric(..., errors="coerce")` 转换
- 缺失值可能是 `null`、`""` 或 `0`，语义不同（如成交量为 0 表示停牌），不要一律当缺失
- **同一报告期可能返回多行**：`fina_indicator` 等财务接口对同一 `(ts_code, end_date)` 可能给出 2 条记录，大部分字段相同、个别派生字段一行为 `null`（实测 `600519.SH` / `20240930` 即如此，两行 `ann_date` 相同）。落盘后按 `(ts_code, end_date)` 去重，优先保留空值更少的一行，或按 `ann_date` 取最新
- 财务类接口多为**季频/年频**，注意 `period` 参数与公告日 `ann_date` 的区别（前者是报告期，后者是披露日）

## 8. 落盘

| 扩展名 | 说明 |
|---|---|
| `.csv` | UTF-8 with BOM（`utf-8-sig`），Excel 直接打开不乱码；`append=True` 追加且不重复写表头 |
| `.json` | 数组，缩进 2，`ensure_ascii=False`（不支持追加） |
| `.jsonl` | 每行一个对象，适合流式/大文件，支持追加 |
| `.parquet` | 需 `pandas` + `pyarrow`；列式压缩，大表首选（不支持追加） |

命名建议：`{接口}_{主体}_{区间}.{ext}`，例如 `daily_000001SZ_20240101_20241231.parquet`。

## 9. 最小可运行示例

```bash
export TUSHARE_MCP_TOKEN=<your_token>

# 1) 查文档
python3 scripts/tsdoc.py doc --name daily

# 2) 取数
python3 scripts/tsfetch.py call \
  --api daily \
  --params '{"ts_code":"000001.SZ","start_date":"20240101","end_date":"20240131"}' \
  --fields ts_code,trade_date,open,high,low,close \
  --out daily_000001SZ_202401.parquet
```
