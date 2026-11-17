"""Server-side scoped-credential check, shared by every AIMO BRAIN MCP
server (M3).

Reuses ``packages/identity``'s HMAC service credentials (issued by
network-api, see ``docs/DECISIONS.md`` ADR-008/ADR-009) rather than
inventing a second identity mechanism. A credential grants its bearer
exactly one scope for exactly one organization; an MCP server deployment
belongs to exactly one organization and exposes exactly one scope, so a
credential minted for the wrong org or the wrong tool set is rejected
here, never by per-tool logic.

Raising :class:`mcp.server.fastmcp.exceptions.ToolError` (rather than
returning an HTTP status) is deliberate: the official MCP SDK turns any
exception raised inside a tool function into an ``isError`` tool result
(see ``mcp.server.lowlevel.server.Server.call_tool``'s handler), which is
the correct wire-level shape for "this specific tool call was rejected" —
the transport-level HTTP response itself stays 200, matching how every
other tool failure (bad arguments, business error) is reported.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from identity import ServiceCredentialClaims, ServiceCredentialError, verify_service_credential

#: Every rejection raised here starts with this exact prefix so
#: ``mcp_integration.client`` can distinguish "the server rejected the
#: caller" from an ordinary business-logic tool error, without leaking the
#: credential or signing secret into the message.
UNAUTHORIZED_PREFIX = "Unauthorized:"


def require_tool_scope(
    ctx: Context,
    *,
    signing_secret: str,
    required_scope: str,
    required_organization_id: str,
) -> ServiceCredentialClaims:
    """Verify the bearer service credential on the current tool call's
    underlying HTTP request.

    Raises :class:`mcp.server.fastmcp.exceptions.ToolError` (message
    prefixed with :data:`UNAUTHORIZED_PREFIX`) if the credential is
    missing, malformed, expired, wrong-scope, or scoped to a different
    organization than this server is configured to serve.
    """
    request = ctx.request_context.request
    authorization = request.headers.get("authorization") if request is not None else None
    if authorization is None or not authorization.startswith("Bearer "):
        raise ToolError(f"{UNAUTHORIZED_PREFIX} missing bearer service credential.")

    token = authorization.removeprefix("Bearer ")
    try:
        claims = verify_service_credential(
            token, signing_secret=signing_secret, required_scope=required_scope
        )
    except ServiceCredentialError as exc:
        raise ToolError(f"{UNAUTHORIZED_PREFIX} {exc}") from exc

    if claims.organization_id != required_organization_id:
        raise ToolError(
            f"{UNAUTHORIZED_PREFIX} credential does not grant this organization's tools."
        )

    return claims
