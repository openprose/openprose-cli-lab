import { describe, expect, test } from "bun:test";

import {
  buildOmpEmbeddedPlan,
  runOmpEmbedded,
} from "../src/omp-embedded.ts";

const input = {
  cwd: "/workspace",
  env: {},
  runtimeImageUtf8: "opaque-image",
  taskEnvelope: "opaque-task",
};

describe("OMP embedded research adapter", () => {
  test("constructs a fully explicit, noninteractive discovery boundary", () => {
    const plan = buildOmpEmbeddedPlan(input);

    expect(plan).toMatchObject({
      auth: "harness-managed",
      billingOwner: "harness-managed",
      envIsolation: "requires-isolated-process",
      taskEnvelope: "opaque-task",
      options: {
        contextFiles: [],
        disableExtensionDiscovery: true,
        enableIrc: false,
        enableLsp: false,
        enableMCP: false,
        hasUI: false,
        interactivePrompts: false,
        promptTemplates: [],
        restrictToolNames: true,
        rules: [],
        skills: [],
        slashCommands: [],
        systemPrompt: ["opaque-image"],
        toolNames: ["read", "bash", "edit", "write", "task"],
      },
    });
  });

  test("uses agent_end as terminal and awaits disposal", async () => {
    let prompt = "";
    let disposed = false;
    const result = await runOmpEmbedded(input, async () => ({
      session: {
        abort: async () => {},
        dispose: async () => {
          disposed = true;
        },
        prompt: async (value) => {
          prompt = value;
        },
        subscribe: (listener) => {
          listener({ type: "agent_start" });
          listener({ type: "agent_end" });
          return () => {};
        },
      },
    }));

    expect(prompt).toBe("opaque-task");
    expect(disposed).toBeTrue();
    expect(result.status).toBe("completed");
    expect(result.terminalEvent).toBe("agent_end");
  });

  test("fails closed when the session settles without agent_end", async () => {
    await expect(
      runOmpEmbedded(input, async () => ({
        session: {
          abort: async () => {},
          dispose: async () => {},
          prompt: async () => {},
          subscribe: () => () => {},
        },
      })),
    ).rejects.toThrow("without agent_end");
  });

  test("forwards cancellation reason and awaits disposal", async () => {
    const controller = new AbortController();
    let abortReason: unknown;
    let disposed = false;
    let finishPrompt: (() => void) | undefined;
    const run = runOmpEmbedded(
      { ...input, signal: controller.signal },
      async () => ({
        session: {
          abort: async (reason) => {
            abortReason = reason;
            finishPrompt?.();
          },
          dispose: async () => {
            disposed = true;
          },
          prompt: () =>
            new Promise<void>((resolve) => {
              finishPrompt = resolve;
            }),
          subscribe: () => () => {},
        },
      }),
    );

    await Bun.sleep(1);
    controller.abort("test-cancel");

    await expect(run).resolves.toMatchObject({
      status: "cancelled",
      terminalEvent: "abort-signal",
    });
    expect(abortReason).toBe("test-cancel");
    expect(disposed).toBeTrue();
  });
});
