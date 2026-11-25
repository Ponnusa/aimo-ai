"""Ed25519 keypair generation and PEM/JWK (RFC 8037) conversion.

Private keys are generated once per organization, out of band, by that
organization's own operator — this module never persists a private key
anywhere; it only provides the primitives a key-generation script and
the verifying side's JWK handling both need.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping

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


def validate_ed25519_public_jwk(jwk: Mapping[str, object]) -> None:
    """Reject anything that is not a well-formed Ed25519 OKP *public* key
    (M8, ADR-015 section D) -- a registrant presenting malformed, wrong
    key-type, or private key material must be rejected before any
    challenge is bound to it, not merely fail cryptographic verification
    later.

    Raises :class:`ValueError` describing exactly what was wrong.
    """
    if not isinstance(jwk, dict):
        raise ValueError("JWK must be a JSON object.")
    if jwk.get("kty") != "OKP":
        raise ValueError(f"Unsupported kty {jwk.get('kty')!r}; only 'OKP' is accepted.")
    if jwk.get("crv") != "Ed25519":
        raise ValueError(f"Unsupported crv {jwk.get('crv')!r}; only 'Ed25519' is accepted.")
    if "d" in jwk:
        raise ValueError(
            "JWK carries a private-key component ('d'); only a public key may be registered."
        )
    x = jwk.get("x")
    if not isinstance(x, str) or not x:
        raise ValueError("JWK is missing a valid 'x' (public key) member.")
    try:
        padding = "=" * (-len(x) % 4)
        raw = base64.urlsafe_b64decode(x + padding)
    except (ValueError, Exception) as exc:  # binascii.Error is a subclass of ValueError
        raise ValueError(f"'x' is not valid base64url: {exc}") from exc
    if len(raw) != 32:
        raise ValueError(f"Ed25519 public key must decode to 32 bytes, got {len(raw)}.")
    # Confirms the bytes actually form a valid Ed25519 public point, not
    # just the right length.
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise ValueError(f"'x' is not a valid Ed25519 public key: {exc}") from exc


def jwk_thumbprint(jwk: dict[str, str]) -> str:
    """RFC 7638 JWK thumbprint for an OKP key: SHA-256 over the JSON
    object containing exactly the required members, in the fixed
    lexicographic member order the RFC specifies, with no insignificant
    whitespace, base64url-encoded without padding."""
    required = {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}
    canonical = json.dumps(required, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


__all__ = [
    "generate_signing_keypair",
    "jwk_thumbprint",
    "private_key_from_pem",
    "private_key_to_pem",
    "public_key_from_jwk",
    "public_key_to_jwk",
    "validate_ed25519_public_jwk",
]
