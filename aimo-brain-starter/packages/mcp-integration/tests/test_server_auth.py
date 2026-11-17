from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp_integration.server_auth import UNAUTHORIZED_PREFIX, require_tool_scope

from identity import issue_service_credential

_SECRET = "test-signing-secret"


@dataclass
class _FakeRequest:
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _FakeRequestContext:
    request: _FakeRequest | None


@dataclass
class _FakeContext:
    request_context: _FakeRequestContext


def _ctx(authorization: str | None) -> _FakeContext:
    headers = {} if authorization is None else {"authorization": authorization}
    return _FakeContext(request_context=_FakeRequestContext(request=_FakeRequest(headers=headers)))


def test_require_tool_scope_accepts_valid_credential() -> None:
    token = issue_service_credential(
        organization_id="org_a", scope="use_buyer_tools", signing_secret=_SECRET
    )
    claims = require_tool_scope(
        _ctx(f"Bearer {token}"),
        signing_secret=_SECRET,
        required_scope="use_buyer_tools",
        required_organization_id="org_a",
    )
    assert claims.organization_id == "org_a"


def test_require_tool_scope_rejects_missing_header() -> None:
    with pytest.raises(ToolError) as exc_info:
        require_tool_scope(
            _ctx(None),
            signing_secret=_SECRET,
            required_scope="use_buyer_tools",
            required_organization_id="org_a",
        )
    assert str(exc_info.value).startswith(UNAUTHORIZED_PREFIX)


def test_require_tool_scope_rejects_wrong_scope() -> None:
    token = issue_service_credential(
        organization_id="org_a", scope="use_supplier_tools", signing_secret=_SECRET
    )
    with pytest.raises(ToolError):
        require_tool_scope(
            _ctx(f"Bearer {token}"),
            signing_secret=_SECRET,
            required_scope="use_buyer_tools",
            required_organization_id="org_a",
        )


def test_require_tool_scope_rejects_wrong_organization() -> None:
    token = issue_service_credential(
        organization_id="org_b", scope="use_buyer_tools", signing_secret=_SECRET
    )
    with pytest.raises(ToolError):
        require_tool_scope(
            _ctx(f"Bearer {token}"),
            signing_secret=_SECRET,
            required_scope="use_buyer_tools",
            required_organization_id="org_a",
        )


def test_require_tool_scope_rejects_expired_credential() -> None:
    token = issue_service_credential(
        organization_id="org_a",
        scope="use_buyer_tools",
        signing_secret=_SECRET,
        ttl_seconds=10,
        now=1_000_000,
    )
    with pytest.raises(ToolError):
        require_tool_scope(
            _ctx(f"Bearer {token}"),
            signing_secret=_SECRET,
            required_scope="use_buyer_tools",
            required_organization_id="org_a",
        )
