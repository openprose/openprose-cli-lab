import { afterEach, describe, expect, test } from "bun:test";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { runCli, type CliDependencies } from "../src/cli";
import { sentinelFixtureImage } from "./sentinel-fixture";

const fakeHarness = resolve(import.meta.dir, "../../conformance/fake-harness/fake_harness.py");
const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

async function fixture(scenario: string) {
  const root = await mkdtemp(join(tmpdir(), "openprose-bun-cli-process-"));
  roots.push(root);
  let stdout = "";
  let stderr = "";
  const observation = join(root, "observation.json");
  const deps: CliDependencies = {
    env: { PATH: process.env.PATH },
    processCwd: root,
    userConfigPath: join(root, "absent.toml"),
    clock: { now: () => "2025-01-01T00:00:00Z", monotonicMs: () => 0 },
    ids: { invocationId: () => "fixture-invocation-0001" },
    writeStdout: (text) => { stdout += text; },
    writeStderr: (text) => { stderr += text; },
    imageBundle: sentinelFixtureImage,
    fakeProcess: { executable: fakeHarness, scenario, observationFile: observation },
  };
  return { root, observation, deps, stdout: () => stdout, stderr: () => stderr };
}

describe("CLI fake-process integration", () => {
  test("returns a schema-shaped result while preserving task argv and process evidence", async () => {
    const io = await fixture("success");
    const exit = await runCli([
      "--harness", "mock", "--transport", "fake-process", "--output", "json",
      "write", "snow 雪", "$(not-a-shell)", "a;b", "x|y",
    ], io.deps);
    expect(exit).toBe(0);
    const result = JSON.parse(io.stdout());
    expect(result).toMatchObject({
      schema: "openprose.runner-result/1",
      adapter: { id: "mock/fake-process", harnessVersion: "1.0.0" },
      transport: "fake-process",
      negotiatedCapabilities: { cancellation: "process-tree", streaming: "structured" },
      terminal: { classification: "success", terminalEventObserved: true, exitCode: 0 },
      semantic: { status: "not-applicable" },
      runnerExitCode: 0,
    });
    const observation = JSON.parse(await readFile(io.observation, "utf8"));
    const task = JSON.parse(Buffer.from(observation.task.bytesBase64, "base64").toString("utf8"));
    expect(task.argv).toEqual(["prose", "write", "snow 雪", "$(not-a-shell)", "a;b", "x|y"]);
    expect(io.stderr()).toBe("");
  });

  test.each([
    ["malformed", 22, "PROTOCOL_MALFORMED"],
    ["truncated", 22, "PROTOCOL_TRUNCATED"],
    ["terminal-nonzero", 22, "HARNESS_FAILED"],
  ])("returns a runner result for %s rather than a bare error", async (scenario, expectedExit, code) => {
    const io = await fixture(scenario);
    const exit = await runCli(["--harness=mock", "--transport=fake-process", "--output=json", "run"], io.deps);
    expect(exit).toBe(expectedExit);
    expect(JSON.parse(io.stdout())).toMatchObject({
      schema: "openprose.runner-result/1",
      adapter: { id: "mock/fake-process" },
      runnerExitCode: expectedExit,
      error: { code },
    });
    const details = JSON.parse(io.stdout()).error.details;
    expect(Object.keys(details).sort()).toEqual(["processExit", "processSignal", "terminalEventObserved"]);
    if (scenario === "malformed") {
      expect(details).toMatchObject({ processExit: 0, processSignal: null, terminalEventObserved: false });
    }
    if (scenario === "terminal-nonzero") {
      expect(JSON.parse(io.stdout())).toMatchObject({
        semantic: { status: "unknown" },
        terminal: { classification: "exit-code", transportCompleted: false },
      });
    }
    expect(io.stderr()).toBe("");
  });

  test("keeps diagnostic stderr separate from a single JSON result", async () => {
    const io = await fixture("stderr");
    expect(await runCli(["--harness=mock", "--transport=fake-process", "--output=json", "run"], io.deps)).toBe(0);
    expect(JSON.parse(io.stdout()).runnerExitCode).toBe(0);
    expect(io.stdout().trim().split("\n")).toHaveLength(1);
    expect(io.stderr()).toContain("fake harness diagnostic");
  });

  test("emits one JSONL terminal event even on malformed process output", async () => {
    const io = await fixture("malformed");
    expect(await runCli(["--harness=mock", "--transport=fake-process", "--output=jsonl", "run"], io.deps)).toBe(22);
    const records = io.stdout().trimEnd().split("\n").map((line) => JSON.parse(line));
    expect(records.at(-1)).toMatchObject({ type: "runner.failed", payload: { error: { code: "PROTOCOL_MALFORMED" } } });
    expect(records.filter((record) => record.type === "runner.failed" || record.type === "runner.completed")).toHaveLength(1);
  });

  test("rejects an inherited recursion marker before starting the fake harness", async () => {
    const io = await fixture("success");
    io.deps.env = { PATH: process.env.PATH, OPENPROSE_RECURSION_TOKEN: "already-running" };
    expect(await runCli(["--harness=mock", "--transport=fake-process", "--output=json", "run"], io.deps)).toBe(20);
    expect(JSON.parse(io.stdout())).toMatchObject({ schema: "openprose.runner-result/1", error: { code: "RECURSIVE_INVOCATION" } });
    expect(await Bun.file(io.observation).exists()).toBeFalse();
  });

  test("honors cancellation before spawn without creating fake-harness evidence", async () => {
    const io = await fixture("success");
    const cancellation = new AbortController();
    cancellation.abort();
    io.deps.cancellationSignal = cancellation.signal;
    expect(await runCli(["--harness=mock", "--transport=fake-process", "--output=json", "run"], io.deps)).toBe(24);
    expect(JSON.parse(io.stdout())).toMatchObject({ error: { code: "CANCELLED", details: { phase: "before-spawn" } } });
    expect(await Bun.file(io.observation).exists()).toBeFalse();
  });

  test("reports the strict Windows boundary as unsupported in a result", async () => {
    const io = await fixture("success");
    io.deps.fakeProcess = { ...io.deps.fakeProcess!, platform: "win32" };
    expect(await runCli(["--harness=mock", "--transport=fake-process", "--output=json", "run"], io.deps)).toBe(20);
    expect(JSON.parse(io.stdout())).toMatchObject({ error: { code: "TRANSPORT_UNSUPPORTED", details: { compiledAdmission: false } } });
  });

  test.skipIf(process.platform === "win32").each([
    "success",
    "semantic-failed",
    "unknown",
  ])("rejects %s because the sentinel terminal schema authorizes only not-applicable", async (status) => {
    const io = await fixture("success");
    const executable = join(io.root, "semantic-fixture");
    await writeFile(executable, [
      `#!${process.execPath}`,
      'if (process.argv.includes("--version")) { console.log("openprose-fake-harness 1.0.0"); process.exit(0); }',
      'console.log(JSON.stringify({schema:"openprose.fake-harness-event/1",type:"session.started",sessionId:"fake-session-0001",harnessVersion:"1.0.0"}));',
      `console.log(JSON.stringify({schema:"openprose.fake-harness-event/1",type:"session.completed",sessionId:"fake-session-0001",terminalEnvelope:{schema:"openprose.sentinel-terminal-envelope/1",semanticStatus:${JSON.stringify(status)},marker:"OPENPROSE_SENTINEL_TERMINAL_V1"}}));`,
    ].join("\n"), { mode: 0o700 });
    await chmod(executable, 0o700);
    io.deps.fakeProcess = { executable, scenario: "success", observationFile: io.observation };
    expect(await runCli(["--harness=mock", "--transport=fake-process", "--output=json", "run"], io.deps)).toBe(22);
    const result = JSON.parse(io.stdout());
    expect(result.semantic).toMatchObject({ status: "unknown", terminalEnvelopeDigestSha256: null });
    expect(result.terminal).toMatchObject({ terminalEventObserved: false, transportCompleted: false });
    expect(result.error.code).toBe("PROTOCOL_MALFORMED");
  });
});
