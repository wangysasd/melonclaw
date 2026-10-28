"""聊天环境配置：完整字面赋值先脱敏，模型只持有绑定当前消息的草稿引用。"""

import hashlib
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from melonclaw.services.environment import EnvironmentError, EnvironmentService, environment_scope

INTENT = re.compile(
    r"^(?:并|同时|另外|然后)?(?:请帮我|请|帮我|麻烦)?(?:新增|创建|设置|配置|保存|更新|替换|修改|删除|停用|禁用|启用|开启)(?:一个|我的|系统共享的|个人的|以下|永久|多个|这些|的|\s)*环境变量"
)
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_environment_chat(content, *, user_id, conversation_id, request_id):
    direct = bool(INTENT.match(content.lstrip()))
    first_line = next(iter(content.splitlines()), "")
    mixed = bool(re.search(r"(?:安装|更新|同步).{0,30}(?:技能|skills?)", content if direct else first_line, re.I))
    if not direct and not (mixed and any(INTENT.match(line.strip()) for line in content.splitlines())):
        return content, None
    # 独立赋值行或指令后的行内赋值；值按字面保存，不去引号、不展开。
    lines = content.splitlines()
    fences = [line for line in lines if re.fullmatch(r"\s*```(?:env|dotenv|sh|bash|text)?\s*", line, flags=re.I)]
    if len(fences) not in {0, 2}:
        raise EnvironmentError("请使用一个完整代码块或独立赋值行。")
    normalized = "\n".join(line for line in lines if not re.fullmatch(r"\s*```(?:env|dotenv|sh|bash|text)?\s*", line, flags=re.I))
    assignments = []
    redacted_lines = []
    for line in normalized.splitlines():
        line = line.strip()
        match = ASSIGNMENT.fullmatch(line)
        if not match:
            intent = INTENT.search(line)
            if intent:
                match = ASSIGNMENT.fullmatch(line[intent.end() :].lstrip(" ：:"))
        if match:
            assignments.append((match[1], match[2]))
            redacted_lines.append(line.replace(match[0], f"{match[1]}=[已安全提取]", 1))
        else:
            redacted_lines.append(line)
    if len(assignments) > 32:
        raise EnvironmentError("每条消息最多维护 32 项环境变量。")
    if len({name for name, _ in assignments}) != len(assignments):
        raise EnvironmentError("同一消息的变量名不能重复，请明确每项的最终值。")
    description = re.fullmatch(
        r"(?:请帮我|请|帮我)?修改环境变量\s+([A-Za-z_][A-Za-z0-9_]*)\s+的说明[：:](.*)",
        content.strip(),
    )
    if description:
        name = description[1]
        action, fields = "update", {"description": description[2]}
    elif assignments:
        if re.search(r"(?:删除|停用|禁用|启用|开启).{0,12}环境变量", content):
            raise EnvironmentError("启停或删除只需变量名，不应同时提供值。")
        name, value = assignments[0]
        action, fields = "save", {"value": value}
    else:
        match = re.fullmatch(
            r"(?:请|帮我|请帮我)?(删除|停用|禁用|启用|开启)环境变量[ ：:]*([A-Za-z_][A-Za-z0-9_]*)[。.!！]?",
            content.strip(),
        )
        if not match:
            raise EnvironmentError(
                "新增或替换请提供完整 KEY=value；启停或删除请使用“停用环境变量 KEY”等指令。"
            )
        verb, name = match.groups()
        action = "delete" if verb == "删除" else "update"
        fields = {} if action == "delete" else {"enabled": verb in {"启用", "开启"}}
    operations = ([{"name": key, "action": "save", "fields": {"value": value}}
                   for key, value in assignments] if assignments
                  else [{"name": name, "action": action, "fields": fields}])
    operation = operations[0]
    payload = {"operations": operations} if len(operations) > 1 else {"operation": operation}
    seed = json.dumps(
        [user_id, conversation_id, request_id, payload], sort_keys=True, ensure_ascii=False
    )
    try:
        seed_bytes = seed.encode("utf-8")
    except UnicodeEncodeError:
        raise EnvironmentError("输入包含无效文本编码，无法维护环境变量。") from None
    identifier = str(
        uuid5(NAMESPACE_URL, "melonclaw:environment:" + hashlib.sha256(seed_bytes).hexdigest())
    )
    names = "、".join(item["name"] for item in operations)
    clean = f"用户请求维护环境变量 {names}；值已安全提取，不得读取或复述。请调用 prepare_environment_change，draft_id={identifier}，再提交审批。"
    if mixed:
        # 先按赋值位置脱敏，避免短值（如 1）误伤 URL。正文重复出现的值也脱敏；
        # URL 内偶然相同的短词/数字不视为再次引用变量值。
        redacted = "\n".join(redacted_lines)
        urls = re.compile(r"https://[^\s<>]+")
        for _, value in sorted(assignments, key=lambda item: len(item[1]), reverse=True):
            if not value:
                continue
            if len(value) >= 8:
                redacted = redacted.replace(value, "[已安全提取]")
            else:
                parts = re.split(r"(https://[^\s<>]+)", redacted)
                redacted = "".join(part if urls.fullmatch(part) else re.sub(
                    rf"(?<![\w]){re.escape(value)}(?![\w])", "[已安全提取]", part,
                ) for part in parts)
        clean = redacted + "\n" + clean
    return clean, {"id": identifier, **payload}


