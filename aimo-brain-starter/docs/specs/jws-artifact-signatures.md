# AIMO BRAIN artifact-signature specification (v1)

**Audience: implementers outside this repository.** This document describes
exactly how a trade-protocol artifact's `proof` object is produced and
verified, with no dependency on this repository's Python code or packages.
It is the normative reference for any independent (non-AIMO) agent that
wants its signed artifacts to verify against AIMO organizations, and for
AIMO organizations verifying artifacts from such an agent. The reference
implementation is `packages/message-integrity` (`proofs.py`, `keys.py`,
`jwks.py`); if this document and that code ever disagree, treat it as a bug
in one of them, not as two valid interpretations — file it against
`docs/DECISIONS.md`.

Status: **v1, matches the implementation as of M8 slice 1 (ADR-015)**. Only
the `EdDSA` algorithm (Ed25519) is defined. There is no algorithm
negotiation; a verifier that receives a `proof.alg` other than `EdDSA` must
reject it.

**Cross-language verification status (read this before trusting this
document for a real integration):** as of this revision, this specification
and its test vectors have been verified by one independent, non-Python
implementation — a minimal Node.js/TypeScript conformance harness, see
section 7. This proves **Python ↔ Node.js** interoperability specifically.
It does **not** prove interoperability with any other language or runtime;
an implementer in a third language should not treat this document as
battle-tested until their own implementation also passes the vectors in
section 6.

## 0. What this document corrects relative to the first M8 slice

An earlier revision of this document was reviewed and found to contain
several gaps that matter for a genuinely independent, cross-language
implementation. This revision fixes the document (not the wire format) to
close those gaps precisely:

- States plainly that canonicalization is **not** RFC 8785 (JSON
  Canonicalization Scheme) and is not an attempt to approximate it — see
  section 2a.
- States the **actual** protected header shape, including the `typ` claim
  the reference implementation's JWT library adds by default (previously
  omitted) — see section 3.
- Spells out the **literal signing-input byte construction** (previously
  implicit) — see section 3a.
- States explicit Unicode/`ensure_ascii` behavior, numeric precision and
  exponent handling, negative zero, duplicate-JSON-key handling, and
  NaN/Infinity handling — see sections 2b–2f.
- Points to the cross-language conformance harness (section 7) and is
  explicit about what it does and does not prove.

**No wire-format behavior changed.** Every artifact that was valid and
verifiable under the previous revision of this document remains valid and
produces the identical `artifact_digest` and the identical JWS under this
revision. The one code-level hardening this slice makes (rejecting
`NaN`/`Infinity` outright — section 2f) only affects inputs that were never
valid under rule 2 below to begin with; it does not change any
previously-valid artifact's bytes.

## 1. Where `proof` lives

Every signed artifact is a JSON object (the nine trade-protocol artifact
types: `request_for_quote`, `quote`, `counterproposal`, `accept_proposal`,
`reject_proposal`, `proposed_agreement`, `authorization_evidence`,
`order_commit_request`, `order_confirmation`, plus any future artifact type
that adopts this mechanism) with a top-level `proof` field:

```json
{
  "proof": {
    "alg": "EdDSA",
    "kid": "<string: key identifier, opaque to the protocol>",
    "jws": "<string: a compact-serialization JWS, see section 3>"
  }
}
```

`proof` is absent until the artifact is signed. A signer must never sign an
artifact that already carries a `proof` field — sign the final content
once, not incrementally.

## 2. Canonicalization

Before an artifact's digest is computed — for signing or for verification —
it is **canonicalized**:

1. Start from the artifact's JSON representation **with the `proof` field
   removed entirely** (not present, not null — absent).
2. **Normalize numbers.** Walk the structure recursively. For every JSON
   number that is a float with a zero fractional part (e.g. `1.0`, `42.0`,
   `-3.0`), replace it with the equivalent JSON integer (`1`, `42`, `-3`).
   Leave integers, non-integer floats, strings, booleans, `null`, objects
   and arrays otherwise unchanged. Recurse into every object value and
   every array element.
   - **Why this rule exists:** the A2A transport this network uses carries
     structured payloads as a protobuf `Struct`, whose only numeric type is
     double-precision float. An integer field such as `version: 1`, sent
     by one party, is observed by the other party as `version: 1.0`.
     Every monetary or quantity value in this protocol is already encoded
     as a decimal **string**, never a JSON number — so the *only* JSON
     numbers a conformant artifact ever legitimately carries are integers,
     and any float observed in their place is that integer with a
     transport-induced representation change, not a change in value.
   - Booleans are not touched by this rule — check for a boolean type
     explicitly and skip it before checking for a float (in a language
     where booleans are a subtype of integer, as in Python, check boolean
     identity *first*).
