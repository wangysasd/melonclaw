"""把上传图片转换为模型出站请求可用的压缩图片，并按附件缓存结果。

hydration 会在一次 Agent run 的每次模型调用中执行；工具循环里同一张图片会被
反复读取和编码。这个模块只做两件事：

1. 按 ``(attachment_id, 文件 mtime, 文件大小)`` 缓存 base64 结果，避免重复读盘
   和编码；文件一旦被替换，指纹变化会自然失效旧缓存。
2. 对最长边超过上限的图片做等比缩放，控制每次出站请求的图片体积和 token 成本；
   小于上限的 PNG 和 JPEG 保持原始字节，其他格式以 PNG 输出。
"""

from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageOps

_LANCZOS = Image.Resampling.LANCZOS
_NATIVE_MEDIA_TYPES = {"image/png", "image/jpeg"}


class OutboundImageEncoder:
    """有界内存缓存的图片出站编码器。"""

    def __init__(
        self,
        *,
        max_edge: int,
        jpeg_quality: int,
        max_entries: int = 32,
    ) -> None:
        self.max_edge = max(1, max_edge)
        self.jpeg_quality = min(max(jpeg_quality, 1), 95)
        self.max_entries = max(1, max_entries)
        self._cache: OrderedDict[tuple[str, int, int], tuple[str, str]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def encode(
        self,
        attachment_id: UUID | str,
        source: Path,
        media_type: str,
    ) -> tuple[str, str]:
        """返回 ``(base64, 实际 media_type)``；同版本文件命中缓存。"""

        stat = await asyncio.to_thread(source.stat)
        key = (str(attachment_id), stat.st_mtime_ns, stat.st_size)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        payload, actual_type = await asyncio.to_thread(
            _encode_image,
            source,
            media_type,
            self.max_edge,
            self.jpeg_quality,
        )
        encoded = base64.b64encode(payload).decode("ascii")
        async with self._lock:
            self._cache[key] = (encoded, actual_type)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        return encoded, actual_type


def _encode_image(
    source: Path,
    media_type: str,
    max_edge: int,
    jpeg_quality: int,
) -> tuple[bytes, str]:
    with Image.open(source) as image:
        if media_type not in _NATIVE_MEDIA_TYPES:
            return _frame_png(image, max_edge), "image/png"
        image.load()
        width, height = image.size
        longest = max(width, height)
        if longest <= max_edge:
            return source.read_bytes(), media_type
        scale = max_edge / longest
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = image.resize(size, _LANCZOS)
        buffer = BytesIO()
        if media_type == "image/png":
            if resized.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                resized = resized.convert("RGBA")
            resized.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), "image/png"
        resized.convert("RGB").save(
            buffer,
            format="JPEG",
            quality=jpeg_quality,
            optimize=True,
        )
        return buffer.getvalue(), "image/jpeg"


def _frame_png(image: Image.Image, max_edge: int | None = None) -> bytes:
    frame = ImageOps.exif_transpose(image)
    if max_edge is not None:
        frame.thumbnail((max_edge, max_edge), _LANCZOS)
    if frame.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}:
        frame = frame.convert("RGBA")
    buffer = BytesIO()
    frame.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def first_frame_png(source: Path) -> bytes:
    with Image.open(source) as image:
        return _frame_png(image)


__all__ = ["OutboundImageEncoder", "first_frame_png"]
