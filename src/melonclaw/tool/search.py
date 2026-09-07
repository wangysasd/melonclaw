"""使用 Tavily SDK 构造 DeepAgents 可调用的自定义搜索工具。"""

from __future__ import annotations

import os
from typing import Any, Literal

from tavily import TavilyClient


def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
) -> dict[str, Any]:
    """Run a web search with Tavily and return ranked web results."""

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("query 不能为空。")
    if len(clean_query) > 400:
        raise ValueError("query 不能超过 400 个字符，请拆分成长问题。")
    if not 0 <= max_results <= 20:
        raise ValueError("max_results 必须介于 0 和 20 之间。")

    tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    return tavily_client.search(
        query=clean_query,
        max_results=max_results,
        topic=topic,
        include_raw_content=include_raw_content,
    )


if __name__ == "__main__":

    test_query = "What is the capital of France?"

    result = internet_search(
            query=test_query,
            max_results=3,
            topic="general",
        )

    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))

