"""Salted hashing for the two new bearer secrets this service issues
and never stores in recoverable form (ADR-019 sections 2 and 4): the
org-scoped admin credential, and the opaque ``manage_agents`` service
credential reference. Both secrets are generated with
``secrets.token_urlsafe`` -- high-entropy, not a human-chosen password
-- so a plain salted SHA-256 is an appropriate, dependency-free choice
here; it is not a general-purpose password KDF and must never be used
for anything a human might type.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32
SALT_HEX_LENGTH = 32


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> tuple[str, str]:
    """Returns ``(credential_hash, credential_salt)``."""
    salt = secrets.token_hex(SALT_HEX_LENGTH // 2)
    digest = hashlib.sha256((salt + token).encode("utf-8")).hexdigest()
    return digest, salt


def verify_token(token: str, *, credential_hash: str, credential_salt: str) -> bool:
    expected = hashlib.sha256((credential_salt + token).encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, credential_hash)


__all__ = ["generate_token", "hash_token", "verify_token"]
