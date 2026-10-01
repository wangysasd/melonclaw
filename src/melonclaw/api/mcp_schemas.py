"""MCP 请求契约；拒绝客户端归属字段，不回显验证输入。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Value = Annotated[str, Field(max_length=8192)]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    user_id: str = Field(min_length=1, max_length=64)


class CredentialPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    set: dict[str, Value] = Field(default_factory=dict, max_length=64)
    remove: list[str] = Field(default_factory=list, max_length=64)
    clear: bool = False


class McpCreateRequest(StrictRequest):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    transport: Literal["http", "sse", "stdio"]
    url: str | None = Field(default=None, max_length=2000)
    command: str | None = Field(default=None, max_length=240)
    args: list[Value] = Field(default_factory=list, max_length=64)
    env: CredentialPatch = Field(default_factory=CredentialPatch)
    headers: CredentialPatch = Field(default_factory=CredentialPatch)
    tool_allowlist: list[Value] | None = Field(default=None, max_length=200)


class McpUpdateRequest(StrictRequest):
    version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    transport: Literal["http", "sse", "stdio"] | None = None
    url: str | None = Field(default=None, max_length=2000)
    command: str | None = Field(default=None, max_length=240)
    args: list[Value] | None = Field(default=None, max_length=64)
    env: CredentialPatch | None = None
    headers: CredentialPatch | None = None
    tool_allowlist: list[Value] | None = Field(default=None, max_length=200)


class McpVersionRequest(StrictRequest):
    version: int = Field(ge=1)


class McpStateRequest(McpVersionRequest):
    enabled: bool


class McpPreferenceRequest(StrictRequest):
    enabled: bool


class McpTestRequest(McpCreateRequest):
    base_id: str | None = None
    version: int | None = Field(default=None, ge=1)


class McpDiscoveryRequest(McpVersionRequest):
    background: bool = False
    refresh: bool = False
