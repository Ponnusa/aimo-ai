"""M8 slice 3 (ADR-018 section 2): verify_inbound_artifact records an
evidence receipt for both an accepted and a rejected verification,
with invariant A's field separation (kid_used/artifact_digest only on
acceptance; claimed_kid/claimed_artifact_digest only on rejection)."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from buyer_agent import message_auth
from buyer_agent.models import BuyerVerificationEvidenceReceiptRecord
from buyer_agent.settings import BuyerAgentSettings
from message_integrity import (
    InMemoryDelegationResolver,
    InMemoryJwksResolver,
    generate_signing_keypair,
    private_key_to_pem,
    sign_artifact,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Base, new_engine

BUYER_ORG = "org_fictional_bistro_01"
SUPPLIER_ORG = "org_fictional_wholesale_01"
SUPPLIER_AGENT_ID = "agent_supplier_01"

_SUPPLIER_PRIVATE_KEY, _SUPPLIER_PUBLIC_KEY = generate_signing_keypair()


@pytest.fixture
async def session_factory() -> async_sessionmaker:
    path = os.path.join(
        tempfile.gettempdir(), f"aimo_buyer_evidence_test_{uuid.uuid4().hex}.sqlite3"
    )
    engine = new_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _settings() -> BuyerAgentSettings:
    _, buyer_public = generate_signing_keypair()
    buyer_private, _ = generate_signing_keypair()
    return BuyerAgentSettings(
        buyer_organization_id=BUYER_ORG,
        buyer_agent_id="agent_buyer_01",
        buyer_signing_private_key_pem=private_key_to_pem(buyer_private),
        buyer_signing_kid="buyer-k1",
        registry_api_url="http://registry-api.test",
        network_api_url="http://network-api.test",
        network_audience="aimo-network:test",
    )


def _artifact(**overrides) -> dict:
    base = {
        "artifact_id": "art_01",
        "transaction_id": "txn_01",
        "correlation_id": "corr_01",
        "sender_agent_id": SUPPLIER_AGENT_ID,
        "recipient_agent_id": "agent_buyer_01",
        "organization_id": SUPPLIER_ORG,
        "type": "quote",
        "version": 1,
        "payload": {"lines": []},
        "content_hash": None,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _patch_resolvers_and_registry(monkeypatch):
    jwks_resolver = InMemoryJwksResolver()
    jwks_resolver.register(
        organization_id=SUPPLIER_ORG, kid="supplier-k1", public_key=_SUPPLIER_PUBLIC_KEY
    )
    delegation_resolver = InMemoryDelegationResolver()
    monkeypatch.setattr(message_auth, "_get_resolver", lambda url: jwks_resolver)
    monkeypatch.setattr(message_auth, "_get_delegation_resolver", lambda url: delegation_resolver)

    async def _fake_registry_check(**kwargs):
        return None

    monkeypatch.setattr(
        message_auth, "verify_agent_is_registered_and_related", _fake_registry_check
    )


@pytest.mark.asyncio
async def test_accepted_verification_records_a_trusted_receipt(session_factory) -> None:
    settings = _settings()
    signed = sign_artifact(
        _artifact(),
        private_key=_SUPPLIER_PRIVATE_KEY,
        kid="supplier-k1",
        organization_id=SUPPLIER_ORG,
        agent_id=SUPPLIER_AGENT_ID,
        recipient_organization_id=BUYER_ORG,
    )

    await message_auth.verify_inbound_artifact(
        signed, settings=settings, session_factory=session_factory
    )

    async with session_factory() as session:
        receipts = (
            (await session.execute(select(BuyerVerificationEvidenceReceiptRecord))).scalars().all()
        )
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.verification_outcome == "accepted"
    assert receipt.kid_used == "supplier-k1"
    assert receipt.artifact_digest is not None
    assert receipt.claimed_kid is None
    assert receipt.claimed_artifact_digest is None


@pytest.mark.asyncio
async def test_rejected_verification_records_a_claimed_only_receipt(session_factory) -> None:
    settings = _settings()
    # Signed by a key never registered in the resolver -- must be rejected.
    unregistered_private, _ = generate_signing_keypair()
    signed = sign_artifact(
        _artifact(),
        private_key=unregistered_private,
        kid="supplier-unregistered-k1",
        organization_id=SUPPLIER_ORG,
        agent_id=SUPPLIER_AGENT_ID,
        recipient_organization_id=BUYER_ORG,
    )

    with pytest.raises(message_auth.MessageAuthenticationError):
        await message_auth.verify_inbound_artifact(
            signed, settings=settings, session_factory=session_factory
        )

    async with session_factory() as session:
        receipts = (
            (await session.execute(select(BuyerVerificationEvidenceReceiptRecord))).scalars().all()
        )
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.verification_outcome == "rejected"
    assert receipt.claimed_kid == "supplier-unregistered-k1"
    assert receipt.kid_used is None
    assert receipt.artifact_digest is None
