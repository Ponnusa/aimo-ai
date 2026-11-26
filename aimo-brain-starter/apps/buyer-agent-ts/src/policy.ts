// This reference agent's OWN, independent authorization decision
// (ADR-020 section 6). Deliberately not a copy of
// apps/buyer-agent/src/buyer_agent/authorization.py's OPA-bundle path --
// that is AIMO-internal infrastructure this package must never import
// or call. A genuine decision: explicit, documented limits, evaluated
// against the real negotiated terms, with a digest computed from the
// actual inputs the decision used -- never a hand-crafted `allow: true`
// with no decision behind it.
//
// Supplier-side OPA approval never substitutes for this: each side's
// authorization_evidence reflects ONLY that side's own decision.
// policy_version/policy_input_digest are opaque strings from the wire
// protocol's point of view (industries/food-wholesale/schemas/
// authorization_evidence.schema.json requires them only as strings) --
// this is not, and must never be described as, a cross-organization
// attestation of correctness.
import { createHash } from "node:crypto";

export const POLICY_VERSION = "buyer-ts-reference-policy-v1";

/** Explicit, documented limits -- the whole policy, in one place. */
export const POLICY_LIMITS = {
  maxUnitPriceEur: 2.0,
  maxTotalQuantity: 500,
};

export interface QuoteLine {
  product_ref: string;
  quantity: string;
  unit: string;
  unit_price: string;
}

export interface PolicyDecision {
  allow: boolean;
  denyReason: string | null;
  policyVersion: string;
  policyInputDigest: string;
}

/** Evaluates the quote's own negotiated lines against this
 * organization's own explicit limits. Deterministic: the same lines
 * always produce the same decision and the same digest. */
export function evaluateBuyerPolicy(lines: QuoteLine[], currency: string): PolicyDecision {
  const input = { lines, currency, limits: POLICY_LIMITS };
  const policyInputDigest = createHash("sha256")
    .update(JSON.stringify(input, Object.keys(input).sort()))
    .digest("hex");

  if (currency !== "EUR") {
    return {
      allow: false,
      denyReason: `Unsupported currency ${currency}; this policy only evaluates EUR.`,
      policyVersion: POLICY_VERSION,
      policyInputDigest,
    };
  }

  let totalQuantity = 0;
  for (const line of lines) {
    const unitPrice = Number.parseFloat(line.unit_price);
    const quantity = Number.parseFloat(line.quantity);
    if (unitPrice > POLICY_LIMITS.maxUnitPriceEur) {
      return {
        allow: false,
        denyReason: `unit_price ${line.unit_price} for ${line.product_ref} exceeds the maximum of ${POLICY_LIMITS.maxUnitPriceEur} EUR.`,
        policyVersion: POLICY_VERSION,
        policyInputDigest,
      };
    }
    totalQuantity += quantity;
  }
  if (totalQuantity > POLICY_LIMITS.maxTotalQuantity) {
    return {
      allow: false,
      denyReason: `total quantity ${totalQuantity} exceeds the maximum of ${POLICY_LIMITS.maxTotalQuantity}.`,
      policyVersion: POLICY_VERSION,
      policyInputDigest,
    };
  }

  return { allow: true, denyReason: null, policyVersion: POLICY_VERSION, policyInputDigest };
}
