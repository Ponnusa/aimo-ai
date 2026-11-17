"""A2A integration package boundary.

**M0 status: scaffold only.** This package will wrap the official A2A
Python SDK (https://github.com/a2aproject/a2a-python) for Agent Card
publishing and task request/response once a compatible version is pinned
in M1 (``docs/ROADMAP.md``). Per ``docs/CLAUDE_START_PROMPT.md``: "Do not
simulate A2A by making direct Python function calls between agents." M0
therefore does not include any in-process buyer<->supplier call path at
all — not even a fake one — so nothing here can be mistaken for real
agent-to-agent communication.
"""

from __future__ import annotations


def build_agent_card(*, agent_id: str, endpoint_url: str) -> None:
    raise NotImplementedError(
        "Agent Card construction via the official A2A SDK lands in M1. Not implemented in M0."
    )
