// A minimal real HTTP server this reference agent runs for itself, so
// registry-api can independently fetch (a) its Agent Card and (b) the
// endpoint-control well-known challenge it is asked to serve (ADR-015/
// 016). This is the one piece of "server" the buyer agent needs -- it
// never receives inbound negotiation traffic (negotiation here is
// synchronous request/response, driven entirely by this agent as a
// client, mirroring buyer_agent.client's own pattern).
import { createServer, type Server } from "node:http";

const CHALLENGE_WELL_KNOWN_PATH = "/.well-known/aimo-brain-challenge";

export interface ChallengeServerHandle {
  baseUrl: string;
  setChallenge(value: string | null): void;
  close(): Promise<void>;
}

export function startChallengeServer(port: number, agentCard: Record<string, unknown>): ChallengeServerHandle {
  let currentChallenge: string | null = null;

  const server: Server = createServer((req, res) => {
    if (req.url === "/.well-known/agent-card.json") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(agentCard));
      return;
    }
    if (req.url === CHALLENGE_WELL_KNOWN_PATH) {
      if (currentChallenge === null) {
        res.writeHead(404);
        res.end();
        return;
      }
      res.writeHead(200, { "Content-Type": "text/plain" });
      res.end(currentChallenge);
      return;
    }
    res.writeHead(404);
    res.end();
  });
  server.listen(port, "127.0.0.1");

  return {
    baseUrl: `http://127.0.0.1:${port}`,
    setChallenge(value: string | null) {
      currentChallenge = value;
    },
    close() {
      return new Promise((resolve) => server.close(() => resolve()));
    },
  };
}
