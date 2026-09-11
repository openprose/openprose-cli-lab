import Ajv2020 from "ajv/dist/2020";
import { failure } from "../core/errors";
import type { RunnerInvocation, VerifiedRuntimeImage } from "../core/types";
import type { RawTransportEvent } from "../supervision/types";

const decoder = new TextDecoder("utf-8", { fatal: true });

/**
 * Recover the language-owned terminal record from the last nonblank assistant
 * line.  The runner knows only the closed, const-valued JSON shape declared by
 * the verified image; it does not interpret OpenProse or invent a status. If
 * that schema requires `task.argv`, the value must bind to the runner-owned
 * invocation; optional schema fields remain entirely image-governed.
 */
export function recoverImageTerminalEnvelope(
  image: VerifiedRuntimeImage,
  events: readonly RawTransportEvent[],
  invocation: RunnerInvocation,
): Record<string, unknown> {
  const schema = imageTerminalSchema(image);
  const assistantText = events
    .filter((event) => event.type === "assistant.message")
    .map((event) => event.text ?? "")
    .join("\n");
  const lines = assistantText.split("\n");
  while (lines.length > 0 && lines.at(-1)!.trim().length === 0) lines.pop();
  const terminalLine = lines.at(-1);
  if (terminalLine === undefined) {
    throw failure("PROTOCOL_MALFORMED", {
      reason: "The harness completed without an assistant message containing the image terminal envelope.",
    });
  }
  let observed: unknown;
  try {
    observed = JSON.parse(terminalLine);
  } catch {
    throw failure("PROTOCOL_MALFORMED", {
      reason: "The final nonblank assistant line was not the image terminal envelope.",
    });
  }
  if (!isPlainRecord(observed) || JSON.stringify(observed) !== terminalLine) {
    throw failure("PROTOCOL_MALFORMED", {
      reason: "The final assistant terminal envelope is not one exact minified JSON object.",
    });
  }
  let valid: ((value: unknown) => boolean) & { errors?: unknown };
  try {
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    valid = ajv.compile(schema) as typeof valid;
  } catch {
    throw failure("IMAGE_INVALID", { reason: "The image terminal schema cannot be compiled." });
  }
  if (!valid(observed)) {
    throw failure("PROTOCOL_MALFORMED", {
      reason: "The final assistant terminal envelope does not match the verified image-declared schema.",
    });
  }
  if (observed.schema !== image.manifest.terminalEnvelope.schemaId || typeof observed.semanticStatus !== "string") {
    throw failure("PROTOCOL_MALFORMED", {
      reason: "The final assistant terminal identity does not match the verified image manifest.",
    });
  }
  if (schemaRequiresTaskArgv(schema)) {
    const task = isPlainRecord(observed.task) ? observed.task : null;
    if (
      task === null
      || JSON.stringify(task.argv) !== JSON.stringify(invocation.task.argv)
    ) {
      throw failure("PROTOCOL_MALFORMED", {
        reason: "The final assistant terminal does not echo the runner-owned task identity.",
      });
    }
  }
  return observed;
}

export function imageTerminalSchema(image: VerifiedRuntimeImage): Record<string, unknown> {
  const bytes = image.files.get(image.manifest.terminalEnvelope.path);
  if (bytes === undefined) throw failure("IMAGE_INVALID", { reason: "The terminal schema artifact is missing." });
  let parsed: unknown;
  try {
    parsed = JSON.parse(decoder.decode(bytes));
  } catch {
    throw failure("IMAGE_INVALID", { reason: "The terminal schema artifact is not valid UTF-8 JSON." });
  }
  if (!isPlainRecord(parsed)) throw failure("IMAGE_INVALID", { reason: "The terminal schema artifact is not a JSON object." });
  return parsed;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requiredPropertySchema(schema: Record<string, unknown>, name: string): Record<string, unknown> | null {
  if (
    !Array.isArray(schema.required)
    || !schema.required.includes(name)
    || !isPlainRecord(schema.properties)
  ) return null;
  const property = schema.properties[name];
  return isPlainRecord(property) ? property : null;
}

function schemaRequiresTaskArgv(schema: Record<string, unknown>): boolean {
  const task = requiredPropertySchema(schema, "task");
  return task !== null && requiredPropertySchema(task, "argv") !== null;
}

/** No synthetic terminal carrier: called after the native protocol has settled. */
export function nativeOutputText(messages: readonly string[]): string { return messages.join("\n"); }
