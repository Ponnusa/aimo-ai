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
from message_integrity import generate_signing_keypair, private_key_to_pem, public_key_to_jwk


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
