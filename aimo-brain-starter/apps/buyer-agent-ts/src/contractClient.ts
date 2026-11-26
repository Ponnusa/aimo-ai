// HTTP client for the PUBLISHED external contract only
// (docs/specs/external-integration-openapi.yaml). Every path, method,
// auth scheme and response shape here must trace back to that document
// -- this file is the one place in this package allowed to know the
// registry-api/network-api URL shapes, and it never reaches for any
// AIMO-internal secret (network_admin_api_key / network_operator_key /
// network_internal_service_key).

export class ContractError extends Error {
  constructor(message: string, public readonly status?: number, public readonly body?: unknown) {
    super(message);
  }
}

async function requestJson(
  url: string,
  init: RequestInit & { expect?: number[] } = {},
): Promise<{ status: number; body: any }> {
  const response = await fetch(url, init);
  let body: any = undefined;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  const expect = init.expect ?? [200];
  if (!expect.includes(response.status)) {
    throw new ContractError(`${init.method ?? "GET"} ${url} -> ${response.status}`, response.status, body);
  }
  return { status: response.status, body };
}

export interface ContractClientConfig {
  registryApiUrl: string;
  networkApiUrl: string;
}

export class ExternalContractClient {
  constructor(private readonly cfg: ContractClientConfig) {}

  // -- network-api: org-scoped-credential-authenticated management ----

  async issueServiceCredential(
    organizationId: string,
    orgScopedCredential: string,
    opts: { scope?: string; ttlSeconds?: number } = {},
  ): Promise<{ token: string; organization_id: string; scope: string; expires_in_seconds: number }> {
    const { body } = await requestJson(
      `${this.cfg.networkApiUrl}/organizations/${organizationId}/service-credentials`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${orgScopedCredential}`, "Content-Type": "application/json" },
        body: JSON.stringify({ scope: opts.scope ?? "manage_agents", ttl_seconds: opts.ttlSeconds ?? 3600 }),
      },
    );
    return body;
  }

  async registerSigningKey(
    organizationId: string,
    orgScopedCredential: string,
    kid: string,
    publicKeyJwk: Record<string, unknown>,
  ): Promise<{ organization_id: string; kid: string; status: string }> {
    const { body } = await requestJson(`${this.cfg.networkApiUrl}/organizations/${organizationId}/signing-keys`, {
      method: "POST",
      headers: { Authorization: `Bearer ${orgScopedCredential}`, "Content-Type": "application/json" },
      body: JSON.stringify({ kid, public_key_jwk: publicKeyJwk }),
    });
    return body;
  }

  async publishDelegation(
    organizationId: string,
    orgScopedCredential: string,
    jws: string,
  ): Promise<{ jti: string; status: string }> {
    const { body } = await requestJson(`${this.cfg.networkApiUrl}/organizations/${organizationId}/delegations`, {
      method: "POST",
      headers: { Authorization: `Bearer ${orgScopedCredential}`, "Content-Type": "application/json" },
      body: JSON.stringify({ jws }),
    });
    return body;
  }

  async revokeDelegation(organizationId: string, orgScopedCredential: string, jti: string): Promise<void> {
    await requestJson(`${this.cfg.networkApiUrl}/organizations/${organizationId}/delegations/${jti}/revoke`, {
      method: "POST",
      headers: { Authorization: `Bearer ${orgScopedCredential}` },
    });
  }

  async getRelationshipStatus(
    organizationId: string,
    orgScopedCredential: string,
    counterpartyOrganizationId: string,
  ): Promise<{ exists: boolean; status: string | null }> {
    const { body } = await requestJson(
      `${this.cfg.networkApiUrl}/organizations/${organizationId}/relationship-status/${counterpartyOrganizationId}`,
      { headers: { Authorization: `Bearer ${orgScopedCredential}` }, expect: [200] },
    );
    return body;
  }

  /** Returns the raw HTTP status for callers that need to assert denial
   * (negative tests) without throwing. */
  async getRelationshipStatusRaw(
    organizationId: string,
    orgScopedCredential: string,
    counterpartyOrganizationId: string,
  ): Promise<{ status: number; body: any }> {
    return requestJson(
      `${this.cfg.networkApiUrl}/organizations/${organizationId}/relationship-status/${counterpartyOrganizationId}`,
      { headers: { Authorization: `Bearer ${orgScopedCredential}` }, expect: [200, 401] },
    );
  }

  // -- public, unauthenticated reads -----------------------------------

  async getJwks(organizationId: string): Promise<{ keys: Record<string, any>[] }> {
    const { body } = await requestJson(`${this.cfg.networkApiUrl}/organizations/${organizationId}/jwks.json`);
    return body;
  }

  async getDelegationsJson(organizationId: string): Promise<{ delegations: string[] }> {
    const { body } = await requestJson(`${this.cfg.networkApiUrl}/organizations/${organizationId}/delegations.json`);
    return body;
  }

  async discoverAgents(
    requestingOrganizationId: string,
    skillId?: string,
  ): Promise<Array<{ agent_id: string; organization_id: string; status: string; a2a_endpoint_url: string }>> {
    const params = new URLSearchParams({ requesting_organization_id: requestingOrganizationId });
    if (skillId) params.set("skill_id", skillId);
    const { body } = await requestJson(`${this.cfg.registryApiUrl}/agents?${params.toString()}`);
    return body;
  }

  // -- registry-api: ServiceCredential-authenticated agent management --

  async registerAgent(
    serviceCredential: string,
    params: {
      agentId: string;
      organizationId: string;
      a2aCardUrl: string;
      displayName?: string;
      signingPublicKeyJwk?: Record<string, unknown>;
    },
  ): Promise<{ agent_id: string; status: string }> {
    const { body } = await requestJson(`${this.cfg.registryApiUrl}/agents`, {
      method: "POST",
      headers: { Authorization: `Bearer ${serviceCredential}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_id: params.agentId,
        organization_id: params.organizationId,
        a2a_card_url: params.a2aCardUrl,
        display_name: params.displayName ?? null,
        signing_public_key_jwk: params.signingPublicKeyJwk ?? null,
      }),
    });
    return body;
  }

  async startVerification(
    serviceCredential: string,
    agentId: string,
    organizationId: string,
  ): Promise<{ agent_id: string; endpoint_challenge: string; key_challenge: string | null; challenge_path: string }> {
    const { body } = await requestJson(
      `${this.cfg.registryApiUrl}/agents/${agentId}/verification/start?organization_id=${organizationId}`,
      { method: "POST", headers: { Authorization: `Bearer ${serviceCredential}` } },
    );
    return body;
  }

  async confirmVerification(
    serviceCredential: string,
    agentId: string,
    organizationId: string,
    keyProofSignatureBase64?: string,
  ): Promise<{ agent_id: string; status: string }> {
    const { body } = await requestJson(
      `${this.cfg.registryApiUrl}/agents/${agentId}/verification/confirm?organization_id=${organizationId}`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${serviceCredential}`, "Content-Type": "application/json" },
        body: JSON.stringify(
          keyProofSignatureBase64 ? { key_proof_signature: keyProofSignatureBase64 } : {},
        ),
      },
    );
    return body;
  }
}
