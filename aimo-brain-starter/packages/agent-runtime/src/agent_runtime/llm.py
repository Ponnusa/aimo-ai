"""LLM adapter protocol and a deterministic fake implementation.

CLAUDE.md is explicit: "LLM outputs are untrusted proposals. Never let an
LLM bypass policy checks, approve its own binding action or hold
unrestricted ERP credentials." Nothing in this module is wired to any
binding action. In M0 there is no reasoning workflow yet (that is
LangGraph's job starting M1/M4) — this module exists so later milestones
have a stable, testable seam between "something that produces text" and
"something that decides policy," and so tests never depend on a live model
provider.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    deterministic: bool


class LLMAdapter(Protocol):
    """Minimal provider-agnostic interface agent workflows depend on."""

    model_name: str

    async def complete(self, messages: list[LLMMessage]) -> LLMResponse: ...


class FakeLLM:
    """Deterministic, offline LLM adapter used in tests and local dev.

    The response is a hash-derived, stable function of the input messages,
    never a call to any network or paid provider. This lets tests assert
    exact output without flakiness and lets M0 run with zero LLM
    credentials, per ``.env.example`` (``LLM_PROVIDER=fake``).
    """

    model_name = "fake-deterministic-v1"

    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        joined = "\n".join(f"{m.role}:{m.content}" for m in messages)
        digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        content = f"[fake-llm:{digest}] acknowledged: {last_user}"
        return LLMResponse(content=content, model=self.model_name, deterministic=True)


def get_llm_adapter(provider: str | None = None) -> LLMAdapter:
    """Resolve an LLM adapter from an explicit provider name or environment.

    Only ``"fake"`` is implemented in M0. Any other value raises rather than
    silently falling back, so a misconfigured deployment fails loudly
    instead of pretending to call a real provider.
    """
    resolved = provider or os.environ.get("LLM_PROVIDER", "fake")
    if resolved == "fake":
        return FakeLLM()
    raise NotImplementedError(
        f"LLM provider '{resolved}' is not implemented yet. "
        "Only 'fake' is available in M0; real providers land with the "
        "agent reasoning workflow in a later milestone."
    )
