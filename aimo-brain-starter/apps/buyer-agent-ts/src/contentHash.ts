// trade_protocol.negotiation.content_hash, ported: SHA-256 of the same
// canonicalization rule used throughout this system. The buyer
// recomputes this itself from the quote's own payload terms rather than
// trusting the supplier's claimed content_hash -- ADR-020 section 7.3.
import { createHash } from "node:crypto";
import { canonicalize } from "./canonicalize.js";

export function contentHash(terms: Record<string, unknown>): string {
  return createHash("sha256").update(canonicalize(terms), "utf8").digest("hex");
}
