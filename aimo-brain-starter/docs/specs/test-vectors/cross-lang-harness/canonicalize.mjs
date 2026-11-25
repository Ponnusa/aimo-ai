// Re-implementation of docs/specs/jws-artifact-signatures.md section 2,
// from the spec document alone -- no import of, or reference to, this
// repository's Python source. Node.js built-ins only.

function escapeString(value) {
  let out = '"';
  for (const ch of value) {
    const code = ch.codePointAt(0);
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (ch === "\b") out += "\\b";
    else if (ch === "\f") out += "\\f";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (code < 0x20 || code > 0x7e) {
      // Spec section 2d: every non-ASCII codepoint is escaped as
      // lowercase \uXXXX, matching Python's ensure_ascii=True default --
      // including each half of a surrogate pair for a codepoint outside
      // the Basic Multilingual Plane, since a JS string already stores
      // such a character as a UTF-16 surrogate pair and iterating by
      // `for...of` here yields the whole codepoint, which we then
      // re-split into its UTF-16 code units to match Python's behavior.
      for (const unit of ch) {
        out += "\\u" + unit.charCodeAt(0).toString(16).padStart(4, "0");
      }
    } else {
      out += ch;
    }
  }
  return out + '"';
}

function serializeNumber(value) {
  if (Number.isNaN(value) || !Number.isFinite(value)) {
    // Spec section 2f: NaN/Infinity/-Infinity are not valid JSON and
    // must never appear in a canonicalized artifact.
    throw new Error("NaN/Infinity is not permitted in a canonicalized artifact.");
  }
  if (!Number.isInteger(value)) {
    // Spec section 2e: no conformant artifact ever carries a
    // non-integer JSON number (every monetary/quantity value is a
    // decimal string). This implementation treats one as a protocol
    // violation rather than guessing at cross-language-safe formatting.
    throw new Error(
      `Non-integer numeric value ${value} is not defined by the canonicalization spec.`,
    );
  }
  // JS's Number -> String conversion already renders -0 as "0" and
  // never produces a trailing ".0" for an integer-valued number, so no
  // separate "normalize to int" step is needed the way the Python
  // reference implementation needs one (Python's json.dumps would
  // otherwise render an integer-valued float as "1.0").
  return String(value);
}

export function canonicalize(value) {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return serializeNumber(value);
  if (typeof value === "string") return escapeString(value);
  if (Array.isArray(value)) {
    return "[" + value.map(canonicalize).join(",") + "]";
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return "{" + keys.map((k) => escapeString(k) + ":" + canonicalize(value[k])).join(",") + "}";
  }
  throw new Error(`Cannot canonicalize value of type ${typeof value}`);
}

