"""This agent's A2A Agent Card.

Built from the official A2A SDK's types (``a2a.types``), never a
hand-rolled JSON shape — per CLAUDE.md: "Use the official A2A SDK for
communication and Agent Cards; do not invent a proprietary A2A
replacement." The skill below is food-wholesale specific by name/
description only; the actual food-wholesale logic lives in
``food_wholesale.availability``, not here.
"""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from supplier_agent.settings import SupplierAgentSettings

CHECK_AVAILABILITY_SKILL_ID = "food.check_product_availability"
REQUEST_QUOTE_SKILL_ID = "food.request_quote"
COUNTEROFFER_SKILL_ID = "food.counteroffer"


def build_agent_card(settings: SupplierAgentSettings) -> AgentCard:
    availability_skill = AgentSkill(
        id=CHECK_AVAILABILITY_SKILL_ID,
        name="Check product availability",
        description=(
            "Checks fictional mock stock availability and indicative unit "
            "price for named food-wholesale products. Not a quote: no price "
            "commitment or validity window."
        ),
        tags=["food-wholesale", "availability"],
        examples=["Do you have 100L of lactose-free milk available today?"],
        input_modes=["application/json"],
        output_modes=["application/json"],
    )
    quote_skill = AgentSkill(
        id=REQUEST_QUOTE_SKILL_ID,
        name="Request a quote",
        description=(
            "Accepts a request_for_quote artifact and returns a versioned, "
            "MCP-sourced quote. M4 scope: nonbinding negotiation only — no "
            "order, reservation or payment commitment."
        ),
        tags=["food-wholesale", "negotiation"],
        examples=["Quote me 100L of lactose-free milk."],
        input_modes=["application/json"],
        output_modes=["application/json"],
    )
    counteroffer_skill = AgentSkill(
        id=COUNTEROFFER_SKILL_ID,
        name="Evaluate a counteroffer",
        description=(
            "Accepts a counterproposal, accept_proposal or reject_proposal "
            "artifact bound to an exact prior proposal version, and returns "
            "a revised quote, a NONBINDING proposed_agreement, or a "
            "rejection. Never creates an order, reservation or payment."
        ),
        tags=["food-wholesale", "negotiation"],
        examples=["I can only pay EUR 1.00 per litre."],
        input_modes=["application/json"],
        output_modes=["application/json"],
    )
    return AgentCard(
        name="AIMO BRAIN Fictional Supplier Agent",
        description=(
            "Supplier-side agent for the fictional Nordic Wholesale Foods "
            "organization. Product availability checks, and nonbinding "
            "RFQ/quote/counteroffer negotiation (M4)."
        ),
        version="0.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=settings.supplier_a2a_url,
                protocol_version="1.0",
            )
        ],
        skills=[availability_skill, quote_skill, counteroffer_skill],
    )
