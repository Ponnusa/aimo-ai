// Thin wrapper around the official @a2a-js/sdk client, used for every
// A2A exchange this reference agent performs (M8 slice 5, ADR-020).
// Never a hand-rolled JSON-RPC call -- this is the whole point of the
// live-SDK-probe gate: prove the official JS SDK really interoperates
// with AIMO's real, unmodified Python a2a-sdk server.
import { ClientFactory, JsonRpcTransportFactory } from "@a2a-js/sdk/client";
import type { Client } from "@a2a-js/sdk/client";
import { Role, TaskState, type Message, type Part, type SendMessageResult, type Task } from "@a2a-js/sdk";
import { randomUUID } from "node:crypto";

export class AgentUnavailableError extends Error {}
export class TaskNotCompletedError extends Error {}
export class InvalidAgentResponseError extends Error {}

const factory = new ClientFactory({ transports: [new JsonRpcTransportFactory()] });

export async function connectToAgent(baseUrl: string): Promise<Client> {
  try {
    return await factory.createFromUrl(baseUrl);
  } catch (exc) {
    throw new AgentUnavailableError(`Could not reach agent at ${baseUrl}: ${exc}`);
  }
}

function dataPart(value: unknown): Part {
  return {
    content: { $case: "data", value },
    metadata: undefined,
    filename: "",
    mediaType: "application/json",
  };
}

/** Mirrors Python's `new_data_message(data=..., role=Role.ROLE_USER)` plus
 * `message.metadata["sender_agent_id"] = ...` exactly -- a single
 * structured-data Part, sender identified via message metadata, never a
 * native A2A message type. */
export function buildDataMessage(payload: unknown, senderAgentId: string): Message {
  return {
    messageId: randomUUID(),
    contextId: "",
    taskId: "",
    role: Role.ROLE_USER,
    parts: [dataPart(payload)],
    metadata: { sender_agent_id: senderAgentId },
    extensions: [],
    referenceTaskIds: [],
  };
}

/** Sends `payload` as a single data message and returns the first
 * structured data payload of the resulting Task's artifacts -- mirrors
 * `buyer_agent.client._send_and_validate`'s unwrapping exactly, so this
 * reference agent proves the same real wire contract the Python buyer
 * already relies on, not a convenient shortcut. */
export async function sendDataMessage(
  client: Client,
  payload: unknown,
  senderAgentId: string,
): Promise<{ task: Task; responsePayload: unknown }> {
  const message = buildDataMessage(payload, senderAgentId);
  let result: SendMessageResult;
  try {
    result = await client.sendMessage({
      tenant: "",
      message,
      configuration: undefined,
      metadata: undefined,
    });
  } catch (exc) {
    throw new AgentUnavailableError(`sendMessage failed: ${exc}`);
  }

  if (!("status" in result)) {
    throw new TaskNotCompletedError("Agent returned a Message, not a Task.");
  }
  const task = result as Task;
  if (task.status?.state !== TaskState.TASK_STATE_COMPLETED) {
    throw new TaskNotCompletedError(
      `Task ended in state ${TaskState[task.status?.state ?? TaskState.TASK_STATE_UNSPECIFIED]}, not completed.`,
    );
  }
  const parts = task.artifacts.flatMap((artifact) => artifact.parts);
  const dataPartFound = parts.find((part) => part.content?.$case === "data");
  if (!dataPartFound || dataPartFound.content?.$case !== "data") {
    throw new InvalidAgentResponseError("Task completed with no structured data part.");
  }
  return { task, responsePayload: dataPartFound.content.value };
}
