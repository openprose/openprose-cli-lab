import { describe, expect, test } from "bun:test";
import primeScenario from "../../shared/fixtures/adapters/scenarios/prime-rpc.v1.json" with { type: "json" };
import { installedProtocol } from "../src/adapters/protocols";

const invocationId = "fixture-invocation-0001";
const fixtureFrames = primeScenario.fakeStdout as unknown[];

function protocol(version = "prime-agent 0.7.0") {
  return installedProtocol("prime/rpc", version, invocationId);
}

function acceptAll(frames: unknown[], version = "prime-agent 0.7.0") {
  const parser = protocol(version);
  return {
    parser,
    events: frames.map((frame) => parser.accept(structuredClone(frame))).filter((event) => event !== null),
  };
}

function expectFailure(action: () => unknown, code: string): void {
  expect(action).toThrow(expect.objectContaining({ code }));
}

function inserted(frames: unknown[], index: number, value: unknown): unknown[] {
  const copy = structuredClone(frames);
  copy.splice(index, 0, value);
  return copy;
}

function textOnlyFrames(): any[] {
  const frames = [...structuredClone(fixtureFrames.slice(0, 6)), ...structuredClone(fixtureFrames.slice(11))] as any[];
  const stripThinking = (message: any): void => {
    if (message?.role === "assistant" && Array.isArray(message.content) && message.content.length === 2) {
      message.content = [message.content[1]];
    }
  };
  for (const frame of frames) {
    if (frame?.type === "message_update" && frame.assistantMessageEvent?.contentIndex === 1) {
      frame.assistantMessageEvent.contentIndex = 0;
    }
    stripThinking(frame?.message);
    if (Array.isArray(frame?.messages)) frame.messages.forEach(stripThinking);
  }
  return frames;
}