class ChatEnvironmentService:
    def __init__(self, chat):
        self.chat = chat
        self.management = EnvironmentService(chat.runtime)

    async def stage(self, draft, context, conversation_id, request_id):
        operations = draft["operations"] if "operations" in draft else [draft["operation"]]
        rows = await self.management.storage.list_environment(context.user_id)
        scope = environment_scope(context)
        changes = []
        for operation in operations:
            existing = next(
                (row for row in rows if row["scope"] == scope and row["name"] == operation["name"]),
                None,
            )
            if operation["action"] != "save" and existing is None:
                raise EnvironmentError("当前维护范围没有该变量。")
            changes.append(await self.management.change(
                context.user_id,
                name=operation["name"],
                identifier=str(existing["id"]) if existing else None,
                expected_version=existing["version"] if existing else None,
                fields=operation["fields"],
                delete=operation["action"] == "delete",
                user=context,
            ))
        payload = changes[0] if len(changes) == 1 else {
            "scope": scope, "owner_user_id": changes[0]["owner_user_id"],
            "created_by": context.user_id, "changes": changes,
        }
        await self.management.storage.stage_environment(
            draft["id"], context.user_id, conversation_id, request_id, payload
        )

    async def _draft(self, context, identifier):
        if context is None or not context.conversation_id or not context.user_message_id:
            raise EnvironmentError("环境变量维护需要当前聊天上下文。")
        user = await self.management.identity(context.user_id)
        if user.tenant_id != context.tenant_id:
            raise EnvironmentError("用户所属租户已变化。")
        storage = self.management.storage
        conversation = await storage.get_conversation(UUID(context.conversation_id), user.user_id)
        if conversation is None or (conversation.get("project_id") or "") != (
            context.project_id or ""
        ):
            raise EnvironmentError("会话归属已变化。")
        draft = await storage.environment_operation(
            identifier, user.user_id, context.conversation_id
        )
        if draft["request_id"] != context.request_id:
            raise EnvironmentError("操作不属于当前消息。")
        scope = (draft["payload"] or draft["manifest"] or {}).get("scope")
        if scope != environment_scope(user):
            raise EnvironmentError("身份或操作范围已变化，请重新提交。")
        return storage, draft, user

    async def inspect(self, context):
        try:
            if context is None or not context.conversation_id:
                raise EnvironmentError("缺少聊天上下文。")
            user = await self.management.identity(context.user_id)
            conversation = await self.management.storage.get_conversation(
                UUID(context.conversation_id), user.user_id
            )
            if conversation is None or user.tenant_id != context.tenant_id:
                raise EnvironmentError("身份或会话无效。")
            return await self.management.list(user.user_id)
        except Exception:
            return {"status": "error", "message": "无法读取环境变量列表。"}

    async def prepare(self, context, draft_id):
        try:
            storage, draft, user = await self._draft(context, draft_id)
            if draft["status"] == "committed":
                return {"status": "committed", "result": draft["response"]}
            change = draft["payload"]
            def public_item(item):
                return {
                    "name": item["name"], "scope": item["scope"], "action": item["action"],
                    "expected_version": item["expected_version"],
                    "replaces_value": "value" in item["fields"],
                    "enabled": item["fields"].get("enabled"),
                    "description": item["fields"].get("description"),
                    "impact": "系统共享，影响使用该共享变量的所有用户"
                    if item["scope"] == "global" else "仅自己；停用或删除后自动使用可用共享配置",
                }
            manifest = {"draft_id": draft_id, **(
                {"scope": change["scope"], "changes": [public_item(item) for item in change["changes"]]}
                if "changes" in change else public_item(change)
            )}
            saved = await storage.environment_operation(
                draft_id, context.user_id, context.conversation_id, manifest=manifest
            )
            return {"status": "prepared", "change": saved["manifest"]}
        except Exception:
            return {
                "status": "error",
                "message": "无法准备环境变量审批，请核对输入、当前身份和操作有效期。",
            }

    async def confirm(self, context, change):
        try:
            async with self.management.storage.account_guard():
                return await self._confirm(context, change)
        except Exception:
            return {"status": "error", "message": "环境变量未提交，请核对当前身份与操作有效期。"}

    async def _confirm(self, context, change):
        try:
            storage, draft, user = await self._draft(context, change["draft_id"])
            if draft["manifest"] != change:
                raise EnvironmentError("审批清单已变化。")
            if draft["status"] == "committed":
                if draft["response"] is None:
                    raise EnvironmentError("回执已过期。")
                return {"status": "saved", **draft["response"]}
            payload = draft["payload"]
            # 再校验保留名、权限、目标版本，防止审批等待期间配置变化。
            for item in payload.get("changes", [payload]):
                checked = await self.management.change(
                    context.user_id,
                    name=item["name"], identifier=item["id"],
                    expected_version=item["expected_version"], fields=item["fields"],
                    delete=item["action"] == "delete", user=user,
                )
                if checked != item:
                    raise EnvironmentError("审批目标已变化。")
            result = await storage.commit_environment(
                change["draft_id"], context.user_id, context.conversation_id, change, payload
            )
            return {"status": "saved", **result, "message": "环境变量已保存，下一轮消息生效。"}
        except Exception:
            return {
                "status": "error",
                "message": "环境变量未提交；审批可能已失效或配置已变化，请刷新后重新提交。",
            }


async def reject_environment_decisions(storage, pending, command, user_id, conversation_id):
    """决定已由 HITL 校验；拒绝立即擦除敏感暂存，永久保留拒重状态。"""
    for item in pending:
        if not any(
            action.get("name") == "confirm_environment_change" for action in item["action_requests"]
        ):
            continue
        decisions = command.resume[str(item["id"])]["decisions"]
        for action, decision in zip(item["action_requests"], decisions):
            if action.get("name") == "confirm_environment_change" and decision["type"] == "reject":
                identifier = action.get("args", {}).get("change", {}).get("draft_id")
                if identifier:
                    await storage.reject_environment(identifier, user_id, conversation_id)


async def cleanup_environment_operation(storage, identifier, user_id, conversation_id):
    """取消时擦除暂存；清理故障不覆盖原异常，周期过期清理继续兜底。"""
    try:
        await storage.reject_environment(identifier, user_id, conversation_id)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("环境变量操作暂存未完成清理，等待过期清理。")
