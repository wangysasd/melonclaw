"""短信账户流程的安全错误。不携带号码、验证码或供应商报文。"""

from __future__ import annotations


class SmsRequestError(Exception):
    def __init__(self, message: str, status: int = 422, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
