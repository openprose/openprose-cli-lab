import { describe, expect, test } from "bun:test";
import type { Query, SDKMessage } from "@anthropic-ai/claude-agent-sdk";

import {
  buildClaudeAgentSdkPlan,
  runClaudeAgentSdk,
} from "../src/claude-agent-sdk.ts";

const input = {
  additionalDirectories: ["/extra"],
  cwd: "/workspace",
  env: { HOME: "/private/home", PATH: "/bin" },
  runtimeImageUtf8: "opaque-image",
  taskEnvelope: "opaque-task",
};

describe("Claude Agent SDK research adapter", () => {
  test("uses SDK isolation controls and a separate system-prompt append", () => {
    const plan = buildClaudeAgentSdkPlan(input, "/asset/claude");

    expect(plan.prompt).toBe("opaque-task");
    expect(plan.options).toMatchObject({
      additionalDirectories: ["/extra"],
      cwd: "/workspace",
      env: { HOME: "/private/home", PATH: "/bin" },
      pathToClaudeCodeExecutable: "/asset/claude",
      permissionMode: "dontAsk",
      persistSession: false,
      plugins: [],
      settingSources: [],
      skills: [],
      strictMcpConfig: true,
      systemPrompt: {
        append: "opaque-image",
        preset: "claude_code",
        type: "preset",
      },
    });
  });

  test("requires the SDK result event and closes the stream", async () => {
    let closed = false;
    let captured: unknown;
    const result = await runClaudeAgentSdk(input, {
      query: (params) => {
        captured = params;
        return fakeQuery(
          [
            {
              type: "stream_event",
              event: {
                type: "content_block_delta",
                delta: { type: "text_delta", text: "hello" },
              },
            } as SDKMessage,
            {
              type: "result",
              subtype: "success",
              result: "hello",
              is_error: false,
            } as SDKMessage,
          ],
          () => {
            closed = true;
          },
        );
      },
    });

    expect(captured).toMatchObject({ prompt: "opaque-task" });
    expect(closed).toBeTrue();
    expect(result).toEqual({
      diagnostics: [],
      status: "completed",
      terminalEvent: "result:success",
      text: "hello",
    });
  });

  test("fails closed when stream EOF has no result", async () => {
    await expect(
      runClaudeAgentSdk(input, { query: () => fakeQuery([], () => {}) }),
    ).rejects.toThrow("without a result message");
  });

  test("bridges caller cancellation to the SDK controller", async () => {
    const controller = new AbortController();
    let closed = false;
    const run = runClaudeAgentSdk(
      { ...input, signal: controller.signal },
      {
        query: ({ options }) => {
          const generator = (async function* () {
            await new Promise<void>((resolve) =>
              options?.abortController?.signal.addEventListener(
                "abort",
                () => resolve(),
                { once: true },
              ),
            );
          })();
          return Object.assign(generator, {
            close: () => {
              closed = true;
            },
          }) as Query;
        },
      },
    );

    controller.abort("test-cancel");
    await expect(run).resolves.toMatchObject({ status: "cancelled" });
    expect(closed).toBeTrue();
  });
});

function fakeQuery(messages: SDKMessage[], close: () => void): Query {
  const generator = (async function* () {
    yield* messages;
  })();
  return Object.assign(generator, {
    close,
  }) as Query;
}
