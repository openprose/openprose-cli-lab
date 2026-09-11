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
      malformed("Harness stdout exceeded the aggregate structured-output limit.");
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
      malformed("Harness emitted a structured record larger than the per-record limit.");
    }
  }

  finish(): void {
    if (this.finished) malformed("Structured EOF was finalized more than once.");
    this.finished = true;
    if (this.pending.byteLength !== 0) {
      throw failure("PROTOCOL_TRUNCATED", { reason: "EOF occurred in the middle of a JSONL record." });
    }
  }
}

async function decodeRecord(
  bytes: Uint8Array,
  maxRecordBytes: number,
  onRecord: (record: unknown) => void | Promise<void>,
): Promise<void> {
  if (bytes.byteLength === 0) malformed("Harness emitted an empty JSONL record.");
  if (bytes.byteLength > maxRecordBytes) {
    malformed("Harness emitted a structured record larger than the per-record limit.");
  }
  let text: string;
  try {
    text = decoder.decode(bytes);
  } catch {
    malformed("Harness emitted a structured record that is not valid UTF-8.");
  }
  let record: unknown;
  try {
    record = JSON.parse(text);
  } catch {
    malformed("Harness emitted invalid JSONL.");
  }
  if (record === null || typeof record !== "object" || Array.isArray(record)) {
    malformed("Harness emitted a JSONL value that is not an object.");
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
  if (caught instanceof RunnerFailure) return caught;
  return failure("PROTOCOL_MALFORMED", {
    reason: caught instanceof Error ? caught.message : "Unknown JSONL framing failure.",
  });
}
