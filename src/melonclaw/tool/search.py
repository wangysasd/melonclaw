"""使用 DeepSeek Search & Fetch API 构造内置联网搜索与网页读取工具。"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from melonclaw.tool.deepseek import (
    DEEPSEEK_FETCH_URL,
    DEEPSEEK_SEARCH_URL,
    DeepSeekResponseError,
    request_json,
    validate_response,
)

FETCH_MAX_CONTENT_CHARS = 32_000


class InternetSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    query: str = Field(
        min_length=1, max_length=128,
        description=(
            "搜索查询词（1~128 个字符，不能为空白）。支持操作符："
            "site:domain、-site:domain、\"精确短语\"、-排除词、"
            "lang:zh,en、after:YYYY-MM-DD、before:YYYY-MM-DD。"
        ),
    )
    count: int = Field(
        default=5, ge=1, le=20,
        description="返回结果条数，介于 1 和 20 之间，默认 5。",
    )
    ori_question: str | None = Field(
        default=None, min_length=1, max_length=128,
        description="可选：触发本次搜索的用户原始问题（1~128 个字符），有助于提升检索意图理解。",
    )
    need_content: bool = Field(
        default=False,
        description="是否直接附带网页正文 content；默认 false（返回 summary 摘要，需精读单页全文时优先调用 web_fetch）。",
    )


class WebFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    url: str = Field(
        min_length=1, max_length=8192,
        description="要读取的公开网页 HTTP/HTTPS URL（最长 8192 字符，不得包含用户名、密码或内网地址）。",
    )


def _resolve_api_key(api_key: str | None = None) -> str:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("未配置 DEEPSEEK_API_KEY，无法调用 DeepSeek 联网搜索或网页读取工具。")
    return key


def _format_deepseek_error(action: str, status_code: int | None = None) -> str:
    """将 HTTP 状态转换为脱敏的错误说明，不回显请求头或凭据。"""

    if status_code == 400:
        return f"{action}请求参数无效，请检查查询长度（<=128 字符）或 URL 格式。"
    if status_code == 401:
        return f"{action}鉴权失败，请检查 DEEPSEEK_API_KEY 配置。"
    if status_code == 402:
        return f"DeepSeek 账户余额不足，无法执行{action}。"
    if status_code == 403:
        return f"{action}请求被目标站点或安全策略拒绝。"
    if status_code == 404:
        return "无法获取该网页的可用正文内容（网页不可达、被站点策略拦截或无正文）。"
    if status_code in {408, 504}:
        return f"{action}请求超时，请稍后重试。"
    if status_code == 413:
        return f"{action}请求体过大。"
    if status_code == 429:
        return f"{action}触发频率限制（QPS 超限），请稍后重试。"
    return f"{action}服务暂时不可用，请稍后重试。"


def validate_public_http_url(url: str) -> str:
    """校验公开 HTTP/HTTPS URL，拒绝凭据、本地回环与私网地址。"""

    if not isinstance(url, str):
        raise ValueError("url 必须是字符串。")
    clean_url = url.strip()
    if not clean_url:
        raise ValueError("url 不能为空。")
    if len(clean_url) > 8192:
        raise ValueError("url 不能超过 8192 个字符。")

    try:
        parsed = urlsplit(clean_url)
    except ValueError as exc:
        raise ValueError("url 格式无效。") from exc

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url 必须是包含主机名的 http 或 https 地址。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url 不能包含用户名或密码。")

    host = parsed.hostname.lower().rstrip(".")
    if (
        host == "localhost"
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        raise ValueError("url 不能指向本地或内部主机。")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return clean_url

    if (
        not ip.is_global
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("url 不能指向内网、回环或保留 IP 地址。")
    return clean_url


def _normalize_search_item(item: dict[str, Any], *, need_content: bool) -> dict[str, Any]:
    limits = {"title": 500, "snippet": 500, "summary": 2000, "site_name": 200, "date": 64}
    if need_content:
        limits["content"] = 8000
    normalized = {key: item[key][:limit] if item[key] is not None else None
                  for key, limit in limits.items()}
    normalized.update(
        url=item["url"], rerank_score=item["rerank_score"], authority=item["authority"],
        truncated_fields=[key for key, limit in limits.items()
                          if item[key] is not None and len(item[key]) > limit],
    )
    return normalized


def internet_search(
    query: str,
    count: int = 5,
    ori_question: str | None = None,
    need_content: bool = False,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """使用 DeepSeek Search API 搜索互联网网页，返回带重排分、权威度和长摘要的结构化结果。"""

    payload = InternetSearchInput(
        query=query, count=count, ori_question=ori_question, need_content=need_content,
    ).model_dump(exclude_none=True)

    resolved_key = _resolve_api_key(api_key)
    try:
        data = request_json(DEEPSEEK_SEARCH_URL, payload, resolved_key)
    except DeepSeekResponseError:
        raise
    except httpx.HTTPStatusError as exc:
        status_code = getattr(exc.response, "status_code", None)
        raise RuntimeError(_format_deepseek_error("网页搜索", status_code)) from None
    except httpx.TimeoutException:
        raise RuntimeError(_format_deepseek_error("网页搜索", 408)) from None
    except Exception:
        raise RuntimeError(_format_deepseek_error("网页搜索")) from None

    raw_results = validate_response(data)["results"]
    results = [
        _normalize_search_item(item, need_content=need_content)
        for item in raw_results[:count]
    ]
    return {"results": results}


def web_fetch(
    url: str,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """使用 DeepSeek Fetch API 读取单个公开网页，返回标题与 Markdown 正文。"""

    clean_url = validate_public_http_url(url)
    resolved_key = _resolve_api_key(api_key)
    try:
        data = request_json(DEEPSEEK_FETCH_URL, {"url": clean_url}, resolved_key)
    except DeepSeekResponseError:
        raise
    except httpx.HTTPStatusError as exc:
        status_code = getattr(exc.response, "status_code", None)
        raise RuntimeError(_format_deepseek_error("网页读取", status_code)) from None
    except httpx.TimeoutException:
        raise RuntimeError(_format_deepseek_error("网页读取", 408)) from None
    except Exception:
        raise RuntimeError(_format_deepseek_error("网页读取")) from None

    page = validate_response(data, fetch=True)
    title_str, content_str = page["title"], page["content"]
    return {
        "url": clean_url,
        "title": title_str[:500],
        "content": content_str[:FETCH_MAX_CONTENT_CHARS],
        "content_truncated": len(content_str) > FETCH_MAX_CONTENT_CHARS,
    }


def build_web_tools(api_key: str | None = None) -> list[BaseTool]:
    """构造绑定配置凭据的 internet_search 与 web_fetch 工具。"""

    bound_key = (api_key or "").strip()

    def run_search(
        query: str,
        count: int = 5,
        ori_question: str | None = None,
        need_content: bool = False,
    ) -> dict[str, Any]:
        return internet_search(
            query=query,
            count=count,
            ori_question=ori_question,
            need_content=need_content,
            api_key=bound_key,
        )

    def run_fetch(url: str) -> dict[str, Any]:
        return web_fetch(url=url, api_key=bound_key)

    return [
        StructuredTool.from_function(
            func=run_search,
            name="internet_search",
            description=(
                "使用 DeepSeek 搜索引擎检索互联网公开网页，返回标题、链接、发布时间、"
                "权威度、相关性分及摘要（summary）。支持 site:、-site:、"
                "\"精确短语\"、lang:、after:、before: 等搜索语法，query 最长 128 字符。truncated_fields 标记被截断的字段；需全文时调用 web_fetch。"
            ),
            args_schema=InternetSearchInput,
        ),
        StructuredTool.from_function(
            func=run_fetch,
            name="web_fetch",
            description=(
                "使用 DeepSeek Fetch 接口读取单个公开 HTTP/HTTPS 网页，返回网页标题与"
                "提取好的 Markdown 正文。适合在搜索后深入阅读某条具体链接，或读取用户提供的网页链接。"
            ),
            args_schema=WebFetchInput,
        ),
    ]
