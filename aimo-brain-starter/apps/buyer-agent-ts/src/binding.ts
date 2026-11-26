// ADR-020 section 7.3, strengthened per merge review: a valid signature
// establishes WHO signed an artifact -- it does not by itself establish
// that the artifact belongs to the exact transaction the buyer intended
// to authorize. These checks run AFTER signature/delegation
// verification (verify.ts) succeeds, and BEFORE the buyer advances
// negotiation state or sends any subsequent artifact. Pure functions,
// deliberately separate from verify.ts and from buyerAgent.ts's
// orchestration, so they are directly unit-testable without a live
// negotiation.
import { canonicalize } from "./canonicalize.js";
import { createHash } from "node:crypto";

export class BindingMismatchError extends Error {}

/** The food-wholesale domain's own `content_hash` (distinct from the
 * JWS `artifact_digest` claim verify.ts already checks) is defined,
 * per trade_protocol.negotiation.content_hash, as SHA-256 of the
 * canonicalized TERMS object only -- `{lines, currency, delivery_terms?}`
 * -- never the full envelope and never fields such as `status` that a
 * `quote`/`proposed_agreement` payload also carries. This exclusion was
 * previously undocumented in the published manifest; fixed in the same
 * change as this file (see manifest.json's new `content_hash_semantics`
 * section). */
export function termsContentHash(payload: {
  lines: unknown;
  currency: unknown;
  delivery_terms?: unknown;
}): string {
  const terms: Record<string, unknown> = { lines: payload.lines, currency: payload.currency };
  if (payload.delivery_terms !== undefined) terms.delivery_terms = payload.delivery_terms;
  return createHash("sha256").update(canonicalize(terms), "utf8").digest("hex");
}

export interface ProposedAgreementBindingExpectation {
  expectedProposalId: string;
  expectedContentHash: string;
}

/** Checks a received `proposed_agreement` against the buyer's OWN held
 * negotiation state (the proposal_id it accepted and the content_hash
 * it independently computed from the quote it accepted) -- never
 * trusting the artifact's self-reported `content_hash` alone. */
export function checkProposedAgreementBinding(
  proposedAgreement: {
    agreement_of_proposal_id: string;
    content_hash: string;
    payload: { lines: unknown; currency: unknown; delivery_terms?: unknown };
  },
  expectation: ProposedAgreementBindingExpectation,
): void {
  if (proposedAgreement.agreement_of_proposal_id !== expectation.expectedProposalId) {
    throw new BindingMismatchError(
      `proposed_agreement.agreement_of_proposal_id (${proposedAgreement.agreement_of_proposal_id}) ` +
        `does not match the proposal this buyer actually accepted (${expectation.expectedProposalId}).`,
    );
  }
  const recomputed = termsContentHash(proposedAgreement.payload);
  if (recomputed !== expectation.expectedContentHash) {
    throw new BindingMismatchError(
      `proposed_agreement's recomputed content_hash (${recomputed}) does not match the ` +
        `content_hash this buyer agreed to (${expectation.expectedContentHash}).`,
    );
  }
  if (proposedAgreement.content_hash !== expectation.expectedContentHash) {
    throw new BindingMismatchError(
      `proposed_agreement's self-reported content_hash (${proposedAgreement.content_hash}) does not ` +
        `match the content_hash this buyer agreed to (${expectation.expectedContentHash}) -- ` +
        `never trusted on its own, but still checked for an honest mismatch.`,
    );
  }
}

export interface OrderConfirmationBindingExpectation {
  expectedAgreementId: string;
  expectedIdempotencyKey: string;
}

/** Checks a received `order_confirmation` against the buyer's OWN held
 * commitment state (the agreement/negotiation id and the exact
 * idempotency_key it itself sent in order_commit_request) -- a
 * confirmation referencing a different agreement or idempotency key is
 * never accepted as completing THIS transaction, regardless of its
 * signature's validity. */
export function checkOrderConfirmationBinding(
  confirmation: { payload: { agreement_id: string; idempotency_key: string } },
  expectation: OrderConfirmationBindingExpectation,
): void {
  if (confirmation.payload.agreement_id !== expectation.expectedAgreementId) {
    throw new BindingMismatchError(
      `order_confirmation.payload.agreement_id (${confirmation.payload.agreement_id}) does not match ` +
        `this buyer's own agreement id (${expectation.expectedAgreementId}).`,
    );
  }
  if (confirmation.payload.idempotency_key !== expectation.expectedIdempotencyKey) {
    throw new BindingMismatchError(
      `order_confirmation.payload.idempotency_key (${confirmation.payload.idempotency_key}) does not ` +
        `match the idempotency_key this buyer itself sent (${expectation.expectedIdempotencyKey}).`,
    );
  }
}
