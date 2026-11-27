"""Test harness: run a real FastAPI app as a real HTTP server on a real
localhost port, in a background thread with its own event loop.

This exists so A2A exchange tests use genuine network calls end to end —
never ``ASGITransport``, which would stay inside one process's call stack
and risk looking like "simulated A2A" even though the HTTP layer is real.
Per ``docs/CLAUDE_START_PROMPT.md``: "Do not simulate A2A by making direct
Python function calls between agents," tests hold themselves to the same
rule the production code follows.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import uvicorn
from fastapi import FastAPI
from message_integrity import (
    DelegationScope,
    build_delegation,
    generate_signing_keypair,
    private_key_to_pem,
    public_key_to_jwk,
)


@dataclass
class RunningServer:
    base_url: str
    _server: uvicorn.Server
    _thread: threading.Thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_server_thread(server: uvicorn.Server) -> None:
    asyncio.run(server.serve())


def start_server(app: FastAPI, *, host: str = "127.0.0.1", port: int) -> RunningServer:
    """Start ``app`` as a real uvicorn server on ``host:port`` in a
    background thread, and block until it responds to ``/health``."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=_run_server_thread, args=(server,), daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.5)
            if response.status_code == 200:
                return RunningServer(base_url=base_url, _server=server, _thread=thread)
        except httpx.HTTPError:
            pass
        time.sleep(0.1)

    server.should_exit = True
    thread.join(timeout=5)
    raise RuntimeError(f"Server on {base_url} did not become healthy in time.")


@contextlib.contextmanager
def running_server(app: FastAPI, *, port: int) -> Iterator[RunningServer]:
    handle = start_server(app, port=port)
    try:
        yield handle
    finally:
        handle.stop()


async def generate_and_register_signing_key(
    *, network_api_base_url: str, organization_id: str, admin_key: str, kid: str
) -> str:
    """M5.1 test helper: generate a fresh Ed25519 keypair, register its
    public JWK with the real network-api server under test, and return
    the private key PEM for the caller to set as that organization's
    own agent's ``*_SIGNING_PRIVATE_KEY_PEM`` env var. Mirrors what
    ``scripts/generate_signing_key.py`` does for a real deployment, but
    end to end against a live test server rather than printed to a
    terminal."""
    private_key, public_key = generate_signing_keypair()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_api_base_url}/organizations/{organization_id}/signing-keys",
            json={"kid": kid, "public_key_jwk": public_key_to_jwk(public_key, kid=kid)},
            headers={"X-Admin-Key": admin_key},
        )
    response.raise_for_status()
    return private_key_to_pem(private_key)


@dataclass
class DelegatedSigningKey:
    """A dedicated per-agent signing key plus the organisation-root-
    signed delegation that authorizes it (M8 slice 2, ADR-017) — the
    replacement for an agent holding its organization's root private
    key directly. The caller sets ``private_key_pem``/``kid`` as the
    agent's own ``*_SIGNING_PRIVATE_KEY_PEM``/``*_SIGNING_KID`` and
    never needs ``root_private_key_pem`` again once ``jti`` is
    published; it is returned only so a test can exercise rotation
    (issuing a second delegation under the same root) without
    re-registering a new root key."""

    private_key_pem: str
    kid: str
    jti: str
    root_private_key_pem: str
    root_kid: str


async def generate_and_register_delegated_signing_key(
    *,
    network_api_base_url: str,
    organization_id: str,
    admin_key: str,
    agent_id: str,
    audience: str,
    artifact_types: tuple[str, ...],
    protocol_version: str = "v1",
    root_kid: str | None = None,
    root_private_key_pem: str | None = None,
    delegated_kid: str | None = None,
) -> DelegatedSigningKey:
    """End-to-end equivalent of ``scripts/issue_agent_delegation.py``,
    against a real test server rather than printed to a terminal: makes
    (or reuses) an organization root key, builds and signs a delegation
    for a dedicated agent key entirely with that root key material, and
    publishes the delegation to network-api. The root private key is
    never set as the agent's own signing key -- only the dedicated
    delegated key returned here is."""
    if root_private_key_pem is None:
        root_kid = root_kid or f"{organization_id}-root-k1"
        root_private_key_pem = await generate_and_register_signing_key(
            network_api_base_url=network_api_base_url,
            organization_id=organization_id,
            admin_key=admin_key,
            kid=root_kid,
        )
    assert root_kid is not None

    from message_integrity import private_key_from_pem

    root_private_key = private_key_from_pem(root_private_key_pem)
    delegated_private_key, delegated_public_key = generate_signing_keypair()
    delegated_kid = delegated_kid or f"{agent_id}-k1"
    delegated_jwk = public_key_to_jwk(delegated_public_key, kid=delegated_kid)

    token = build_delegation(
        root_private_key=root_private_key,
        issuing_kid=root_kid,
        organization_id=organization_id,
        agent_id=agent_id,
        audience=audience,
        delegated_kid=delegated_kid,
        delegated_public_key_jwk=delegated_jwk,
        scope=DelegationScope(artifact_types=artifact_types, protocol_version=protocol_version),
    )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_api_base_url}/organizations/{organization_id}/delegations",
            json={"jws": token},
            headers={"X-Admin-Key": admin_key},
        )
    response.raise_for_status()
    jti = response.json()["jti"]

    return DelegatedSigningKey(
        private_key_pem=private_key_to_pem(delegated_private_key),
        kid=delegated_kid,
        jti=jti,
        root_private_key_pem=root_private_key_pem,
        root_kid=root_kid,
    )


async def revoke_delegation(
    *, network_api_base_url: str, organization_id: str, admin_key: str, jti: str
) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{network_api_base_url}/organizations/{organization_id}/delegations/{jti}/revoke",
            headers={"X-Admin-Key": admin_key},
        )
    response.raise_for_status()
