import { failure } from "../core/errors";
import { RunnerFailure } from "../core/types";
import type { TransportLimits } from "./types";

const decoder = new TextDecoder("utf-8", { fatal: true });

export async function readBoundedJsonLines(
  stream: ReadableStream<Uint8Array>,
  limits: TransportLimits,
  onRecord: (record: unknown) => void | Promise<void>,
): Promise<void> {
  const decoder = new BoundedJsonLineDecoder(limits);
  const reader = stream.getReader();

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      await decoder.push(value, onRecord);
    }
    decoder.finish();
  } finally {
    reader.releaseLock();
  }
}

/** Incremental form used when an outer containment protocol carries child JSONL chunks. */
export class BoundedJsonLineDecoder {
  private readonly limits: TransportLimits;
  private pending: Uint8Array<ArrayBufferLike> = new Uint8Array(0);
  private aggregate = 0;
  private finished = false;

  constructor(limits: TransportLimits) {
    validateLimits(limits);
    this.limits = limits;
  }

  async push(
    value: Uint8Array,
    onRecord: (record: unknown) => void | Promise<void>,
  ): Promise<void> {
    if (this.finished) malformed("Harness bytes arrived after structured EOF.");
    this.aggregate += value.byteLength;
    if (this.aggregate > this.limits.maxAggregateStdoutBytes) {
      diagnosed("aggregate-stdout-limit", this.aggregate, this.limits.maxAggregateStdoutBytes);
    }
    this.pending = append(this.pending, value);
    let lineEnd = this.pending.indexOf(0x0a);
    while (lineEnd >= 0) {
      let line = this.pending.subarray(0, lineEnd);
      this.pending = this.pending.slice(lineEnd + 1);
      if (line.byteLength > 0 && line.at(-1) === 0x0d) line = line.subarray(0, line.byteLength - 1);
      await decodeRecord(line, this.limits.maxRecordBytes, onRecord);
      lineEnd = this.pending.indexOf(0x0a);
    }
    if (this.pending.byteLength > this.limits.maxRecordBytes) {
      diagnosed("record-byte-limit", this.pending.byteLength, this.limits.maxRecordBytes);
    }
  }

  finish(): void {
    if (this.finished) malformed("Structured EOF was finalized more than once.");
    this.finished = true;
    if (this.pending.byteLength !== 0) {
      throw failure("PROTOCOL_TRUNCATED", { transportDiagnostic: diagnostic("truncated-record", this.pending.byteLength) });
    }
  }
}

async function decodeRecord(
  bytes: Uint8Array,
  maxRecordBytes: number,
  onRecord: (record: unknown) => void | Promise<void>,
): Promise<void> {
  if (bytes.byteLength === 0) diagnosed("empty-record", 0);
  if (bytes.byteLength > maxRecordBytes) {
    diagnosed("record-byte-limit", bytes.byteLength, maxRecordBytes);
  }
  let text: string;
  try {
    text = decoder.decode(bytes);
  } catch {
    diagnosed("invalid-utf8", bytes.byteLength);
  }
  let record: unknown;
  try {
    record = JSON.parse(text);
  } catch {
    diagnosed("invalid-json", bytes.byteLength);
  }
  if (record === null || typeof record !== "object" || Array.isArray(record)) {
    diagnosed("non-object-record", bytes.byteLength);
  }
  // Awaiting the consumer before pulling again is the bounded queue: Bun's
  // ReadableStream applies natural backpressure and at most one record is in
  // flight. Nothing is buffered in an unbounded application queue.
  await onRecord(record);
}

function append(left: Uint8Array<ArrayBufferLike>, right: Uint8Array<ArrayBufferLike>): Uint8Array<ArrayBufferLike> {
  if (left.byteLength === 0) return right.slice();
  const result = new Uint8Array(left.byteLength + right.byteLength);
  result.set(left);
  result.set(right, left.byteLength);
  return result;
}

function validateLimits(limits: TransportLimits): void {
  if (
    !Number.isSafeInteger(limits.maxRecordBytes) || limits.maxRecordBytes <= 0
    || !Number.isSafeInteger(limits.maxAggregateStdoutBytes) || limits.maxAggregateStdoutBytes < limits.maxRecordBytes
    || !Number.isSafeInteger(limits.maxQueuedRecords) || limits.maxQueuedRecords <= 0
  ) malformed("Structured transport limits are invalid.");
}

function malformed(reason: string): never {
  throw failure("PROTOCOL_MALFORMED", { reason });
}

export function normalizeProtocolFailure(caught: unknown): RunnerFailure {
  if (caught instanceof RunnerFailure) {
    if (caught.code === "PROTOCOL_MALFORMED" && caught.details?.transportDiagnostic === undefined) return failure(caught.code, {...caught.details, transportDiagnostic: diagnostic("lifecycle-rejection")});
    return caught;
  }
  return failure("PROTOCOL_MALFORMED", {
    reason: caught instanceof Error ? caught.message : "Unknown JSONL framing failure.",
  });
}

type DiagnosticReason = "aggregate-stdout-limit" | "record-byte-limit" | "invalid-json" | "invalid-utf8" | "empty-record" | "non-object-record" | "truncated-record" | "lifecycle-rejection";

function diagnostic(reason: DiagnosticReason, observedBytes?: number, limitBytes?: number): Record<string, unknown> {
  const maximum = 4294967295;
  return {
    schema: "openprose.transport-diagnostic/1",
    reason,
    ...(observedBytes === undefined ? {} : { observedBytes: Math.min(observedBytes, maximum) }),
    ...(limitBytes === undefined ? {} : { limitBytes: Math.min(limitBytes, maximum) }),
    ...((observedBytes ?? 0) > maximum || (limitBytes ?? 0) > maximum ? { saturated: true } : {}),
  };
}

function diagnosed(reason: DiagnosticReason, observedBytes?: number, limitBytes?: number): never {
  throw failure("PROTOCOL_MALFORMED", { transportDiagnostic: diagnostic(reason, observedBytes, limitBytes) });
}
