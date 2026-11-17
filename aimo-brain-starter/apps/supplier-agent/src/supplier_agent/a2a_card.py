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


def build_agent_card(settings: SupplierAgentSettings) -> AgentCard:
    skill = AgentSkill(
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
    return AgentCard(
        name="AIMO BRAIN Fictional Supplier Agent",
        description=(
            "Supplier-side agent for the fictional Nordic Wholesale Foods "
            "organization. M1 scope: product availability checks only."
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
        skills=[skill],
    )
