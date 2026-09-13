"""在可终止的独立进程中运行附件解析器。"""

from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
from queue import Empty
from typing import Any

from melonclaw.parsers.documents import DocumentParseError, parse_document


class ParserTimeoutError(TimeoutError):
    """解析进程超过 wall-clock 限制。"""


def _parse_worker(
    result_queue: Any,
    source: str,
    output_dir: str,
    max_chars: int,
    original_name: str | None,
) -> None:
    try:
        result = parse_document(
            Path(source),
            Path(output_dir),
            max_chars=max_chars,
            original_name=original_name,
        )
    except DocumentParseError as exc:
        result_queue.put(("error", exc.error_code, str(exc)))
    except Exception:
        # 子进程只回传稳定错误码，不把第三方库路径或堆栈带回 Web 层。
        result_queue.put(("error", "attachment_parse_failed", "附件解析失败。"))
    else:
        result_queue.put(("ok", result.character_count, result.files))


async def parse_document_isolated(
    source: Path,
    output_dir: Path,
    *,
    max_chars: int,
    timeout_seconds: int,
    original_name: str | None = None,
) -> None:
    """运行解析子进程，超时后终止而不是留下仍在写临时目录的线程。"""

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_parse_worker,
        args=(result_queue, str(source), str(output_dir), max_chars, original_name),
        daemon=True,
    )
    process.start()
    try:
        await asyncio.to_thread(process.join, timeout_seconds)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 1)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 1)
            raise ParserTimeoutError("附件解析超时。")
        try:
            result = await asyncio.to_thread(result_queue.get, True, 1)
        except Empty as exc:
            raise DocumentParseError("附件解析进程未返回结果。") from exc
        if result[0] == "error":
            raise DocumentParseError(str(result[2]), str(result[1]))
    finally:
        result_queue.close()
        result_queue.join_thread()
