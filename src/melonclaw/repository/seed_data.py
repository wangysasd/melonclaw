"""系统租户与管理员的种子数据。

初次上线只有一个用户：system 租户下的 admin（管理员）。
其他用户都是普通用户（member），由 admin 通过资源管理入口创建。
"""

from __future__ import annotations

from typing import Any

from melonclaw.repository.constants import SYSTEM_TENANT_ID

TENANT_SEEDS: tuple[dict[str, str], ...] = (
    {"tenant_id": SYSTEM_TENANT_ID, "tenant_name_zh": "系统"},
)

ADMIN_USER_SEED: dict[str, str] = {
    "user_id": "admin",
    "user_name_zh": "管理员",
    "tenant_id": SYSTEM_TENANT_ID,
    "tenant_role": "owner",
    "tenant_status": "active",
}

USER_SEEDS: tuple[dict[str, str], ...] = (ADMIN_USER_SEED,)

# ---------------------------------------------------------------------------
# 平台模型供应商模板
# ---------------------------------------------------------------------------

# 初始化只写供应商模板，不读取任何模型凭据、不创建模型。
# 清单照搬 Yuxi builtin（23 家）但只留 chat 所需字段：不含 embedding/rerank
# 能力与多 base_url；纯 openai_compatible。
PROVIDER_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "provider_key": "openai",
        "display_name": "OpenAI",
        "provider_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "models_endpoint": "https://api.openai.com/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "deepseek",
        "display_name": "DeepSeek",
        "provider_type": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "models_endpoint": "https://api.deepseek.com/models",
        "enabled": False,
    },
    {
        "provider_key": "alibaba-cn",
        "display_name": "DashScope",
        "provider_type": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "alibaba",
        "display_name": "DashScope (International)",
        "provider_type": "openai_compatible",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "models_endpoint": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "alibaba-coding-plan-cn",
        "display_name": "Aliyun Coding Plan",
        "provider_type": "openai_compatible",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "models_endpoint": "https://coding.dashscope.aliyuncs.com/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "alibaba-coding-plan",
        "display_name": "Aliyun Coding Plan (International)",
        "provider_type": "openai_compatible",
        "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
        "models_endpoint": "https://coding-intl.dashscope.aliyuncs.com/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "zhipuai",
        "display_name": "Zhipu (BigModel)",
        "provider_type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models_endpoint": "https://open.bigmodel.cn/api/paas/v4/models",
        "enabled": False,
    },
    {
        "provider_key": "zhipuai-coding-plan",
        "display_name": "Zhipu Coding Plan (BigModel)",
        "provider_type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "models_endpoint": "https://open.bigmodel.cn/api/coding/paas/v4/models",
        "enabled": False,
    },
    {
        "provider_key": "zai",
        "display_name": "Zhipu (Z.AI)",
        "provider_type": "openai_compatible",
        "base_url": "https://api.z.ai/api/paas/v4",
        "models_endpoint": "https://api.z.ai/api/paas/v4/models",
        "enabled": False,
    },
    {
        "provider_key": "zai-coding-plan",
        "display_name": "Zhipu Coding Plan (Z.AI)",
        "provider_type": "openai_compatible",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "models_endpoint": "https://api.z.ai/api/coding/paas/v4/models",
        "enabled": False,
    },
    {
        "provider_key": "xiaomi-token-plan-cn",
        "display_name": "XiaomiMiMo Token Plan",
        "provider_type": "openai_compatible",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "models_endpoint": "https://token-plan-cn.xiaomimimo.com/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "xiaomi",
        "display_name": "XiaomiMiMo",
        "provider_type": "openai_compatible",
        "base_url": "https://api.xiaomimimo.com/v1",
        "models_endpoint": "https://api.xiaomimimo.com/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "kimi-for-coding",
        "display_name": "Kimi Code",
        "provider_type": "openai_compatible",
        "base_url": "https://api.kimi.com/coding/v1",
        "models_endpoint": "https://api.kimi.com/coding/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "moonshotai-cn",
        "display_name": "Moonshot",
        "provider_type": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "models_endpoint": "https://api.moonshot.cn/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "moonshotai",
        "display_name": "Moonshot (International)",
        "provider_type": "openai_compatible",
        "base_url": "https://api.moonshot.ai/v1",
        "models_endpoint": "https://api.moonshot.ai/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "minimax-cn",
        "display_name": "MiniMax",
        "provider_type": "openai_compatible",
        "base_url": "https://api.minimaxi.com/v1",
        "models_endpoint": "https://api.minimaxi.com/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "minimax",
        "display_name": "MiniMax (International)",
        "provider_type": "openai_compatible",
        "base_url": "https://api.minimax.io/v1",
        "models_endpoint": "https://api.minimax.io/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "openrouter",
        "display_name": "OpenRouter",
        "provider_type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "models_endpoint": "https://openrouter.ai/api/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "modelscope",
        "display_name": "ModelScope",
        "provider_type": "openai_compatible",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "models_endpoint": "https://api-inference.modelscope.cn/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "opencode",
        "display_name": "OpenCode",
        "provider_type": "openai_compatible",
        "base_url": "https://opencode.ai/zen/v1",
        "models_endpoint": "https://opencode.ai/zen/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "opencode-go",
        "display_name": "OpenCode Go",
        "provider_type": "openai_compatible",
        "base_url": "https://opencode.ai/zen/go/v1",
        "models_endpoint": "https://opencode.ai/zen/go/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "siliconflow-cn",
        "display_name": "SiliconFlow",
        "provider_type": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "models_endpoint": "https://api.siliconflow.cn/v1/models",
        "enabled": False,
    },
    {
        "provider_key": "siliconflow",
        "display_name": "SiliconFlow (International)",
        "provider_type": "openai_compatible",
        "base_url": "https://api.siliconflow.com/v1",
        "models_endpoint": "https://api.siliconflow.com/v1/models",
        "enabled": False,
    },
)
