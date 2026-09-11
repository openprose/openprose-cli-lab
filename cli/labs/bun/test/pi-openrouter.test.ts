import { describe, expect, test } from "bun:test";

import {
  buildPiOpenRouterPlan,
  runPiOpenRouter,
} from "../src/pi-openrouter.ts";

const input = {
  cwd: "/workspace",
  env: {},
  runtimeImageUtf8: "opaque-image",
  taskEnvelope: "opaque-task",
};

describe("Pi/OpenRouter research adapter", () => {
  test("selects OpenRouter and replaces ambient resource discovery", () => {
    const plan = buildPiOpenRouterPlan(input);

    expect(plan.auth).toBe("openrouter-api-key-or-oauth");
    expect(plan.billingOwner).toBe("openrouter");
    expect(plan.envIsolation).toBe("requires-isolated-process");
    expect(plan.options.model?.provider).toBe("openrouter");
    expect(plan.options.resourceLoader?.getSystemPrompt()).toBe("opaque-image");
    expect(plan.options.resourceLoader?.getSkills()).toEqual({
      diagnostics: [],
      skills: [],
    });
    expect(plan.options.tools).toEqual(["read", "bash", "edit", "write"]);
    expect(plan.taskEnvelope).toBe("opaque-task");
  });

  test("uses agent_end as the terminal event", async () => {
    let prompt = "";
    let disposed = false;
    const result = await runPiOpenRouter(input, {
      factory: async () => ({
        session: {
          abort: async () => {},
          dispose: () => {
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
      }),
    });

    expect(prompt).toBe("opaque-task");
    expect(disposed).toBeTrue();
    expect(result.status).toBe("completed");
    expect(result.terminalEvent).toBe("agent_end");
  });

  test("fails closed when the session settles without agent_end", async () => {
    await expect(
      runPiOpenRouter(input, {
        factory: async () => ({
          session: {
            abort: async () => {},
            dispose: () => {},
            prompt: async () => {},
            subscribe: () => () => {},
          },
        }),
      }),
    ).rejects.toThrow("without agent_end");
  });

  test("forwards cancellation and still disposes the session", async () => {
    const controller = new AbortController();
    let aborted = false;
    let disposed = false;
    let finishPrompt: (() => void) | undefined;
    const run = runPiOpenRouter(
      { ...input, signal: controller.signal },
      {
        factory: async () => ({
          session: {
            abort: async () => {
              aborted = true;
              finishPrompt?.();
            },
            dispose: () => {
              disposed = true;
            },
            prompt: () =>
              new Promise<void>((resolve) => {
                finishPrompt = resolve;
              }),
            subscribe: () => () => {},
          },
        }),
      },
    );

    await Bun.sleep(1);
    controller.abort("test-cancel");

    await expect(run).resolves.toMatchObject({
      status: "cancelled",
      terminalEvent: "abort-signal",
    });
    expect(aborted).toBeTrue();
    expect(disposed).toBeTrue();
  });
});
