"""把写操作追加到数据目录中的审计文件，不写业务表。"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from melonclaw.api.audit_alert import (
    classify_audit_failure,
    note_audit_write_recovered,
    report_audit_write_failure,
)
from melonclaw.core.config import load_settings

logger = logging.getLogger(__name__)

# 任意六个连续自然月最多 184 天，边界日期的文件仍保留。
RETENTION_DAYS = 184
DEVICE_COOKIE = "melonclaw_device"
_DEVICE_RE = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.Lock()
_PRUNED: dict[str, date] = {}
_TEXT_LIMITS = {
    "account": 64,
    "target_account": 64,
    "operation": 80,
    "route": 180,
    "result": 8,
    "source_ip": 64,
    "destination_address": 255,
    "user_agent": 300,
    "device_id": 32,
    "client_platform": 64,
}
_OPERATIONS = {
    "POST /api/auth/login": "登录",
    "POST /api/auth/login/sms": "短信登录",
    "POST /api/auth/register": "注册账号",
    "POST /api/auth/password/reset": "找回密码",
    "POST /api/auth/sms/challenges": "发送短信验证码",
    "POST /api/auth/phone/challenges": "发送绑定验证码",
    "POST /api/auth/phone/bind": "绑定手机号",
    "POST /api/auth/passwordless": "免密登录",
    "POST /api/auth/logout": "登出",
    "POST /api/auth/switch": "切换用户",
    "PATCH /api/auth/profile": "修改昵称",
    "POST /api/auth/password": "修改密码",
    "POST /api/admin/users": "创建用户",
    "PATCH /api/admin/users/{user_id}": "修改用户",
    "DELETE /api/admin/users/{user_id}": "删除用户",
    "POST /api/admin/users/{user_id}/password": "重置密码",
    "POST /api/admin/tenants": "创建租户",
    "PATCH /api/admin/tenants/{tenant_id}": "修改租户",
    "POST /api/reports/anonymous": "提交投诉",
    "POST /api/reports/lookup": "查询投诉",
    "POST /api/reports": "提交举报",
    "POST /api/admin/reports/{report_id}/accept": "受理投诉",
    "POST /api/admin/reports/{report_id}/message": "核查被举报回复",
    "POST /api/admin/reports/{report_id}/note": "记录投诉备注",
    "POST /api/admin/reports/{report_id}/reply": "回复投诉",
    "POST /api/admin/reports/{report_id}/resolve": "办结投诉",
    "POST /api/conversations": "创建会话",
    "POST /api/conversations/{conversation_id}/messages": "发送消息",
    "PATCH /api/conversations/{conversation_id}": "修改会话",
    "DELETE /api/conversations/{conversation_id}": "删除会话",
}


class SecurityAuditMiddleware:
    """写请求结束后追加一行。读取类请求只补设备标识，不记审计。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        device_id, fresh = resolve_device(scope)
        status = {"code": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message = _with_client_headers(message, scope, device_id, fresh)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
                _write(scope, status["code"], device_id)


def append_audit(
    fields: Mapping[str, object], directory: Path, *, today: date | None = None
) -> Path:
    """追加一行 JSON。未知字段丢弃，避免口令或令牌被记入。"""

    today = today or date.today()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    line = json.dumps(_record(fields), ensure_ascii=False, separators=(",", ":")) + "\n"
    path = directory / f"audit-{today.isoformat()}.jsonl"
    with _LOCK:
        _prune(directory, today)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
    return path


def resolve_device(scope) -> tuple[str, bool]:
    """返回设备标识，以及这次是否需要新发给浏览器。"""

    for part in _header(scope, b"cookie").split(";"):
        name, _, value = part.strip().partition("=")
        if name == DEVICE_COOKIE and _DEVICE_RE.fullmatch(value):
            return value, False
    return uuid4().hex, True


def describe_request(scope, status: int | None, device_id: str) -> dict[str, object]:
    """从当前请求提取审计字段。不读取正文。"""

    source_ip, source_port, destination, destination_port = endpoints(scope)
    route = _route(scope)
    state = scope.get("state")
    account = _state_text(state, "audit_account") or _user_id(state)
    target = _state_text(state, "audit_target") or str(
        scope.get("path_params", {}).get("user_id", "")
    )
    code = status if isinstance(status, int) else 0
    return {
        "account": account,
        "target_account": target,
        "operation": _OPERATIONS.get(route, route),
        "route": route,
        "result": "成功" if 200 <= code < 400 else "失败",
        "status": code,
        "source_ip": source_ip,
        "source_port": source_port,
        "destination_address": destination,
        "destination_port": destination_port,
        "user_agent": _header(scope, b"user-agent"),
        "device_id": device_id,
        "client_platform": _header(scope, b"sec-ch-ua-platform").strip('"'),
    }


def endpoints(scope) -> tuple[str, int | None, str, int | None]:
    """来源只在连接来自本机反代时采信 Nginx 传入的地址和端口。"""

    peer_host, peer_port = _peer(scope)
    trusted = _is_loopback(peer_host)
    source_ip = _ip(_header(scope, b"x-real-ip")) if trusted else _ip(peer_host)
    if trusted and not source_ip:
        source_ip = _ip(peer_host)
    source_port = _port(_header(scope, b"x-real-port")) if trusted else peer_port
    if trusted and source_port is None:
        source_port = peer_port
    if trusted and _header(scope, b"x-forwarded-host"):
        destination = _host(_header(scope, b"x-forwarded-host"))
        destination_port = _port(_header(scope, b"x-forwarded-port"))
    else:
        destination = _host(_header(scope, b"host"))
        destination_port = _server_port(scope)
    return source_ip, source_port, destination, destination_port


def _write(scope, status: int | None, device_id: str) -> None:
    try:
        directory = load_settings().audit_dir
        if directory is None:
            return
        append_audit(describe_request(scope, status, device_id), directory)
    except Exception as exc:
        category = classify_audit_failure(exc)
        logger.warning("安全审计日志写入失败 category=%s", category)
        report_audit_write_failure(category)
    else:
        note_audit_write_recovered()


def _with_client_headers(message, scope, device_id: str, fresh: bool):
    headers = list(message["headers"])
    if fresh:
        headers.append((b"set-cookie", _cookie(device_id, scope).encode()))
    if not any(key.lower() == b"accept-ch" for key, _ in headers):
        headers.append((b"accept-ch", b"Sec-CH-UA-Platform"))
    return {**message, "headers": headers}


def _cookie(device_id: str, scope) -> str:
    secure = _public_scheme(scope) == "https"
    parts = [
        f"{DEVICE_COOKIE}={device_id}",
        "HttpOnly",
        "Path=/",
        "SameSite=Lax",
        "Max-Age=34560000",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _public_scheme(scope) -> str:
    peer_host, _ = _peer(scope)
    if _is_loopback(peer_host):
        forwarded = _header(scope, b"x-forwarded-proto").split(",", 1)[0].strip().lower()
        if forwarded in {"http", "https"}:
            return forwarded
    return str(scope.get("scheme") or "http")


def _record(fields: Mapping[str, object]) -> dict[str, object]:
    record: dict[str, object] = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": _status(fields.get("status")),
        "source_port": _optional_port(fields.get("source_port")),
        "destination_port": _optional_port(fields.get("destination_port")),
    }
    for key, limit in _TEXT_LIMITS.items():
        record[key] = _clean(fields.get(key, ""), limit)
    if record["result"] not in {"成功", "失败"}:
        record["result"] = "失败"
    if record["device_id"] and not _DEVICE_RE.fullmatch(str(record["device_id"])):
        record["device_id"] = ""
    if record["source_ip"]:
        record["source_ip"] = _ip(str(record["source_ip"]))
    return record


def _prune(directory: Path, today: date) -> None:
    key = str(directory)
    if _PRUNED.get(key) == today:
        return
    cutoff = today - timedelta(days=RETENTION_DAYS)
    for path in directory.glob("audit-*.jsonl"):
        try:
            stamp = date.fromisoformat(path.stem.removeprefix("audit-"))
        except ValueError:
            continue
        if stamp < cutoff:
            path.unlink(missing_ok=True)
    _PRUNED[key] = today


def _route(scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path", "") or scope.get("path", "")
    return f"{scope.get('method', '')} {template}"


def _peer(scope) -> tuple[str, int | None]:
    client = scope.get("client")
    if not client:
        return "", None
    host = str(client[0])
    port = client[1] if len(client) > 1 else None
    return host, port if isinstance(port, int) else None


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""


def _state_value(state, name: str):
    if state is None:
        return None
    if isinstance(state, dict):
        return state.get(name)
    return getattr(state, name, None)


def _state_text(state, name: str) -> str:
    value = _state_value(state, name)
    return "" if value is None else str(value)


def _user_id(state) -> str:
    user = _state_value(state, "user")
    return str(getattr(user, "user_id", "") or "")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return ""


def _port(value: str) -> int | None:
    if value.isdigit():
        number = int(value)
        if 0 < number <= 65535:
            return number
    return None


def _optional_port(value: object) -> int | None:
    if isinstance(value, int) and 0 < value <= 65535:
        return value
    if isinstance(value, str):
        return _port(value)
    return None


def _status(value: object) -> int:
    if isinstance(value, int) and 0 <= value <= 599:
        return value
    return 0


def _host(value: str) -> str:
    value = value.strip()
    if value.startswith("[") and "]" in value:
        value = value[1 : value.find("]")]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    if not value or any(char in value for char in " \r\n/\\"):
        return ""
    return value[:255]


def _server_port(scope) -> int | None:
    server = scope.get("server")
    if server and len(server) > 1 and isinstance(server[1], int):
        return _optional_port(server[1])
    return None


def _clean(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text.replace("\r", " ").replace("\n", " ")[:limit]
