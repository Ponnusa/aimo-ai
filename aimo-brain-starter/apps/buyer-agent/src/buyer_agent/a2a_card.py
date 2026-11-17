"""This agent's A2A Agent Card.

Buyer-agent is a genuinely independent A2A server too (M1 requirement 3),
even though the M1 reference interaction only has it acting as a client
against the supplier. The skill below is intentionally minimal and
honest: it reports buyer identity/status, nothing more. Real buyer-side
skills (receiving counteroffers, approvals, etc.) land with the
negotiation protocol in M4.
"""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from buyer_agent.settings import BuyerAgentSettings

AGENT_STATUS_SKILL_ID = "food.buyer_agent_status"


def build_agent_card(settings: BuyerAgentSettings) -> AgentCard:
    skill = AgentSkill(
        id=AGENT_STATUS_SKILL_ID,
        name="Buyer agent status",
        description=(
            "Reports this buyer agent's identity and fictional organization "
            "context. Placeholder until buyer-side negotiation skills "
            "(receiving counteroffers, approvals) land in M4."
        ),
        tags=["food-wholesale", "status"],
        examples=["status"],
        input_modes=["application/json"],
        output_modes=["application/json"],
    )
    return AgentCard(
        name="AIMO BRAIN Fictional Buyer Agent",
        description=(
            "Buyer-side agent for the fictional Lakeside Bistro organization. "
            "M1 scope: acts as an A2A client requesting product availability; "
            "exposes its own compliant A2A server endpoint and Agent Card."
        ),
        version="0.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=settings.buyer_a2a_url,
                protocol_version="1.0",
            )
        ],
        skills=[skill],
    )