3. Serialize the normalized structure to a JSON string per rules 2a–2f
   below.
4. Compute `sha256` of the UTF-8 bytes of that string. The resulting
   64-character lowercase hex digest is the **artifact digest**.

This is the *same* canonicalization rule used elsewhere in this protocol
for `content_hash` (see `docs/PROTOCOLS.md`) — there is exactly one
canonicalization rule in this entire system (`message_integrity.proofs.
canonicalize` and `trade_protocol.negotiation.canonicalize_terms` are kept
byte-for-byte identical on purpose), never two slightly different ones.

### 2a. This is not RFC 8785

**This canonicalization rule is a bespoke, self-contained specification. It
is not RFC 8785 (the JSON Canonicalization Scheme, "JCS"), and it does not
attempt to be.** In particular, JCS defines its own number-serialization
algorithm (based on ECMA-262's `Number::toString`) that this rule does not
use — this rule's only numeric transformation is the integer/float
coercion in step 2 above. Do not assume a JCS library gets this right; it
will not, because it is solving a related but different problem. An
implementer should serialize directly per rules 2b–2f, not reach for a JCS
library and expect it to match.

### 2b. Key ordering

Object keys are sorted by their exact string value, using ordinary
codepoint (not locale-aware) ordering — this is the ordering Python's
`json.dumps(..., sort_keys=True)` produces, and the ordering most standard
JSON libraries' default lexicographic string sort produces. Nested objects
are sorted independently at every level.

### 2c. Whitespace and encoding

No insignificant whitespace: no spaces after `:` or `,`, no newlines, no
indentation. Equivalent to Python's `json.dumps(data, sort_keys=True,
separators=(",", ":"))`. The resulting string is then encoded as UTF-8
bytes before hashing/signing — see section 3a for exactly where in the
pipeline this happens.

### 2d. Unicode and non-ASCII characters

**The reference implementation's canonicalization uses Python's
`json.dumps` default behavior, which is `ensure_ascii=True`.** This means
every non-ASCII codepoint in a string value is escaped as `\uXXXX` (or a
`\uXXXX\uXXXX` surrogate pair for codepoints outside the Basic Multilingual
Plane) in the canonical JSON string, rather than being emitted as raw UTF-8
bytes. **An implementation in another language must reproduce this
escaping exactly** — a JSON library that defaults to emitting raw UTF-8 for
non-ASCII characters (many do) will produce a *different* canonical string,
and therefore a different digest and signature, for any artifact
containing non-ASCII text (e.g. a product name with accented characters or
non-Latin script). Escaped hex digits must be lowercase, matching Python's
output. No Unicode normalization (NFC/NFD/NFKC/NFKD) is performed or
required — strings are escaped as given, not normalized first.

### 2e. Numeric precision, exponents, and negative zero

Beyond the integer/float coercion in step 2, no other numeric
transformation is defined or required, because **no conformant artifact
ever contains a JSON number other than an integer** — every monetary or
quantity value in this protocol is a decimal string (`docs/PROTOCOLS.md`
rule 2). A non-integer float, or an integer so large it would lose
precision in IEEE-754 double representation, should never appear in a
conformant artifact in the first place; this specification does not define
cross-language-safe serialization for such a value, and an implementation
encountering one should treat it as a protocol violation rather than guess
at a serialization. This is a documented residual gap, not a silent one:
a future protocol revision may define a stricter rule (e.g. outright
rejecting any non-integer number during canonicalization), but doing so now
would require explicit version negotiation, since it is a behavior change
regardless of how unlikely it is to ever trigger on real traffic — see
section 0.

Negative zero (`-0.0`) **is** covered by the existing rule: its fractional
part is zero, so it is coerced to the integer `0` (not `-0`) in step 2,
identically to positive zero. `-0` and `0` therefore always canonicalize
identically.

### 2f. Duplicate keys, NaN, and Infinity

A canonicalized artifact is built by walking an **already-parsed, in-memory
object** (a Python `dict`, or a Node.js `object`/`Map`) — not by
re-serializing raw JSON text. Because a language-native object cannot
contain two values under the exact same key, the classic "duplicate JSON
key" parsing ambiguity does not arise inside canonicalization itself. It
*can* arise one layer earlier, in whatever code deserializes raw JSON text
off the wire into that in-memory object — and that layer is explicitly in
scope for any implementation that accepts raw JSON text as a transport
(this network's primary transport, the A2A SDK's protobuf `Struct`, has no
such concept at all, since protobuf maps cannot have duplicate keys
either). **An implementation that does parse raw JSON text before
canonicalizing must reject input containing a duplicate object key**,
rather than silently keeping the first or last occurrence — the reference
implementation provides `message_integrity.proofs.strict_json_loads` for
exactly this, and section 6 includes a rejection vector demonstrating it.

`NaN`, `Infinity`, and `-Infinity` are **not valid JSON** per RFC 8259 and
must never appear in a canonicalized artifact. The reference
implementation's canonicalization explicitly rejects them (Python's
`json.dumps(..., allow_nan=False)`) rather than silently emitting the
non-standard, unparseable tokens its default behavior otherwise would. Any
implementation must reject these the same way, not attempt to serialize
them.

## 3. The JWS payload

The `proof.jws` string is a standard compact-serialization JSON Web
Signature (RFC 7515), algorithm `EdDSA` (Ed25519, RFC 8032).

**Protected header — the exact, complete shape, as actually produced by the
reference implementation (`PyJWT`):**
```json
{"typ": "JWT", "alg": "EdDSA", "kid": "<kid>"}
```
**Note the `typ: "JWT"` member.** `kid` is an opaque string chosen by the
signer (in this network, by convention, scoped under the signing
organization's id — but a verifier must not assume any particular format,
only use it as an opaque lookup key into the issuer's published key set —
see section 5). A verifier must accept this header shape; it must not
require `typ` to be absent, and it must not require any particular key
*order* within the header object (the header, unlike the canonicalized
artifact, is not separately canonicalized by this specification — it is
whatever JSON object the signer's JWT library serializes, and a verifier
parses it as JSON, not by exact string comparison).

**Claims (the signed payload):**

| Claim | Type | Meaning |
|---|---|---|
| `iss` | string | The signing organization's id. |
| `sub` | string | The signing **agent's** id (the specific agent within that organization that produced this artifact). |
| `aud` | string | The intended recipient organization's id. A verifier must reject a JWS whose `aud` does not match its own organization id. |
| `neg` | string | The artifact's `transaction_id` (negotiation id). |
| `typ` | string | The artifact's `type`. **Not to be confused with the header's own `typ: "JWT"` claim above** — this is a same-named but entirely distinct claim, at the payload level, carrying the artifact type (e.g. `"quote"`), not a token-type marker. |
| `ver` | integer | The artifact's `version`. |
| `hash` | string or null | The artifact's own `content_hash` field, verbatim (this is the business-content hash defined elsewhere in `docs/PROTOCOLS.md`; it is a different value from `artifact_digest` below, and both are checked independently). |
| `artifact_digest` | string | The sha256 hex digest computed in section 2, over the **entire artifact except `proof`**. |
| `jti` | string | A fresh unique identifier for this specific signature (e.g. a UUID), used by a receiver's own replay cache. The signer must generate a new `jti` for every signature, even when re-signing semantically identical content. |
| `iat` | integer | Issued-at, Unix seconds (UTC). |
| `exp` | integer | Expiry, Unix seconds (UTC). The reference implementation defaults to `iat + 600` (10 minutes) but a verifier must honor whatever `exp` is actually present, not assume a fixed window. |

No other claims are defined in v1. An implementer may ignore unrecognized
claims but must not require them.

### 3a. Exact signing-input byte construction

This is standard JWS compact serialization (RFC 7515 §5.1), stated
explicitly here rather than left to "the reader already knows JWS":

1. Serialize the protected header (section 3) to JSON. (Exact key order
   and whitespace are implementation-defined for the header — unlike the
   artifact canonicalization in section 2, the header is not separately
   canonicalized. Two different, both-conformant signers may legally
   produce byte-different headers for otherwise-identical claims; the
   fixed point is how each one's own bytes get folded into the signing
   input, not that all signers must agree on header byte-identity.)
2. Base64url-encode (no padding) the UTF-8 bytes of that header JSON →
   `encoded_header`.
3. Serialize the claims (section 3) to JSON (any valid JSON serialization
   of the claims is acceptable here — this is **not** the artifact
   canonicalization from section 2, which is a separate, narrower
   transformation that only ever applies to computing `artifact_digest`
   as one claim's *value*, not to the claims object as a whole).
4. Base64url-encode (no padding) the UTF-8 bytes of that claims JSON →
   `encoded_payload`.
5. The **signing input** is the ASCII bytes of
   `encoded_header + "." + encoded_payload` (a literal period joining the
   two base64url strings).
6. The Ed25519 signature is computed over exactly those ASCII bytes, using
   the signer's private key.
7. Base64url-encode (no padding) the raw 64-byte Ed25519 signature →
   `encoded_signature`.
8. The complete `proof.jws` string is
   `encoded_header + "." + encoded_payload + "." + encoded_signature`.

A verifier reverses steps 2–4 to recover the header and claims, then
independently reconstructs the signing input from the token's own first two
dot-separated segments (never from its own re-serialization of the parsed
claims, which could legally differ byte-for-byte from the original — see
step 1's note) and verifies the signature in step 6 against that
reconstructed input.

### 3b. Base64url encoding

All base64url values in this specification (JWS segments, and the JWK `x`
member in section 5) use the URL-safe alphabet (`-`/`_` instead of `+`/`/`)
**without padding** (`=` characters stripped), per RFC 7515/RFC 8037. A
verifier must accept unpadded input; it must not require padding.

### 3c. Algorithm restriction

Only `EdDSA` (Ed25519) is accepted, both in the header's `alg` and as the
actual verification algorithm a verifier configures its JWT library with.
A verifier must configure an explicit algorithm allowlist containing only
`EdDSA` — never accept whatever `alg` the token itself claims (that would
allow an `alg: none` or algorithm-confusion attack). The reference
implementation does this by passing `algorithms=["EdDSA"]` to its JWT
library's decode call.

## 4. Verification algorithm (normative order)

A verifier, given an artifact carrying a `proof`, must perform the
following checks, in this order, and reject on the first failure (do not
"best-effort" skip a check — each one guards against a distinct, real
attack found during this network's own development):

1. **Presence.** `proof` and `proof.jws` must both be present. Reject if
   either is missing.
2. **Parse.** The JWS header and claims must parse. Reject on malformed
   input.
3. **`kid`/`iss` present.** The header must carry a `kid`; the claims must
   carry an `iss`. Reject if either is absent — both are needed to resolve
   a key (section 5) before the signature itself can even be checked.
4. **Key resolution.** Resolve the Ed25519 public key for `(iss, kid)` via
   the issuer's published key set (section 5). Reject if no *currently
   valid* key is found for that pair — this single failure mode covers
   both "this key never existed" and "this key was revoked/expired," which
   a verifier must treat identically (reject), never distinguish in a way
   that leaks which case occurred to an unauthenticated caller.
5. **Signature.** Verify the JWS signature (section 3a) against the
   resolved public key, algorithm `EdDSA` only (section 3c — reject any
   other `alg`, including `none`). Also verify standard JWT `aud` (must
   equal the verifier's own organization id) and `exp` (must not be
   expired) as part of this step — most JWT libraries do this as part of
   signature verification when given the expected audience.
6. **Digest / tamper check.** Recompute the artifact digest (section 2)
   over the artifact actually received, with its `proof` field removed, and
   compare to the claims' `artifact_digest`. Reject on mismatch — this
   catches any post-signing modification to the artifact, including a
   modification to a field that happens to also be covered by one of the
   explicit scope claims below.
7. **Scope claims.** Independently compare `neg`, the payload `typ`, `ver`,
   `hash` in the verified claims against the corresponding fields on the
   artifact itself. Reject on any mismatch. (In practice a mismatch here
   also fails step 6, since those fields are part of what gets digested —
   this check exists to give a more specific failure reason, and as
   defense in depth if a future artifact type ever digests a transformed
   view of its content rather than the raw fields.)

Everything past this point (replay-cache lookup by `jti`, confirming the
claimed sender is a registered/active agent with an active trading
relationship to the verifier, negotiation-participant binding) is
deployment-specific business logic, not part of this cryptographic
specification — see `docs/PROTOCOLS.md` and `docs/DECISIONS.md` ADR-012.

## 5. Key resolution and revocation

A verifier resolves `(organization_id, kid) -> Ed25519 public key` by
fetching `GET {network_api_base_url}/organizations/{organization_id}/jwks.json`,
a standard JWK Set (RFC 7517) response:

```json
{"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "...", "x": "<base64url, unpadded>", "use": "sig", "alg": "EdDSA"}]}
```

`x` is the raw 32-byte Ed25519 public key, base64url-encoded without
padding, per RFC 8037 (see section 3b for the general base64url rule).

**Revocation is enforced by absence**, not by a separate revocation list:
a key that is revoked or expired is simply no longer present in this
response. A verifier must re-fetch (or expire its own cache of) this
response on a bounded interval — the reference implementation uses 5
minutes — so that a revocation takes effect network-wide within that
bound. A verifier that caches this response indefinitely does not conform
to this specification, because it can be made to trust a key the issuing
organization has already revoked.

This document does not yet specify a **historical** key archive (for
re-verifying a signature made under a since-rotated key, for audit
purposes after the fact) or **per-agent delegated keys** (as opposed to
one key per organization) — both are explicitly out of scope for this v1
and are tracked as later M8 work (see `docs/DECISIONS.md` ADR-015 and the
M8 section of `TASKS.md`). An implementer building against v1 should not
assume either exists yet. (Note: the *registry-api* registration/
verification challenge mechanism described in ADR-015 sections A/D is a
**separate** mechanism, for proving control of an agent-registration key
at onboarding time — it is not part of this artifact-signing
specification and uses its own, differently-structured challenge
documents, never the JWS construction in section 3.)

## 6. Worked example and machine-checkable test vectors

A full worked example, plus machine-checkable test vectors, are provided as
plain JSON files in [`test-vectors/`](test-vectors/) alongside this
document, with [`test-vectors/README.md`](test-vectors/README.md)
describing their exact shape:

- `vector_01` — verify-success, built from a precomputed signature.
- `vector_02` — verify-failure: tampered artifact (digest mismatch).
- `vector_03` — verify-failure: wrong key (well-formed signature, wrong
  signer).
- `vector_04` — the integer/float coercion case from section 2, step 2.
- `vector_05` — verify-success with non-ASCII (Unicode) content in an
  artifact field, demonstrating the `ensure_ascii` escaping from section
  2d.
- `vector_06` — rejection: raw JSON text containing a duplicate object key
  (section 2f), demonstrating `strict_json_loads`.

These are the same vectors this repository's own pytest suite
(`packages/message-integrity/tests/test_jws_vectors.py`) runs against the
Python reference implementation, **and** the same vectors the independent
Node.js harness (section 7) runs against a from-scratch TypeScript
implementation built only from this document.

## 7. Cross-language conformance harness

[`test-vectors/cross-lang-harness/`](test-vectors/cross-lang-harness/) is a
minimal, standalone Node.js/TypeScript program with **zero dependency on
any AIMO Python package** — it re-implements canonicalization and JWS
verification from this document alone, using only Node's built-in `crypto`
module. It:

- Verifies every vector in section 6 produced by the Python reference
  implementation.
- Generates a fresh artifact and signature entirely in Node.js, which the
  Python reference implementation (`verify_artifact_proof`) independently
  verifies — proving the round trip works in both directions, not just
  one.
- Exercises the Unicode and integer/float-coercion edge cases itself, and
  confirms it rejects tampered, malformed, and wrong-key signatures.

See that directory's own `README.md` for how to run it. As stated in the
status note at the top of this document, passing this harness demonstrates
**Python ↔ Node.js** interoperability only — it is not a substitute for a
third language's own independent implementation.
