// ADR-020 section 9, test 1: an untrusted verification host named by a
// Trust Extension descriptor must never be contacted, must never
// receive a credential, and a redirect from the real trust anchor back
// toward it must not be followed. Pure unit test -- no real servers
// needed, since the whole point is proving the stub is NEVER reached.
import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { fetchFromTrustedIdentityAuthority, UntrustedHostError } from "../src/trustExtension.js";

test("never contacts the host named by a descriptor's endpoint, even when it looks authoritative", async () => {
  let stubWasContacted = false;
  let stubReceivedCredential: string | undefined;
  const stub = createServer((req, res) => {
    stubWasContacted = true;
    stubReceivedCredential = req.headers.authorization;
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "active", agent_id: "forged" }));
  });
  await new Promise<void>((resolve) => stub.listen(0, "127.0.0.1", resolve));
  const stubPort = (stub.address() as { port: number }).port;

  let trustedWasContacted = false;
  const trusted = createServer((req, res) => {
    trustedWasContacted = true;
    assert.equal(req.headers.authorization, "Bearer real-credential");
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify([{ agent_id: "real" }]));
  });
  await new Promise<void>((resolve) => trusted.listen(0, "127.0.0.1", resolve));
  const trustedPort = (trusted.address() as { port: number }).port;

  const maliciousDescriptor = {
    verification_sources: {
      identity: { endpoint: `http://127.0.0.1:${stubPort}/agents?malicious=1` },
    },
  };

  const response = await fetchFromTrustedIdentityAuthority(
    { registryApiUrl: `http://127.0.0.1:${trustedPort}`, networkApiUrl: "" },
    maliciousDescriptor,
    "real-credential",
  );

  assert.equal(stubWasContacted, false, "the descriptor-supplied host must never be contacted");
  assert.equal(stubReceivedCredential, undefined, "no credential may ever reach the untrusted host");
  assert.equal(trustedWasContacted, true, "the verifier's own trusted authority must be the one actually called");
  assert.equal(response.status, 200);

  await new Promise((resolve) => stub.close(resolve));
  await new Promise((resolve) => trusted.close(resolve));
});

test("refuses to follow a redirect from the trusted authority toward an untrusted host", async () => {
  const trusted = createServer((req, res) => {
    res.writeHead(302, { Location: "http://attacker.invalid/agents" });
    res.end();
  });
  await new Promise<void>((resolve) => trusted.listen(0, "127.0.0.1", resolve));
  const trustedPort = (trusted.address() as { port: number }).port;

  await assert.rejects(
    () =>
      fetchFromTrustedIdentityAuthority(
        { registryApiUrl: `http://127.0.0.1:${trustedPort}`, networkApiUrl: "" },
        {},
        "real-credential",
      ),
    UntrustedHostError,
  );

  await new Promise((resolve) => trusted.close(resolve));
});
