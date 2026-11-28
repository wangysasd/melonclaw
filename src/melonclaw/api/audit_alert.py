"""审计文件写失败时通知部署者配置的接收地址。

告警在后台发送，并受超时限制。一次持续故障在通知成功后不再重复发送；
通知本身失败时保留应用警告，并按间隔重试。写入恢复后，下一次故障可以重新告警。
状态只留在当前进程。告警不写审计文件，也不携带请求、用户内容或接收地址。
"""

from __future__ import annotations

import errno
import logging
import threading
import time
from datetime import datetime

import httpx

from melonclaw.core.config import load_settings

logger = logging.getLogger(__name__)

_SUGGESTIONS = {
    "directory": "确认审计目录的上级路径存在，且该路径本身不是文件。运行用户需要能够创建目录。",
    "permission": "确认运行用户可以写入审计目录和当天的日志文件。",
    "disk": "确认磁盘仍有空间，并且文件系统允许写入。",
    "io": "确认审计目录可以创建并写入。业务请求结果不受影响，目录恢复后下一次写入会重新记日志。",
}


class _AlertState:
    def __init__(self, generation: int = 0) -> None:
        self.failing = False
        self.notified = False
        self.last_attempt: float | None = None
        self.generation = generation


_LOCK = threading.Lock()
_state = _AlertState()
_pending: list[threading.Thread] = []


def _now() -> float:
    return time.monotonic()


def classify_audit_failure(exc: BaseException) -> str:
    """把写入异常归成固定类别。不返回异常原文。"""

    number = getattr(exc, "errno", None)
    if isinstance(exc, PermissionError) or number in {errno.EACCES, errno.EPERM}:
        return "permission"
    if number in {errno.ENOSPC, errno.EDQUOT, errno.EIO, errno.EROFS}:
        return "disk"
    if isinstance(exc, (FileNotFoundError, NotADirectoryError, FileExistsError)) or number in {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EEXIST,
        errno.EISDIR,
    }:
        return "directory"
    return "io"


def report_audit_write_failure(category: str) -> None:
    """在已记录应用警告后尝试通知。未配置接收地址时直接返回。"""

    try:
        _notify(category if category in _SUGGESTIONS else "io")
    except Exception:
        logger.warning("安全审计告警发送失败")


def note_audit_write_recovered() -> None:
    """写入成功后解除抑制，使下一次故障可以重新告警。"""

    global _state
    with _LOCK:
        if not _state.failing:
            return
        _state = _AlertState(_state.generation + 1)


def reset_audit_alerts() -> None:
    """清空进程内抑制状态。测试用来隔离前后用例。"""

    global _state
    with _LOCK:
        _state = _AlertState(_state.generation + 1)
        pending = list(_pending)
        _pending.clear()
    for thread in pending:
        thread.join(1)


def flush_audit_alerts(timeout: float = 5) -> bool:
    """等待已发出的告警线程结束。请求路径不调用它。"""

    with _LOCK:
        pending = list(_pending)
    finished = True
    for thread in pending:
        thread.join(timeout)
        finished = finished and not thread.is_alive()
    with _LOCK:
        _pending[:] = [thread for thread in _pending if thread.is_alive()]
    return finished


def _notify(category: str) -> None:
    settings = load_settings()
    url = settings.audit_alert_webhook_url
    if not url:
        return
    generation = _claim(settings.audit_alert_retry_seconds)
    if generation is None:
        return
    _start(_deliver, url, _payload(category), settings.audit_alert_timeout_seconds, generation)


def _claim(interval: float) -> int | None:
    moment = _now()
    with _LOCK:
        if not _state.failing:
            _state.failing = True
            _state.notified = False
            _state.last_attempt = moment
            return _state.generation
        if _state.notified:
            return None
        if _state.last_attempt is not None and moment - _state.last_attempt < interval:
            return None
        _state.last_attempt = moment
        return _state.generation


def _payload(category: str) -> dict[str, str]:
    if category not in _SUGGESTIONS:
        category = "io"
    return {
        "service": "MelonClaw",
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "category": category,
        "suggestion": _SUGGESTIONS[category],
    }


def _start(target, url: str, payload: dict[str, str], timeout: float, generation: int) -> None:
    def run() -> None:
        target(url, payload, timeout, generation)

    thread = threading.Thread(target=run, name="melonclaw-audit-alert", daemon=True)
    with _LOCK:
        _pending.append(thread)
    thread.start()


def _deliver(url: str, payload: dict[str, str], timeout: float, generation: int) -> None:
    delivered = False
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.post(url, json=payload)
        delivered = 200 <= response.status_code < 300
    except Exception:
        delivered = False
    if delivered:
        _mark_notified(generation)
        return
    logger.warning("安全审计告警发送失败")


def _mark_notified(generation: int) -> None:
    with _LOCK:
        if _state.generation == generation and _state.failing:
            _state.notified = True
