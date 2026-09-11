import { describe, expect, test } from "bun:test";
import { readBoundedJsonLines } from "../src/supervision/jsonl";
import type { TransportLimits } from "../src/supervision/types";

const limits: TransportLimits = {
  maxRecordBytes: 128,
  maxAggregateStdoutBytes: 512,
  maxAggregateStderrBytes: 128,
  maxQueuedRecords: 2,
};

function chunks(...values: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const value of values) controller.enqueue(encoder.encode(value));
      controller.close();
    },
  });
}

describe("bounded JSONL framing", () => {
  test("accepts fragmented UTF-8 records and CRLF without buffering the full stream", async () => {
    const records: unknown[] = [];
    await readBoundedJsonLines(
      chunks('{"message":"雪', ' 🚀"}\r', '\n{"value":', "2}\n"),
      limits,
      (record) => { records.push(record); },
    );
    expect(records).toEqual([{ message: "雪 🚀" }, { value: 2 }]);
  });

  test("applies natural backpressure with only one consumer callback in flight", async () => {
    let active = 0;
    let maximumActive = 0;
    const seen: number[] = [];
    await readBoundedJsonLines(chunks('{"n":1}\n{"n":2}\n{"n":3}\n'), limits, async (record) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await Bun.sleep(2);
      seen.push((record as { n: number }).n);
      active -= 1;
    });
    expect(seen).toEqual([1, 2, 3]);
    expect(maximumActive).toBe(1);
  });

  test.each([
    ["malformed JSON", chunks('{"bad":}\n'), "PROTOCOL_MALFORMED"],
    ["truncated EOF", chunks('{"unfinished":'), "PROTOCOL_TRUNCATED"],
    ["empty record", chunks("\n"), "PROTOCOL_MALFORMED"],
    ["oversized record", chunks(`${JSON.stringify({ value: "x".repeat(130) })}\n`), "PROTOCOL_MALFORMED"],
    ["aggregate overflow", chunks(`${JSON.stringify({ a: "x".repeat(80) })}\n`, `${JSON.stringify({ b: "y".repeat(80) })}\n`), "PROTOCOL_MALFORMED"],
  ])("rejects %s deterministically", async (_label, stream, code) => {
    const constrained = _label === "aggregate overflow" ? { ...limits, maxAggregateStdoutBytes: 150 } : limits;
    await expect(readBoundedJsonLines(stream, constrained, () => {})).rejects.toMatchObject({ code });
  });
});
