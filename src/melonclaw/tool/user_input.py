"""主 Agent 使用的结构化用户提问工具。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt

USER_INPUT_TOOL_NAME = "ask_user"
USER_INPUT_KIND = "user_question"
# 问题卡片只有一种形状：一张卡片就是一组问题，单题是长度为一的那一组。
# 早期还有过顶层单字段的 v1 卡片，已经没有代码会再生成它。
USER_INPUT_SCHEMA_VERSION = 2
OPTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 用户主动跳过，或问题过期后由服务端代答；Agent 收到后自行收尾，不再追问。
USER_INPUT_ANSWER_CANCELLED = "cancelled"
USER_INPUT_CANCELLED_TEXT = "用户跳过了这个问题，请自行决定后续步骤。"

_MAX_QUESTION_LENGTH = 2_000
_MAX_QUESTION_COUNT = 6
_MAX_OPTION_COUNT = 6
_MAX_OPTION_LABEL_LENGTH = 120
_MAX_OPTION_DESCRIPTION_LENGTH = 500
_MAX_OPTION_ID_LENGTH = 64
_MAX_CUSTOM_ANSWER_LENGTH = 4_000


def _clean_text(value: Any, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串。")
    result = value.strip()
    if not result:
        raise ValueError(f"{field_name} 不能为空。")
    if len(result) > maximum:
        raise ValueError(f"{field_name} 不能超过 {maximum} 个字符。")
    return result


def _normalize_question(
    raw_question: Mapping[str, Any],
    *,
    index: int,
    used_ids: set[str],
) -> dict[str, Any]:
    """校验并规范化卡片里的单个问题。"""

    if not isinstance(raw_question, Mapping):
        raise ValueError(f"第 {index} 个问题必须是对象。")
    unknown = set(raw_question) - {
        "question",
        "options",
        "allow_custom_answer",
        "multi_select",
        "id",
    }
    if unknown:
        raise ValueError(f"第 {index} 个问题包含未知字段。")
    question = _clean_text(
        raw_question.get("question"),
        field_name=f"第 {index} 个问题的 question",
        maximum=_MAX_QUESTION_LENGTH,
    )
    allow_custom_answer = raw_question.get("allow_custom_answer", False)
    if not isinstance(allow_custom_answer, bool):
        raise ValueError("allow_custom_answer 必须是布尔值。")
    multi_select = raw_question.get("multi_select", False)
    if not isinstance(multi_select, bool):
        raise ValueError("multi_select 必须是布尔值。")
    raw_options = raw_question.get("options")
    if raw_options is None:
        raw_options = []
    if not isinstance(raw_options, list):
        raise ValueError("options 必须是数组。")
    if len(raw_options) > _MAX_OPTION_COUNT:
        raise ValueError(f"options 最多只能有 {_MAX_OPTION_COUNT} 个选项。")
    if not raw_options and not allow_custom_answer:
        raise ValueError("没有选项时必须允许自定义回答。")
    if raw_options and len(raw_options) < 2:
        raise ValueError("options 至少需要 2 个可区分的选项。")
    if multi_select and not raw_options:
        raise ValueError("多选问题必须提供选项。")

    normalized_options: list[dict[str, str]] = []
    option_ids: set[str] = set()
    for option_index, raw_option in enumerate(raw_options, start=1):
        if not isinstance(raw_option, Mapping):
            raise ValueError(f"第 {option_index} 个选项必须是对象。")
        unknown = set(raw_option) - {"id", "label", "description"}
        if unknown:
            raise ValueError(f"第 {option_index} 个选项包含未知字段。")
        option_id = _clean_text(
            raw_option.get("id"),
            field_name=f"第 {option_index} 个选项的 id",
            maximum=_MAX_OPTION_ID_LENGTH,
        )
        if OPTION_ID_PATTERN.fullmatch(option_id) is None:
            raise ValueError(
                f"第 {option_index} 个选项的 id 只能包含字母、数字、下划线和短横线。"
            )
        if option_id in option_ids:
            raise ValueError("选项 id 不能重复。")
        option_ids.add(option_id)
        label = _clean_text(
            raw_option.get("label"),
            field_name=f"第 {option_index} 个选项的 label",
            maximum=_MAX_OPTION_LABEL_LENGTH,
        )
        description = raw_option.get("description")
        normalized: dict[str, str] = {"id": option_id, "label": label}
        if description is not None:
            normalized["description"] = _clean_text(
                description,
                field_name=f"第 {option_index} 个选项的 description",
                maximum=_MAX_OPTION_DESCRIPTION_LENGTH,
            )
        normalized_options.append(normalized)

    question_id = raw_question.get("id", f"question-{index}")
    question_id = _clean_text(
        question_id,
        field_name=f"第 {index} 个问题的 id",
        maximum=_MAX_OPTION_ID_LENGTH,
    )
    if OPTION_ID_PATTERN.fullmatch(question_id) is None:
        raise ValueError(
            f"第 {index} 个问题的 id 只能包含字母、数字、下划线和短横线。"
        )
    if question_id in used_ids:
        raise ValueError("问题 id 不能重复。")
    used_ids.add(question_id)
    return {
        "id": question_id,
        "question": question,
        "options": normalized_options,
        "allow_custom_answer": allow_custom_answer,
        "multi_select": multi_select,
    }


def normalize_user_question_batch(
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """校验一张卡片里的全部问题，返回可放入 checkpoint 的 JSON。

    单题就是长度为一的数组，协议里没有别的卡片形状。
    """

    if not isinstance(questions, list) or not questions:
        raise ValueError("questions 必须是非空数组。")
    if len(questions) > _MAX_QUESTION_COUNT:
        raise ValueError(f"questions 最多只能有 {_MAX_QUESTION_COUNT} 个问题。")

    normalized_questions: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw_question in enumerate(questions, start=1):
        normalized_questions.append(
            _normalize_question(raw_question, index=index, used_ids=used_ids)
        )
    return {
        "kind": USER_INPUT_KIND,
        "schema_version": USER_INPUT_SCHEMA_VERSION,
        "questions": normalized_questions,
    }


def _normalize_answer(
    question_payload: Mapping[str, Any],
    answer: Any,
) -> dict[str, Any]:
    """按卡片里的单个问题校验答案，并保留服务端可信的选项文本。"""

    if not isinstance(answer, Mapping):
        raise ValueError("用户答案必须是对象。")
    if set(answer) - {"type", "option_id", "option_ids", "text"}:
        raise ValueError("用户答案包含未知字段。")
    answer_type = answer.get("type")
    if answer_type == USER_INPUT_ANSWER_CANCELLED:
        if set(answer) - {"type"}:
            raise ValueError("取消回答不支持其他字段。")
        return {"type": USER_INPUT_ANSWER_CANCELLED, "text": USER_INPUT_CANCELLED_TEXT}
    options = question_payload.get("options")
    if not isinstance(options, list):
        raise ValueError("当前问题选项格式无效。")

    if answer_type == "option":
        if question_payload.get("multi_select"):
            raise ValueError("多选问题必须提交 option_ids。")
        option_id = answer.get("option_id")
        if not isinstance(option_id, str) or not option_id:
            raise ValueError("选项答案必须提供 option_id。")
        selected = next(
            (item for item in options if isinstance(item, Mapping) and item.get("id") == option_id),
            None,
        )
        if selected is None:
            raise ValueError("提交的选项不属于当前问题。")
        label = selected.get("label")
        if not isinstance(label, str):
            raise ValueError("当前问题选项格式无效。")
        return {"type": "option", "option_id": option_id, "text": label}

    if answer_type == "options":
        if not question_payload.get("multi_select"):
            raise ValueError("单选问题不能提交 option_ids。")
        raw_option_ids = answer.get("option_ids")
        custom_text = answer.get("text")
        if not isinstance(raw_option_ids, list):
            raise ValueError("多选答案中的 option_ids 必须是数组。")
        if not raw_option_ids and custom_text is None:
            raise ValueError("多选答案至少需要选择一个选项或填写自定义回答。")
        if len(raw_option_ids) > len(options):
            raise ValueError("多选答案包含过多选项。")
        if any(not isinstance(option_id, str) or not option_id for option_id in raw_option_ids):
            raise ValueError("多选答案中的 option_ids 必须是非空字符串。")
        if len(set(raw_option_ids)) != len(raw_option_ids):
            raise ValueError("多选答案不能重复选择同一个选项。")
        selected: list[Mapping[str, Any]] = []
        for option_id in raw_option_ids:
            option = next(
                (
                    item
                    for item in options
                    if isinstance(item, Mapping) and item.get("id") == option_id
                ),
                None,
            )
            if option is None:
                raise ValueError("提交的选项不属于当前问题。")
            selected.append(option)
        labels = [item.get("label") for item in selected]
        if any(not isinstance(label, str) for label in labels):
            raise ValueError("当前问题选项格式无效。")
        normalized = {
            "type": "options",
            "option_ids": list(raw_option_ids),
            "text": "、".join(labels),
        }
        if custom_text is not None:
            if not question_payload.get("allow_custom_answer"):
                raise ValueError("当前问题不允许自定义回答。")
            normalized["custom_text"] = _clean_text(
                custom_text,
                field_name="text",
                maximum=_MAX_CUSTOM_ANSWER_LENGTH,
            )
        return normalized

    if answer_type == "text":
        if not question_payload.get("allow_custom_answer"):
            raise ValueError("当前问题不允许自定义回答。")
        text = _clean_text(
            answer.get("text"),
            field_name="text",
            maximum=_MAX_CUSTOM_ANSWER_LENGTH,
        )
        return {"type": "text", "option_id": None, "text": text}

    raise ValueError("用户答案 type 只能是 option、options、text 或 cancelled。")


def normalize_user_answer_batch(
    question_payload: Mapping[str, Any],
    answer: Any,
) -> dict[str, Any]:
    """校验一张问题卡片的全部答案，并保留服务端可信选项文本。"""

    if (
        isinstance(answer, Mapping)
        and answer.get("type") == USER_INPUT_ANSWER_CANCELLED
    ):
        if set(answer) - {"type"}:
            raise ValueError("取消回答不支持其他字段。")
        return {"type": USER_INPUT_ANSWER_CANCELLED, "text": USER_INPUT_CANCELLED_TEXT}
    questions = question_payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("问题卡片缺少 questions。")
    if not isinstance(answer, Mapping):
        raise ValueError("批量用户答案必须是对象。")
    if set(answer) - {"type", "answers"}:
        raise ValueError("批量用户答案包含未知字段。")
    if answer.get("type") != "batch":
        raise ValueError("多个问题必须提交 type=batch 的答案。")
    answers = answer.get("answers")
    if not isinstance(answers, Mapping):
        raise ValueError("批量用户答案中的 answers 必须是对象。")

    normalized_questions = [
        item for item in questions if isinstance(item, Mapping)
    ]
    question_ids = [str(item.get("id", "")) for item in normalized_questions]
    if len(normalized_questions) != len(questions) or any(not item_id for item_id in question_ids):
        raise ValueError("问题缺少稳定 ID。")
    if set(answers) != set(question_ids):
        raise ValueError("必须回答问题卡片中的全部问题，且不能包含未知问题。")

    normalized: dict[str, Any] = {}
    for question in normalized_questions:
        question_id = str(question["id"])
        normalized[question_id] = _normalize_answer(
            question,
            answers[question_id],
        )
    return {"type": "batch", "answers": normalized}


@tool(USER_INPUT_TOOL_NAME)
def ask_user(
    question: str | None = None,
    options: list[dict[str, Any]] | None = None,
    allow_custom_answer: bool = False,
    multi_select: bool = False,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """在需要用户决定时暂停当前 Agent，并展示一张单问题或多问题卡片。

    仅当缺少会显著改变结果的关键信息、存在多个合理方案，或 Skill 明确要求
    用户选择时使用。信息充分时自行决策，不要把普通流程确认变成提问。
    需要多个相互独立的信息时，一次传入 questions，不要连续调用多次 ask_user。
    questions 示例：[{"question": "部署环境？", "options": [{"id": "staging", "label": "测试"}, {"id": "prod", "label": "生产"}]}]。
    单问题模式下，options 为空时必须设置 allow_custom_answer=true；
    multi_select=true 时用户可以选择多个选项，并可在允许时附加自定义文本。
    """

    if questions is not None:
        if (
            question is not None
            or options is not None
            or allow_custom_answer
            or multi_select
        ):
            raise ValueError("questions 不能和单问题参数同时使用。")
        payload = normalize_user_question_batch(questions)
    else:
        if question is None:
            raise ValueError("单问题模式必须提供 question。")
        payload = normalize_user_question_batch(
            [
                {
                    "question": question,
                    "options": options,
                    "allow_custom_answer": allow_custom_answer,
                    "multi_select": multi_select,
                }
            ]
        )
    answer = interrupt(payload)
    return {"question": payload, "answer": answer}
