# Cross-language JWS conformance harness (Python ↔ Node.js)

A minimal, standalone Node.js program with **zero dependency on any AIMO
Python package or npm package** — only Node's built-in `node:crypto` and
`node:fs`/`node:path`/`node:url`. It re-implements
[`../../jws-artifact-signatures.md`](../../jws-artifact-signatures.md)'s
canonicalization (`canonicalize.mjs`) and JWS sign/verify (`jws.mjs`) from
that document alone.

**What this proves, precisely:** Python ↔ Node.js interoperability for this
one specification. It does **not** prove interoperability with any other
language or runtime — see the spec document's status note.

## Running it

Requires Node.js 18+ (uses `node:crypto`'s `sign`/`verify`/`createPublicKey`
with native Ed25519 support, and ES modules). No `npm install` needed —
there are no dependencies.

```sh
node verify-vectors.mjs    # verifies every Python-generated vector in ../
node generate-artifact.mjs # signs a fresh artifact entirely in Node.js
```

or `npm test` (just runs both of the above in sequence).

`generate-artifact.mjs` writes `node-generated-artifact.json` in this
directory. The Python reference implementation's own test suite
(`packages/message-integrity/tests/test_jws_vectors.py::
test_node_generated_artifact_verifies_in_python`) loads that same file and
verifies it with `message_integrity.proofs.verify_artifact_proof` — proving
the **Node.js → Python** direction, complementing `verify-vectors.mjs`'s
**Python → Node.js** direction. Re-run `generate-artifact.mjs` any time you
want a fresh signature (it generates a new Ed25519 keypair every run); the
committed copy is a fixture, not a one-time artifact.

## Files

| File | Purpose |
|---|---|
| `canonicalize.mjs` | Spec section 2: sorted-key, no-whitespace, `\uXXXX`-escaped, NaN/Infinity-rejecting canonical JSON. |
| `jws.mjs` | Spec section 3/3a/4: JWS compact-serialization sign/verify, EdDSA only, explicit signing-input byte construction. |
| `verify-vectors.mjs` | Loads and checks every vector in `../` (vectors 01–06) against this implementation. |
| `generate-artifact.mjs` | Signs a fresh artifact entirely in Node.js, for Python to verify. |

## Known simplifications (stated plainly, not hidden)

- `verify-vectors.mjs`'s duplicate-key scanner (for vector 06) is a
  depth-aware scan sufficient for that vector's flat object shape, not a
  general-purpose recursive-at-every-level JSON tokenizer. A production
  implementation handling arbitrarily nested untrusted JSON should use a
  real streaming/tokenizing parser with duplicate-key detection, not this
  harness's minimal scanner.
- This harness does not implement JWKS fetching/caching/revocation
  (spec section 5) — it resolves keys from a plain in-memory map taken
  directly from each vector file, since exercising the cryptographic
  conformance of section 2–4 is this harness's whole purpose.
