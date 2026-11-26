// Re-implementation of docs/specs/jws-artifact-signatures.md section 2,
// from the spec document and the existing, already-reviewed Node.js
// reference implementation at
// docs/specs/test-vectors/cross-lang-harness/canonicalize.mjs -- not
// from any AIMO Python source. Deliberately identical logic, ported to
// TypeScript, so this reference agent and that cross-language
// conformance harness never drift against each other.

function escapeString(value: string): string {
  let out = '"';
  for (const ch of value) {
    const code = ch.codePointAt(0)!;
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\b") out += "\\b";
    else if (ch === "\f") out += "\\f";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (code < 0x20 || code > 0x7e) {
      for (const unit of ch) {
        out += "\\u" + unit.charCodeAt(0).toString(16).padStart(4, "0");
      }
    } else {
      out += ch;
    }
  }
  return out + '"';
}

function serializeNumber(value: number): string {
  if (Number.isNaN(value) || !Number.isFinite(value)) {
    throw new Error("NaN/Infinity is not permitted in a canonicalized artifact.");
  }
  if (!Number.isInteger(value)) {
    throw new Error(`Non-integer numeric value ${value} is not defined by the canonicalization spec.`);
  }
  return String(value);
}

export function canonicalize(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return serializeNumber(value);
  if (typeof value === "string") return escapeString(value);
  if (Array.isArray(value)) {
    return "[" + value.map(canonicalize).join(",") + "]";
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    return "{" + keys.map((k) => escapeString(k) + ":" + canonicalize(obj[k])).join(",") + "}";
  }
  throw new Error(`Cannot canonicalize value of type ${typeof value}`);
}
