"""M8 slice 5 merge-review fix (binding verification): proves the
end-to-end consequence, not just the pure-function unit tests in
``apps/buyer-agent-ts/test/binding.test.ts``. A minimal "evil" agent --
a genuinely separate, registered, verified organization holding its own
real, live signing authority (so every artifact it sends is VALIDLY
SIGNED) -- deliberately sends a tampered binding field at one of two
points in the negotiation. The real TypeScript buyer must reject it via
its own binding check and must never send any artifact past that point.

Discovery is still fully registry-mediated (ADR-020 fix 2): the evil
agent is a real, independently registered and verified participant, not
a bypass of discovery -- this test is about binding verification, not
discovery.
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest
from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from fastapi import FastAPI, Response
from food_wholesale.authorization import build_authorization_evidence, build_order_confirmation
from food_wholesale.negotiation import build_proposed_agreement, build_quote
from message_integrity import private_key_from_pem
from message_integrity import sign_artifact as _sign_artifact

from identity import issue_service_credential

from .a2a_server_harness import find_free_port, generate_and_register_signing_key, running_server
from .test_slice5_buyer_happy_path import (
    ADMIN_KEY,
    NETWORK_AUDIENCE,
    SIGNING_SECRET,
    _bootstrap_external_org,
    _create_legacy_org,
    _create_relationship,
    _npm_and_npx,
    _run_node,
    full_stack,
    ts_installed,
)

__all__ = ["full_stack", "ts_installed"]

BUYER_ORG = "org_fictional_bistro_01"
EVIL_ORG = "org_evil_tamperer_01"
EVIL_AGENT_ID = "agent_evil_tamperer_01"


class EvilExecutor(AgentExecutor):
    """Honest about everything except exactly one tampered binding field,
    selected by ``corrupt``. Every response it sends is VALIDLY SIGNED
    with its own real, registered root key -- the point is that a valid
    signature alone must not be enough."""

    def __init__(
        self, *, private_key_pem: str, kid: str, organization_id: str, agent_id: str, corrupt: str
    ):
        self._private_key = private_key_from_pem(private_key_pem)
        self._kid = kid
        self._organization_id = organization_id
        self._agent_id = agent_id
        self._corrupt = corrupt
        self.received_types: list[str] = []

    def _sign(self, artifact: dict, *, recipient_organization_id: str) -> dict:
        return _sign_artifact(
            artifact,
            private_key=self._private_key,
            kid=self._kid,
            organization_id=self._organization_id,
            agent_id=self._agent_id,
            recipient_organization_id=recipient_organization_id,
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.start_work()

        data_parts = get_data_parts(context.message.parts) if context.message else []
        if not data_parts:
            await updater.failed(message=new_text_message("No structured data payload."))
            return
        payload = data_parts[0]
        artifact_type = payload.get("type")
        self.received_types.append(artifact_type)
        buyer_org = payload["organization_id"]
        buyer_agent_id = payload["sender_agent_id"]
        transaction_id = payload["transaction_id"]

        if artifact_type == "request_for_quote":
            lines = [
                {"product_ref": "sku-evil-1", "quantity": "100", "unit": "L", "unit_price": "1.00"}
                for _ in payload["payload"]["lines"]
            ]
            response = build_quote(
                artifact_id=str(uuid.uuid4()),
                transaction_id=transaction_id,
                correlation_id=payload["correlation_id"],
                supplier_agent_id=self._agent_id,
                buyer_agent_id=buyer_agent_id,
                supplier_organization_id=self._organization_id,
                proposal_id=str(uuid.uuid4()),
                supersedes_proposal_id=payload["proposal_id"],
                version=payload["version"] + 1,
                lines=lines,
                currency="EUR",
            )
        elif artifact_type == "accept_proposal":
            lines = [
                {"product_ref": "sku-evil-1", "quantity": "100", "unit": "L", "unit_price": "1.00"}
            ]
            agreement_of_proposal_id = (
                "WRONG-PROPOSAL-ID"
                if self._corrupt == "proposed_agreement_proposal_id"
                else payload["payload"]["accepted_proposal_id"]
            )
            if self._corrupt == "proposed_agreement_content_hash":
                lines = [
                    {
                        "product_ref": "sku-evil-1",
                        "quantity": "999",
                        "unit": "L",
                        "unit_price": "1.00",
                    }
                ]
            response = build_proposed_agreement(
                artifact_id=str(uuid.uuid4()),
                transaction_id=transaction_id,
                correlation_id=payload["correlation_id"],
                sender_agent_id=self._agent_id,
                recipient_agent_id=buyer_agent_id,
                organization_id=self._organization_id,
                proposal_id=str(uuid.uuid4()),
                version=payload["version"] + 1,
                agreement_of_proposal_id=agreement_of_proposal_id,
                lines=lines,
                currency="EUR",
                delivery_terms=None,
            )
        elif artifact_type == "authorization_evidence":
            response = build_authorization_evidence(
                artifact_id=str(uuid.uuid4()),
                transaction_id=transaction_id,
                correlation_id=payload["correlation_id"],
                sender_agent_id=self._agent_id,
                recipient_agent_id=buyer_agent_id,
                organization_id=self._organization_id,
                proposal_id=str(uuid.uuid4()),
                version=payload["version"] + 1,
                agreement_id=transaction_id,
                content_hash_value=payload["payload"]["content_hash"],
                allow=True,
                human_approval_required=False,
                deny_reason=None,
                policy_version="evil-v1",
                policy_input_digest="n/a",
            )
        elif artifact_type == "order_commit_request":
            agreement_id = (
                "WRONG-AGREEMENT-ID"
                if self._corrupt == "order_confirmation_agreement_id"
                else transaction_id
            )
            idempotency_key = (
                "WRONG-IDEMPOTENCY-KEY"
                if self._corrupt == "order_confirmation_idempotency_key"
                else payload["payload"]["idempotency_key"]
            )
            response = build_order_confirmation(
                artifact_id=str(uuid.uuid4()),
                transaction_id=transaction_id,
                correlation_id=payload["correlation_id"],
                sender_agent_id=self._agent_id,
                recipient_agent_id=buyer_agent_id,
                organization_id=self._organization_id,
                proposal_id=str(uuid.uuid4()),
                version=payload["version"] + 1,
                agreement_id=agreement_id,
                idempotency_key=idempotency_key,
                status="CONFIRMED",
                external_order_id="evil-order-1",
            )
        else:
            await updater.failed(
                message=new_text_message(f"Unexpected artifact type {artifact_type!r}.")
            )
            return

        signed = self._sign(response, recipient_organization_id=buyer_org)
        await updater.add_artifact(parts=[new_data_part(signed)])
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def _build_evil_app(
    *, private_key_pem: str, kid: str, corrupt: str, a2a_base_url: str
) -> tuple[FastAPI, EvilExecutor]:
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import (
        add_a2a_routes_to_fastapi,
        create_agent_card_routes,
        create_jsonrpc_routes,
    )
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface

    app = FastAPI()
    challenge_state: dict[str, str | None] = {"value": None}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/aimo-brain-challenge")
    async def serve_challenge() -> Response:
        return Response(content=challenge_state["value"] or "", media_type="text/plain")

    executor = EvilExecutor(
        private_key_pem=private_key_pem,
        kid=kid,
        organization_id=EVIL_ORG,
        agent_id=EVIL_AGENT_ID,
        corrupt=corrupt,
    )
    agent_card = AgentCard(
        name="Evil Tamperer Agent",
        description="Deliberately sends one tampered binding field per test scenario.",
        version="0.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=a2a_base_url, protocol_version="1.0")
        ],
        skills=[],
    )
    request_handler = DefaultRequestHandler(
        agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=agent_card
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
    )
    app.router.routes.insert(0, app.router.routes.pop())
    return app, executor, challenge_state


async def _register_evil_agent_with_registry(
    *, registry_base_url: str, a2a_card_url: str, challenge_state: dict
) -> None:
    manage_agents_credential = issue_service_credential(
        organization_id=EVIL_ORG, scope="manage_agents", signing_secret=SIGNING_SECRET
    )
    headers = {"Authorization": f"Bearer {manage_agents_credential}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        register = await client.post(
            f"{registry_base_url}/agents",
            json={
                "agent_id": EVIL_AGENT_ID,
                "organization_id": EVIL_ORG,
                "a2a_card_url": a2a_card_url,
            },
            headers=headers,
        )
        assert register.status_code == 200, register.text

        start = await client.post(
            f"{registry_base_url}/agents/{EVIL_AGENT_ID}/verification/start",
            params={"organization_id": EVIL_ORG},
            headers=headers,
        )
        assert start.status_code == 200, start.text
        challenge_state["value"] = start.json()["endpoint_challenge"]

        confirm = await client.post(
            f"{registry_base_url}/agents/{EVIL_AGENT_ID}/verification/confirm",
            params={"organization_id": EVIL_ORG},
            headers=headers,
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt,expected_last_received_type",
    [
        ("proposed_agreement_proposal_id", "accept_proposal"),
        ("proposed_agreement_content_hash", "accept_proposal"),
        ("order_confirmation_agreement_id", "order_commit_request"),
        ("order_confirmation_idempotency_key", "order_commit_request"),
    ],
)
async def test_tampered_binding_is_rejected_before_advancing(
    full_stack, ts_installed, corrupt, expected_last_received_type
):
    _, npx = _npm_and_npx()
    stack = full_stack

    await _create_legacy_org(stack["network"].base_url, EVIL_ORG)
    evil_kid = f"{EVIL_ORG}-root-1"
    evil_pem = await generate_and_register_signing_key(
        network_api_base_url=stack["network"].base_url,
        organization_id=EVIL_ORG,
        admin_key=ADMIN_KEY,
        kid=evil_kid,
    )

    org_credential = await _bootstrap_external_org(stack["network"].base_url, BUYER_ORG)
    await _create_relationship(stack["network"].base_url, BUYER_ORG, EVIL_ORG)

    evil_port = find_free_port()
    evil_base_url = f"http://127.0.0.1:{evil_port}"
    evil_app, evil_executor, challenge_state = _build_evil_app(
        private_key_pem=evil_pem, kid=evil_kid, corrupt=corrupt, a2a_base_url=evil_base_url
    )
    with running_server(evil_app, port=evil_port) as evil_server:
        await _register_evil_agent_with_registry(
            registry_base_url=stack["registry"].base_url,
            a2a_card_url=evil_server.base_url,
            challenge_state=challenge_state,
        )

        provision_env = dict(os.environ)
        buyer_agent_id = f"agent_external_buyer_{uuid.uuid4().hex[:8]}"
        provision_env.update(
            {
                "NETWORK_API_URL": stack["network"].base_url,
                "BUYER_ORGANIZATION_ID": BUYER_ORG,
                "ORG_SCOPED_CREDENTIAL": org_credential,
                "BUYER_AGENT_ID": buyer_agent_id,
                "DELEGATION_AUDIENCE": NETWORK_AUDIENCE,
            }
        )
        provisioned = _run_node(npx, "src/provision.ts", provision_env)
        assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
        prov = json.loads(provisioned.stdout)

        buyer_port = find_free_port()
        buyer_env = dict(os.environ)
        buyer_env.update(
            {
                "REGISTRY_API_URL": stack["registry"].base_url,
                "NETWORK_API_URL": stack["network"].base_url,
                "BUYER_ORGANIZATION_ID": BUYER_ORG,
                "SUPPLIER_ORGANIZATION_ID": EVIL_ORG,
                "ORG_SCOPED_CREDENTIAL": org_credential,
                "BUYER_AGENT_ID": buyer_agent_id,
                "SUPPLIER_AGENT_ID": EVIL_AGENT_ID,
                "BUYER_SELF_PORT": str(buyer_port),
                "AGENT_PRIVATE_KEY_JWK": json.dumps(prov["agentPrivateJwk"]),
                "AGENT_PUBLIC_KEY_JWK": json.dumps(prov["agentPublicJwk"]),
                "DELEGATED_KID": prov["delegatedKid"],
                "NETWORK_AUDIENCE": NETWORK_AUDIENCE,
            }
        )
        result = _run_node(npx, "src/buyerAgent.ts", buyer_env)

        # The buyer's own BindingMismatchError must stop it -- it must
        # NEVER report "committed", and the process must not exit 0 with
        # a success outcome.
        assert "committed" not in result.stdout, (
            f"tampered binding ({corrupt}) was NOT rejected:\n{result.stdout}\n{result.stderr}"
        )
        assert result.returncode != 0, (
            f"expected the buyer process to fail on tampered binding ({corrupt}):\n"
            f"{result.stdout}\n{result.stderr}"
        )

        # The evil agent's own received-type log proves the buyer never
        # sent anything past the tampered artifact.
        assert evil_executor.received_types[-1] == expected_last_received_type, (
            f"evil agent received an unexpected artifact after the tampered one: "
            f"{evil_executor.received_types}"
        )
