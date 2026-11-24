"""Typed error hierarchy for supplier business-system adapter calls (M7).

Mirrors ``mcp_integration.client``'s error hierarchy deliberately: callers
(``apps/supplier-mcp``) already know how to turn a transient-vs-permanent
distinction into the right protocol-level outcome (retry/reconcile vs.
fail closed), and reusing the same shape means no new decision logic is
needed at that boundary.
"""

from __future__ import annotations


class AdapterError(Exception):
    """Base class for all supplier business-system adapter failures."""


class AdapterUnavailableError(AdapterError):
    """The business system could not be reached, or timed out.

    Transient by definition: the caller must never treat this as proof
    that an order failed — see ``docs/SECURITY.md``'s "timeout is not
    failure" rule. Safe to retry/reconcile.
    """


class AdapterAuthenticationError(AdapterError):
    """The business system rejected the adapter's own credentials.

    Never retried and never treated as "uncertain" — a credential
    rejection is a configuration/authorization problem, not a transient
    network condition, so the caller must fail closed immediately.
    """


class AdapterInvalidRequestError(AdapterError):
    """The business system rejected the request itself (bad/unknown
    identifier, quantity below MOQ with no substitution, etc.) — a
    definite, non-retryable business answer, not a transport fault."""


class AdapterMalformedResponseError(AdapterError):
    """The business system returned something that does not fit the
    expected normalized shape. Never partially trusted — the caller
    must treat this exactly like an unavailable system, not guess at
    the missing fields."""
