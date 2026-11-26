// Merge-review fix: binding verification. A valid signature establishes
// WHO signed an artifact, never that it belongs to the exact
// transaction the buyer intended -- these pure functions are the
// dedicated check for that, exercised directly here (independent of any
// live negotiation or signature) for all four tampered-binding
// scenarios the review required, plus the correct-binding case for
// each artifact type.
import assert from "node:assert/strict";
import test from "node:test";
import {
  BindingMismatchError,
  checkOrderConfirmationBinding,
  checkProposedAgreementBinding,
  termsContentHash,
} from "../src/binding.js";

const TERMS = { lines: [{ product_ref: "sku-1", quantity: "100", unit: "L", unit_price: "1.20" }], currency: "EUR" };
const REAL_HASH = termsContentHash(TERMS);

test("accepts a proposed_agreement whose binding genuinely matches", () => {
  assert.doesNotThrow(() =>
    checkProposedAgreementBinding(
      { agreement_of_proposal_id: "proposal-2", content_hash: REAL_HASH, payload: TERMS },
      { expectedProposalId: "proposal-2", expectedContentHash: REAL_HASH },
    ),
  );
});

test("tampered binding 1: wrong agreement_of_proposal_id is rejected", () => {
  assert.throws(
    () =>
      checkProposedAgreementBinding(
        { agreement_of_proposal_id: "proposal-WRONG", content_hash: REAL_HASH, payload: TERMS },
        { expectedProposalId: "proposal-2", expectedContentHash: REAL_HASH },
      ),
    BindingMismatchError,
  );
});

test("tampered binding 2: wrong content_hash (payload terms altered) is rejected even with a self-consistent self-reported hash", () => {
  const tamperedTerms = { ...TERMS, currency: "USD" }; // changes the real hash
  const selfReportedHash = termsContentHash(tamperedTerms); // attacker recomputes consistently with their own lie
  assert.throws(
    () =>
      checkProposedAgreementBinding(
        { agreement_of_proposal_id: "proposal-2", content_hash: selfReportedHash, payload: tamperedTerms },
        { expectedProposalId: "proposal-2", expectedContentHash: REAL_HASH },
      ),
    BindingMismatchError,
  );
});

test("tampered binding 2b: self-reported content_hash disagrees with the artifact's own payload (internally inconsistent) is also rejected", () => {
  assert.throws(
    () =>
      checkProposedAgreementBinding(
        { agreement_of_proposal_id: "proposal-2", content_hash: "not-the-real-hash", payload: TERMS },
        { expectedProposalId: "proposal-2", expectedContentHash: REAL_HASH },
      ),
    BindingMismatchError,
  );
});

test("accepts an order_confirmation whose binding genuinely matches", () => {
  assert.doesNotThrow(() =>
    checkOrderConfirmationBinding(
      { payload: { agreement_id: "txn-1", idempotency_key: "idem-1" } },
      { expectedAgreementId: "txn-1", expectedIdempotencyKey: "idem-1" },
    ),
  );
});

test("tampered binding 3: wrong agreement_id is rejected", () => {
  assert.throws(
    () =>
      checkOrderConfirmationBinding(
        { payload: { agreement_id: "txn-WRONG", idempotency_key: "idem-1" } },
        { expectedAgreementId: "txn-1", expectedIdempotencyKey: "idem-1" },
      ),
    BindingMismatchError,
  );
});

test("tampered binding 4: wrong idempotency_key is rejected", () => {
  assert.throws(
    () =>
      checkOrderConfirmationBinding(
        { payload: { agreement_id: "txn-1", idempotency_key: "idem-WRONG" } },
        { expectedAgreementId: "txn-1", expectedIdempotencyKey: "idem-1" },
      ),
    BindingMismatchError,
  );
});

test("termsContentHash excludes a `status` field (e.g. proposed_agreement's NONBINDING marker), matching the published content_hash_semantics", () => {
  const withStatus = { ...TERMS, status: "NONBINDING" };
  assert.equal(termsContentHash(withStatus as any), REAL_HASH);
});
