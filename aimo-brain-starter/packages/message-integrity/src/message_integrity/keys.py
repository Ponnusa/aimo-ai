"""Ed25519 keypair generation and PEM/JWK (RFC 8037) conversion.

Private keys are generated once per organization, out of band, by that
organization's own operator — this module never persists a private key
anywhere; it only provides the primitives a key-generation script and
the verifying side's JWK handling both need.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_signing_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def private_key_to_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def private_key_from_pem(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("PEM does not encode an Ed25519 private key.")
    return key


def public_key_to_jwk(public_key: Ed25519PublicKey, *, kid: str) -> dict[str, str]:
    """Encode as a standard OKP JWK (RFC 8037)."""
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": x, "use": "sig", "alg": "EdDSA"}


def public_key_from_jwk(jwk: dict[str, str]) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError(f"Unsupported JWK type for signing verification: {jwk!r}")
    padding = "=" * (-len(jwk["x"]) % 4)
    raw = base64.urlsafe_b64decode(jwk["x"] + padding)
    return Ed25519PublicKey.from_public_bytes(raw)


__all__ = [
    "generate_signing_keypair",
    "private_key_from_pem",
    "private_key_to_pem",
    "public_key_from_jwk",
    "public_key_to_jwk",
]
