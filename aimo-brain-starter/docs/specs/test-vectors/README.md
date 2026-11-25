# JWS artifact-signature test vectors

Companion fixtures for [`../jws-artifact-signatures.md`](../jws-artifact-signatures.md).
Each file is plain JSON, with no dependency on this repository's Python
code, so an independent implementer can load them directly. All key
material here is **test-only**, generated for these vectors alone, and
must never be treated as a real signing identity.

| File | Case | What it proves |
|---|---|---|
| `vector_01_valid_signature.json` | `verify_success` | A correctly signed artifact must be accepted, and the recovered claims must match exactly. |
| `vector_02_tampered_artifact.json` | `verify_failure` (`artifact_digest_mismatch`) | A valid signature over content that was changed after signing must be rejected — the digest check, not just the cryptographic signature check, matters. |
| `vector_03_wrong_key.json` | `verify_failure` (`invalid_signature`) | A signature made with a key other than the one published for the claimed `(organization_id, kid)` must be rejected, even though the JWS is otherwise well-formed. |
| `vector_04_integer_float_coercion.json` | `digest_equivalence` | An artifact whose `version` field is the JSON float `1.0` must canonicalize and digest identically to the same artifact with `version: 1` — see spec section 2 for why this matters (A2A's protobuf `Struct` transport). |
| `vector_05_unicode_content.json` | `verify_success` | A correctly signed artifact containing non-ASCII (Unicode) text must canonicalize with every non-ASCII codepoint escaped as lowercase `\uXXXX` (spec section 2d — Python `json.dumps`'s `ensure_ascii=True` default), and must verify successfully. |
| `vector_06_duplicate_key_rejected.json` | `parse_failure` (`duplicate_key_rejected`) | Raw JSON **text** (not an already-parsed object) containing a duplicate object key must be rejected by strict parsing (spec section 2f) before it ever reaches canonicalization — exercises `message_integrity.proofs.strict_json_loads`, not `canonicalize` itself. |

## Common shape

`verify_success` / `verify_failure` vectors carry:
- `public_key_jwk` — the signer's public key, RFC 8037 JWK form, as it would appear in a `GET /organizations/{id}/jwks.json` response.
- `expected_recipient_organization_id` — the `aud` a verifier should pass in.
- `signed_artifact` — the full artifact, including its `proof`.
- (`verify_success` only) `expected_artifact_digest`, `expected_canonicalized_artifact_without_proof`, `expected_verified_claims`.
- (`verify_failure` only) `expected_failure_reason` — a short machine-readable label (not a specific exception class name — different languages will raise differently; what matters is that *some* rejection occurs, for the stated reason).

`digest_equivalence` vectors (`vector_04`) instead carry two artifact
variants and the single digest/canonicalized form both must produce.

## Cross-checking against the Python reference implementation

`packages/message-integrity/tests/test_jws_vectors.py` loads these same
files and asserts the Python implementation (`message_integrity.proofs`)
produces/accepts exactly what each vector states. Run it with:

```
uv run pytest packages/message-integrity/tests/test_jws_vectors.py -v
```
