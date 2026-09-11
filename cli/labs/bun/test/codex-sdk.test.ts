import { afterEach, describe, expect, test } from "bun:test";
import { chmod, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

import {
  buildCodexSdkPlan,
  runCodexSdk,
} from "../src/codex-sdk.ts";

const roots: string[] = [];
const fakeCodex = resolve(import.meta.dir, "fixtures/fake-codex.ts");

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { force: true, recursive: true })));
});

describe("Codex SDK research adapter", () => {
  test("keeps image, task, environment, and cwd in distinct SDK fields", () => {
    const plan = buildCodexSdkPlan(invocation("/workspace"), "/fake/codex");

    expect(plan.client).toEqual({
      codexPathOverride: "/fake/codex",
      config: { developer_instructions: "opaque-image" },
      env: { CODEX_HOME: "/private/codex", PATH: "/bin" },
    });
    expect(plan.taskEnvelope).toBe("opaque-task");
    expect(plan.thread).toEqual({
      additionalDirectories: ["/extra"],
      approvalPolicy: "never",
      sandboxMode: "workspace-write",
      skipGitRepoCheck: true,
      workingDirectory: "/workspace",
    });
  });

  test("drives the real SDK wrapper against a provider-free executable", async () => {
    const root = await temporaryRoot();
    const capturePath = join(root, "capture.json");
    await chmod(fakeCodex, 0o755);
    const bunPath = Bun.which("bun");
    if (bunPath === null) throw new Error("bun not found");
    const input = invocation(root, {
      CODEX_HOME: join(root, "codex-home"),
      HOME: join(root, "home"),
      LAB_CAPTURE_PATH: capturePath,
      PATH: dirname(bunPath),
    });

    const result = await runCodexSdk(input, { codexPathOverride: fakeCodex });
    const capture = JSON.parse(await readFile(capturePath, "utf8")) as {
      argv: string[];
      env: Record<string, string | undefined>;
      input: string;
    };

    expect(result).toEqual({
      diagnostics: [],
      status: "completed",
      terminalEvent: "turn.completed",
      text: "fake response",
    });
    expect(capture.input).toBe("opaque-task");
    expect(capture.argv).toContain("developer_instructions=\"opaque-image\"");
    expect(capture.argv).toContain("--experimental-json");
    expect(capture.argv).toContain("--skip-git-repo-check");
    expect(capture.env.LAB_SECRET_SHOULD_BE_ABSENT).toBeUndefined();
  });

  test("fails closed on EOF without a terminal turn event", async () => {
    const root = await temporaryRoot();
    await chmod(fakeCodex, 0o755);
    const bunPath = Bun.which("bun");
    if (bunPath === null) throw new Error("bun not found");
    const input = invocation(root, {
      CODEX_HOME: join(root, "codex-home"),
      HOME: join(root, "home"),
      LAB_CAPTURE_PATH: join(root, "capture.json"),
      LAB_FAKE_MODE: "no-terminal",
      PATH: dirname(bunPath),
    });

    await expect(
      runCodexSdk(input, { codexPathOverride: fakeCodex }),
    ).rejects.toThrow("without turn.completed or turn.failed");
  });

  test("forwards cancellation through the SDK AbortSignal", async () => {
    const root = await temporaryRoot();
    await chmod(fakeCodex, 0o755);
    const bunPath = Bun.which("bun");
    if (bunPath === null) throw new Error("bun not found");
    const controller = new AbortController();
    const input = {
      ...invocation(root, {
        CODEX_HOME: join(root, "codex-home"),
        HOME: join(root, "home"),
        LAB_CAPTURE_PATH: join(root, "capture.json"),
        LAB_FAKE_MODE: "delay",
        PATH: dirname(bunPath),
      }),
      signal: controller.signal,
    };

    const run = runCodexSdk(input, { codexPathOverride: fakeCodex });
    await Bun.sleep(50);
    controller.abort("test-cancel");

    await expect(run).resolves.toMatchObject({
      status: "cancelled",
      terminalEvent: "abort-signal",
    });
  });
});

function invocation(cwd: string, env?: Record<string, string>) {
  return {
    additionalDirectories: ["/extra"],
    cwd,
    env: env ?? { CODEX_HOME: "/private/codex", PATH: "/bin" },
    runtimeImageUtf8: "opaque-image",
    taskEnvelope: "opaque-task",
  };
}

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "openprose-codex-lab-"));
  roots.push(root);
  return root;
}
