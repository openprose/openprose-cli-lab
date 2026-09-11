import taxonomy from "../../../shared/errors/taxonomy.v1.json" with { type: "json" };
import { RunnerFailure, type RunnerErrorCode, type RunnerErrorShape } from "./types";

export function failure(code: RunnerErrorCode, details?: Record<string, unknown>): RunnerFailure {
  const definition = taxonomy.errors.find((item) => item.code === code);
  if (definition === undefined) throw new Error(`Missing shared error taxonomy entry for ${code}`);
  return new RunnerFailure({
    code,
    boundary: definition.boundary as RunnerErrorShape["boundary"],
    message: definition.message,
    action: definition.action,
    exitCode: definition.exitCode,
    retryable: definition.retryable,
    ...(details === undefined ? {} : { details }),
  });
}

export function invocationFailure(reason: string): RunnerFailure {
  return failure("INVOCATION_INVALID", { reason });
}
