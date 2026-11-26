// This reference agent's own Agent Card, served by challengeServer.ts
// at /.well-known/agent-card.json for registry-api to fetch during
// registration/verification -- built to the same real A2A wire shape a
// running Python a2a-sdk agent serves (verified directly against a real
// supplier-agent instance during implementation: supportedInterfaces[],
// capabilities.streaming, defaultInputModes/defaultOutputModes, camelCase
// throughout), never invented from the Python SDK's internal Pydantic
// field names.
export function buildBuyerAgentCard(selfBaseUrl: string): Record<string, unknown> {
  return {
    name: "AIMO BRAIN TypeScript Reference Buyer Agent",
    description:
      "M8 slice 5 (ADR-020): an independently-built, non-Python reference buyer agent proving genuine interoperability against AIMO BRAIN's published external contract.",
    supportedInterfaces: [{ url: selfBaseUrl, protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
    version: "0.1.0",
    capabilities: { streaming: false },
    defaultInputModes: ["application/json"],
    defaultOutputModes: ["application/json"],
    skills: [
      {
        id: "food.buyer_reference_agent",
        name: "TypeScript reference buyer",
        description: "Drives negotiation/commitment as a client; carries no inbound skills of its own.",
        tags: ["food-wholesale", "reference-agent"],
        examples: [],
        inputModes: ["application/json"],
        outputModes: ["application/json"],
      },
    ],
  };
}
