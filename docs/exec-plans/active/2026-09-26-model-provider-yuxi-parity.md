# 模型供应商 Yuxi 对齐改造

状态：进行中
开始日期：2026-09-26

## 目标

学 Yuxi 做 MelonClaw 供应商+模型两层配置：23 家内置供应商（纯 chat）、供应商只 admin 可配、普通用户只能用自己的 Key、前端为搜索+已启用/未启用卡片+供应商弹窗+供应商内模型管理弹窗（含远端拉取一键启用）。`scripts/check.sh backend` 通过且现有模型测试按新权限矩阵更新通过。

## 背景

- Yuxi：单表 `model_providers.enabled_models JSON`，`builtin.py` 23 家，`ModelProviderManagePanel.vue` 为搜索+分组卡片+两层弹窗，前端 `PUT enabled_models` 整数组替换。
- MelonClaw 现状：两表 `model_providers + model_configs` 已落地（保留，不学 Yuxi 单表），但只有 deepseek 1 家种子，前端是两段式简陋表单（`ResourceView.tsx` ProviderManager/ModelManager），`window.prompt` 改 Key，无搜索分组、无弹窗、无一键导入。
- 用户决策（2026-09-26）：全量照搬 23 家但去掉 embedding/rerank 能力、保持纯 chat；供应商只能 admin 配置，用户不能自建供应商，只能在 admin 供应商基础上填自己的 Key 使用；前端要全套 Yuxi 交互。

## 步骤

- [x] 后端 schema：新增 `provider_user_keys(provider_key,user_id,api_key,updated_at)`，`model_providers/model_configs` 保持两表（可清空 DB，无兼容代码）
- [x] 种子：`seed_data.py PROVIDER_SEEDS` 扩到 23 家纯 chat（照搬 Yuxi builtin 去 embedding/rerank/capabilities），`MODEL_SEEDS` 保留 deepseek-flash/pro + 加 siliconflow-cn 3 个 chat 系统模型
- [x] 权限收紧：`resource_service.py` provider/model 创建/更新/删除/启停只 admin；`list_providers/list_models` 对普通用户只读 global + 附 `enabled_models_count/has_shared_key/has_my_key/effective_has_key`
- [x] 用户 Key：`resources.py + resource_service.py + chat.py + routes/models.py + schemas.py` 加 `PUT/DELETE /api/model-providers/{key}/my-key`，运行时解析优先级 用户Key > 共享Key
- [x] 远端拉取一键启用：`fetch_remote_models` 保持不落库，前端调 `POST /api/models` 完成启用；后端返回 `{id,display_name}` 不变
- [x] 前端重写 `ResourceView.tsx` ModelSection：搜索+已启用/未启用分组卡片+供应商弹窗（admin）+模型管理弹窗（admin 建/启停/设默认/删除+远端列表一键启用）+我的Key弹窗（所有用户）
- [x] 前端 `client.ts/types/api.ts` 补 `my-key` 接口与 `enabledModelsCount/hasMyKey` 类型
- [x] 测试更新：`test_model_configs.py`（admin-only 矩阵+用户Key覆盖）、`test_bootstrap.py`（23 家种子）、`test_schema.py`（新表）、`test_runtime_models.py`（有效Key解析）
- [x] 文档：`note/note.md` 记录，`docs/design-docs/custom-models.md` 重写数据模型/权限/运行步骤，`README.md` 补新增 API 与 env 变量，`docs/design-docs/index.md` 更新状态

## 验收标准

- `uv run pytest -q tests/test_model_configs.py tests/test_bootstrap.py tests/test_schema.py tests/test_runtime_models.py` 通过
- `uv run ruff check src tests` 通过
- `uv run melonclaw-db-init` 后 `GET /api/model-providers?user_id=admin` 返回 23 家，`siliconflow-cn/deepseek` 启用，其余停用
- admin 在 UI 新建供应商→管理模型→拉取远端→一键启用→设默认全流程可用；普通用户只能看到卡片+设我的Key，不能进编辑弹窗
- 普通用户设置我的Key后，`GET /api/models?user_id=xxx` 对应供应商模型 `available=true`，调用时用用户Key

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-09-26 | 保留两表，不学 Yuxi 单表 JSON | 两表已有外键/RESTRICT/版本缓存，查询与权限更清晰 |
| 2026-09-26 | 23 家全搬但只留 chat 字段 | 用户明确不要 embedding/rerank，后端仍只跑 openai_compatible |
| 2026-09-26 | 供应商+模型只 admin 可写，用户 Key 存新表 `provider_user_keys` | 用户要求供应商 admin 独占、用户用自己的 Key；新表避免 provider_key 唯一冲突 |
| 2026-09-26 | 种子默认仅 deepseek 启用，SiliconFlow 进未启用 | 用户明确默认供应商是 deepseek，Yuxi 的 siliconflow 默认不适用 |
| 2026-09-26 | 供应商/模型改回两级 scope：admin 配全局共享默认，用户可配私有，各人启用集合不一样 | 用户澄清：admin 启动的供应商是全员默认共享，个人可配自己的供应商和模型 |

## 风险与依赖

- DB 需清空重建（开发期允许）：`uv run melonclaw-db-init`，无迁移代码
- `.env` 需补 23 家 Key 占位（仅 `DEEPSEEK_API_KEY/SILICONFLOW_API_KEY` 有真实值，其余空即未配置）
- 前端 antd Modal + 卡片 CSS 在 `global.css` 追加，不动现有 skill/mcp 样式
