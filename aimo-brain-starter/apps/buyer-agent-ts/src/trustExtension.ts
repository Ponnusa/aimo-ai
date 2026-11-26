// Implements docs/specs/trust-extension.md's host-pinning rule exactly
// (ADR-020 section 7.4, section 9 test 1). A Trust Extension
// descriptor's `verification_sources.*.endpoint` is read ONLY for the
// path/operation it names -- never dereferenced as a host. The actual
// request always goes to this verifier's own, independently configured
// trusted base URL. A credential is attached only when the final
// request host is that same trusted base URL; a redirect is never
// followed.
export class UntrustedHostError extends Error {}

export interface TrustedAuthority {
  registryApiUrl: string;
  networkApiUrl: string;
}

/** Returns the PATH + query of a descriptor-supplied endpoint URL,
 * discarding its scheme/host entirely -- the host is never trusted. */
function pathOnly(descriptorEndpoint: string): string {
  const url = new URL(descriptorEndpoint, "http://ignored-host.invalid");
  return url.pathname + url.search;
}

/** Resolves a Trust Extension descriptor's `verification_sources.identity`
 * entry against THIS verifier's own trusted `registry-api` origin --
 * the descriptor's own host, scheme, and port are read and discarded.
 * `credential`, if provided, is attached ONLY to this request, which by
 * construction can only ever target `authority.registryApiUrl` -- there
 * is no code path in this function that can send it anywhere else. */
export async function fetchFromTrustedIdentityAuthority(
  authority: TrustedAuthority,
  descriptor: { verification_sources?: { identity?: { endpoint?: string } } },
  credential?: string,
): Promise<Response> {
  const descriptorEndpoint = descriptor.verification_sources?.identity?.endpoint;
  const path = descriptorEndpoint ? pathOnly(descriptorEndpoint) : "/agents";
  const url = `${authority.registryApiUrl}${path}`;

  const response = await fetch(url, {
    headers: credential ? { Authorization: `Bearer ${credential}` } : undefined,
    redirect: "manual", // never silently follow a redirect to an untrusted host
  });
  if (response.status >= 300 && response.status < 400) {
    throw new UntrustedHostError(
      `Trusted authority responded with a redirect; refusing to follow it (location may be untrusted).`,
    );
  }
  return response;
}
