// M8 slice 5 implementation step 2 (ADR-020, required gate before any
// negotiation logic is written): a minimal, real exchange between the
// official @a2a-js/sdk client and AIMO's real, unmodified Python
// supplier-agent, over genuine HTTP. product_availability_request is
// the simplest viable probe -- unauthenticated, no signing/delegation
// setup required -- so a failure here is unambiguously a transport/SDK
// incompatibility, not a credential or policy problem.
import { connectToAgent, sendDataMessage } from "./a2aClient.js";

async function main(): Promise<void> {
  const supplierUrl = process.env.SUPPLIER_A2A_URL;
  if (!supplierUrl) {
    throw new Error("SUPPLIER_A2A_URL must be set to the real, running supplier-agent's base URL.");
  }

  const client = await connectToAgent(supplierUrl);
  console.log(`probe: negotiated A2A protocol version = ${client.protocolVersion}`);

  const requestPayload = {
    schema: "https://schemas.aimobrain.example/food-wholesale/product_availability_request/v0.1",
    type: "product_availability_request",
    version: 1,
    buyer_organization_id: process.env.PROBE_BUYER_ORGANIZATION_ID ?? "org-probe-external",
    lines: [{ product_query: "lactose-free milk", quantity: "100", unit: "L" }],
  };

  const { responsePayload } = await sendDataMessage(client, requestPayload, "probe-agent-ts");
  const result = responsePayload as { type?: string; matches?: unknown[] };
  if (result.type !== "product_availability_response") {
    throw new Error(`Unexpected response type: ${JSON.stringify(result)}`);
  }

  console.log("probe: PASS");
  console.log(`probe: negotiated_protocol_version=${client.protocolVersion}`);
  console.log(`probe: response=${JSON.stringify(result)}`);
}

main().catch((exc) => {
  console.error("probe: FAIL");
  console.error(exc);
  process.exit(1);
});