describe("Prime 0.7 no-tool RPC lifecycle", () => {
  test("accepts the source-audited text-only lifecycle at content index zero", () => {
    const acceptTextOnly = (frames: unknown[]) => acceptAll(frames, "prime-agent 0.8.1");
    const frames = textOnlyFrames();
    const { parser, events } = acceptTextOnly(frames);
    const assistant = frames.at(-2).message.content[0].text;
    expect(events).toEqual([
      { type: "session.started", harnessVersion: "prime-agent 0.8.1" },
      { type: "assistant.message", text: assistant },
      { type: "session.completed" },
    ]);
    expect(parser.terminalEventObserved).toBeTrue();

    const atomicText = textOnlyFrames();
    atomicText.splice(7, 3);
    expect(() => acceptTextOnly(atomicText)).not.toThrow();

    const emptyDeltasThenAtomicText = textOnlyFrames();
    for (const frame of emptyDeltasThenAtomicText.slice(6)) {
      if (frame?.assistantMessageEvent?.type === "text_delta") {
        frame.assistantMessageEvent.delta = "";
        frame.message.content[0].text = "";
      }
    }
    expect(() => acceptTextOnly(emptyDeltasThenAtomicText)).not.toThrow();

    const invalidFirstContent = textOnlyFrames();
    invalidFirstContent[6].assistantMessageEvent.contentIndex = 1;
    try {
      acceptTextOnly(invalidFirstContent);
      throw new Error("invalid first Prime content unexpectedly passed");
    } catch (caught) {
      expect(caught).toMatchObject({
        details: { adapterDiagnostic: { phase: "await-thinking-or-text-start", counters: { acceptedRecords: 6 } } },
      });
    }
  });

  test("rejects source-valid but functionally unusable or unaudited text-only branches", () => {
    const thinkingOnly = structuredClone(fixtureFrames) as any[];
    thinkingOnly.splice(11, 5);
    const thinkingMessage = thinkingOnly[10].message;
    thinkingOnly[11].message = structuredClone(thinkingMessage);
    thinkingOnly[12].message = structuredClone(thinkingMessage);
    thinkingOnly[13].messages[1] = structuredClone(thinkingMessage);

    const emptyText = textOnlyFrames();
    emptyText.splice(7, 3);
    for (const frame of emptyText.slice(6)) {
      if (frame?.assistantMessageEvent?.type === "text_end") frame.assistantMessageEvent.content = "";
      if (frame?.message?.role === "assistant") frame.message.content[0].text = "";
      if (Array.isArray(frame?.messages) && frame.messages[1]?.role === "assistant") {
        frame.messages[1].content[0].text = "";
      }
    }

    const emptyDeltaText = textOnlyFrames();
    for (const frame of emptyDeltaText.slice(6)) {
      if (frame?.assistantMessageEvent?.type === "text_delta") frame.assistantMessageEvent.delta = "";
      if (frame?.assistantMessageEvent?.type === "text_end") frame.assistantMessageEvent.content = "";
      if (frame?.message?.role === "assistant") frame.message.content[0].text = "";
      if (Array.isArray(frame?.messages) && frame.messages[1]?.role === "assistant") {
        frame.messages[1].content[0].text = "";
      }
    }

    const tool = inserted(textOnlyFrames(), 6, {
      type: "message_update",
      assistantMessageEvent: { type: "toolcall_start", contentIndex: 0 },
      message: {
        ...(textOnlyFrames()[5] as any).message,
        content: [{ type: "toolCall", id: "call-1", name: "tool", arguments: {} }],
      },
    });

    const multipleBlocks = textOnlyFrames();
    multipleBlocks[6].message.content.push({ type: "text", text: "" });

    const postTextThinking = textOnlyFrames();
    const finalText = structuredClone(postTextThinking[10].message.content[0]);
    postTextThinking.splice(11, 0, {
      type: "message_update",
      assistantMessageEvent: { type: "thinking_start", contentIndex: 1 },
      message: {
        ...structuredClone(postTextThinking[10].message),
        content: [finalText, { type: "thinking", thinking: "" }],
      },
    });

    for (const [name, frames] of [
      ["thinking-only", thinkingOnly],
      ["empty-text", emptyText],
      ["empty-delta-text", emptyDeltaText],
      ["tool", tool],
      ["multiple-blocks", multipleBlocks],
      ["post-text-thinking", postTextThinking],
    ] as const) {
      expect(() => acceptAll(frames), name).toThrow(expect.objectContaining({ code: "PROTOCOL_MALFORMED" }));
    }
  });

  test("retains the atomic redacted-thinking then text lifecycle", () => {
    const frames = structuredClone(fixtureFrames) as any[];
    frames.splice(7, 3);
    for (const index of [6, 7]) {
      frames[index].message.content[0].thinkingSignature = "opaque-redacted-reasoning";
      frames[index].message.content[0].redacted = true;
    }
    frames[7].assistantMessageEvent.content = "[Reasoning redacted]";
    frames[7].message.content[0].thinking = "[Reasoning redacted]";
    for (const frame of frames.slice(8)) {
      if (frame?.message?.role === "assistant") {
        frame.message.content[0].thinking = "[Reasoning redacted]";
        frame.message.content[0].thinkingSignature = "opaque-redacted-reasoning";
        frame.message.content[0].redacted = true;
      }
      if (Array.isArray(frame?.messages) && frame.messages[1]?.role === "assistant") {
        frame.messages[1].content[0].thinking = "[Reasoning redacted]";
        frame.messages[1].content[0].thinkingSignature = "opaque-redacted-reasoning";
        frame.messages[1].content[0].redacted = true;
      }
    }
    expect(() => acceptAll(frames)).not.toThrow();
  });

  test("accepts the exact source-audited lifecycle and extracts only the completed assistant text", () => {
    const { parser, events } = acceptAll(fixtureFrames);
    const assistant = (fixtureFrames.at(-2) as { message: { content: Array<{ type: string; text?: string }> } })
      .message.content.find((content) => content.type === "text")!.text;
    if (typeof assistant !== "string") throw new Error("Prime fixture assistant text is absent");
    expect(events).toEqual([
      { type: "session.started", harnessVersion: "prime-agent 0.7.0" },
      { type: "assistant.message", text: assistant },
      { type: "session.completed" },
    ]);
    expect(parser.terminalEventObserved).toBeTrue();
    expect(parser.stdinCloseRequested).toBeTrue();
    const finalMessage = (fixtureFrames.at(-2) as { message: { responseId: string; content: Array<Record<string, unknown>> } }).message;
    expect(finalMessage.responseId).toBe("fixture-response-id");
    expect(finalMessage.content[0]!.thinkingSignature).toBe("fixture-thinking-signature");
    expect(finalMessage.content[1]!.textSignature).toBe("fixture-text-signature");
    const atomicThinking = structuredClone(fixtureFrames);
    atomicThinking.splice(7, 3);
    expect(() => acceptAll(atomicThinking)).not.toThrow();
    const atomicText = structuredClone(fixtureFrames);
    atomicText.splice(12, 3);
    expect(() => acceptAll(atomicText)).not.toThrow();
    const exactCumulativeThinking = structuredClone(fixtureFrames) as any[];
    exactCumulativeThinking[9].assistantMessageEvent.delta = exactCumulativeThinking[9].assistantMessageEvent.delta.slice(0, -2);
    exactCumulativeThinking[9].message.content[0].thinking = exactCumulativeThinking[9].message.content[0].thinking.slice(0, -2);
    expect(() => acceptAll(exactCumulativeThinking)).not.toThrow();
  });

  test("rejects missing, duplicate, out-of-order, unknown, and post-terminal records", () => {
    for (const omitted of [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]) {
      const frames = fixtureFrames.filter((_, index) => index !== omitted);
      expectFailure(() => acceptAll(frames), "PROTOCOL_MALFORMED");
    }

    const duplicate = inserted(fixtureFrames, 2, structuredClone(fixtureFrames[1]));
    expectFailure(() => acceptAll(duplicate), "PROTOCOL_MALFORMED");

    const reordered = structuredClone(fixtureFrames);
    [reordered[3], reordered[5]] = [reordered[5], reordered[3]];
    expectFailure(() => acceptAll(reordered), "PROTOCOL_MALFORMED");

    const unknown = inserted(fixtureFrames, 6, { type: "notice", message: "late" });
    expectFailure(() => acceptAll(unknown), "PROTOCOL_MALFORMED");

    const complete = acceptAll(fixtureFrames).parser;
    expectFailure(() => complete.accept({ type: "notice" }), "PROTOCOL_MALFORMED");
  });

  test("binds the acknowledgement, roles, no-tool turn, and terminal transcript exactly", () => {
    const user = (fixtureFrames[3] as { message: { content: unknown } }).message.content;
    expect(user).toEqual([{ type: "text", text: expect.any(String) }]);
    const mutations: Array<(frames: any[]) => void> = [
      (frames) => { frames[0].id = "wrong"; },
      (frames) => { frames[0].extra = true; },
      (frames) => { frames[3].message.role = "assistant"; },
      (frames) => { frames[3].message.content = "legacy string"; },
      (frames) => { frames[3].message.content = []; },
      (frames) => { frames[3].message.content = [{ type: "text", text: "" }]; },
      (frames) => { frames[3].message.content = [{ type: "text", text: "x", extra: true }]; },
      (frames) => { frames[3].message.content = [{ type: "image", text: "x" }]; },
      (frames) => { frames[4].message.content = "different"; },
      (frames) => { frames[17].toolResults = [{ role: "toolResult" }]; },
      (frames) => { frames[18].messages = []; },
      (frames) => { frames[18].messages[1] = { role: "assistant", content: [] }; },
    ];
    for (const mutate of mutations) {
      const frames = structuredClone(fixtureFrames) as any[];
      mutate(frames);
      expectFailure(() => acceptAll(frames), "PROTOCOL_MALFORMED");
    }

    const rejected = structuredClone(fixtureFrames) as any[];
    rejected[0].success = false;
    expectFailure(() => acceptAll(rejected), "HARNESS_FAILED");
  });

  test("reports a constant-only diagnostic for the audited user-message boundary", () => {
    const frames = structuredClone(fixtureFrames) as any[];
    const secret = "provider-secret-must-not-leak";
    frames[3].message.content = [{ type: "text", text: "", signature: secret }];
    try {
      acceptAll(frames);
      throw new Error("invalid Prime user content unexpectedly passed");
    } catch (caught) {
      expect(caught).toMatchObject({
        code: "PROTOCOL_MALFORMED",
        details: {
          adapterDiagnostic: {
            schema: "openprose.adapter-diagnostic/1",
            adapterId: "prime/rpc",
            stage: "prime-lifecycle",
            phase: "await-user-message-start",
            counters: {
              acceptedRecords: 3,
              thinkingDeltas: 0,
              textDeltas: 0,
              saturated: false,
            },
          },
        },
      });
      expect(JSON.stringify(caught)).not.toContain(secret);
    }
  });

  test("reports only lifecycle state and safe counters for rejected rich deltas", () => {
    const mutations = [
      {
        index: 9,
        pointer: "/assistantMessageEvent/delta",
        phase: "await-thinking-delta-or-end",
        acceptedRecords: 9,
        thinkingDeltas: 2,
        textDeltas: 0,
      },
      {
        index: 13,
        pointer: "/assistantMessageEvent/delta",
        phase: "await-text-delta-or-end",
        acceptedRecords: 13,
        thinkingDeltas: 3,
        textDeltas: 1,
      },
    ] as const;
    for (const mutation of mutations) {
      const frames = structuredClone(fixtureFrames) as any[];
      const secret = `candidate-secret-${mutation.index}`;
      const segments = mutation.pointer.split("/").slice(1);
      let target = frames[mutation.index];
      for (const segment of segments.slice(0, -1)) target = target[segment];
      target[segments.at(-1)!] = secret;
      try {
        acceptAll(frames);
        throw new Error("invalid Prime delta unexpectedly passed");
      } catch (caught) {
        expect(caught).toMatchObject({
          code: "PROTOCOL_MALFORMED",
          details: {
            adapterDiagnostic: {
              schema: "openprose.adapter-diagnostic/1",
              adapterId: "prime/rpc",
              stage: "prime-lifecycle",
              phase: mutation.phase,
              counters: {
                acceptedRecords: mutation.acceptedRecords,
                thinkingDeltas: mutation.thinkingDeltas,
                textDeltas: mutation.textDeltas,
                saturated: false,
              },
            },
          },
        });
        expect(JSON.stringify(caught)).not.toContain(secret);
      }
    }
  });

  test("saturates diagnostic counters without exposing the rejected candidate", () => {
    const parser = protocol();
    (parser as unknown as { acceptedRecords: number }).acceptedRecords = 0xffff_ffff;
    parser.accept(structuredClone(fixtureFrames[0]));
    const secret = "candidate-secret-after-counter-saturation";
    try {
      parser.accept({ candidate: secret });
      throw new Error("invalid Prime candidate unexpectedly passed");
    } catch (caught) {
      expect(caught).toMatchObject({
        code: "PROTOCOL_MALFORMED",
        details: {
          adapterDiagnostic: {
            counters: {
              acceptedRecords: 0xffff_ffff,
              thinkingDeltas: 0,
              textDeltas: 0,
              saturated: true,
            },
          },
        },
      });
      expect(JSON.stringify(caught)).not.toContain(secret);
    }
  });

  test("requires the exact projected update grammar and consistent rich current messages", () => {
    const mutations: Array<(frames: any[]) => void> = [
      (frames) => { frames[6].assistantMessageEvent.type = "thinking_delta"; },
      (frames) => { frames[6].assistantMessageEvent.extra = true; },
      (frames) => { frames[6].assistantMessageEvent.partial = frames[6].message; },
      (frames) => { frames[7].assistantMessageEvent.contentIndex = 1; },
      (frames) => { frames[8].assistantMessageEvent.type = "text_delta"; },
      (frames) => { frames[9].assistantMessageEvent.delta = "different"; },
      (frames) => { frames[9].message.content[0].thinking = "different"; },
      (frames) => { frames[10].assistantMessageEvent.content = "different"; },
      (frames) => { frames[11].assistantMessageEvent.type = "text_delta"; },
      (frames) => { frames[12].assistantMessageEvent.delta = "different"; },
      (frames) => { frames[13].message.content[1].text = "different"; },
      (frames) => { frames[15].assistantMessageEvent.content = "different"; },
      (frames) => { frames[16].message.content[1].text = "different"; },
    ];
    for (const mutate of mutations) {
      const frames = structuredClone(fixtureFrames) as any[];
      mutate(frames);
      expectFailure(() => acceptAll(frames), "PROTOCOL_MALFORMED");
    }

    for (const suffix of ["\n", "\n\n\n", "  ", "\n "]) {
      const frames = structuredClone(fixtureFrames) as any[];
      frames[9].assistantMessageEvent.delta = frames[9].assistantMessageEvent.delta.slice(0, -2) + suffix;
      frames[9].message.content[0].thinking = frames[9].message.content[0].thinking.slice(0, -2) + suffix;
      expectFailure(() => acceptAll(frames), "PROTOCOL_MALFORMED");
    }
    const settledDrift = structuredClone(fixtureFrames) as any[];
    settledDrift[10].assistantMessageEvent.content += "drift";
    settledDrift[10].message.content[0].thinking += "drift";
    expectFailure(() => acceptAll(settledDrift), "PROTOCOL_MALFORMED");
  });

  test("fails closed on interactive UI, tool activity, and oversized rich fields", () => {
    expectFailure(
      () => acceptAll(inserted(fixtureFrames, 6, { type: "extension_ui_request", id: "x", method: "confirm" })),
      "HARNESS_FAILED",
    );
    expectFailure(
      () => acceptAll(inserted(fixtureFrames, 13, { type: "tool_execution_start", toolCallId: "x" })),
      "PROTOCOL_MALFORMED",
    );
    const oversized = structuredClone(fixtureFrames) as any[];
    oversized[6].message.responseId = "x".repeat(65_537);
    expectFailure(() => acceptAll(oversized), "PROTOCOL_MALFORMED");
  });
});
