import { afterEach, describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { chmod, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ProcessSupervisionRequest, RawTransportEvent, StructuredProtocolState } from "../src/supervision/types";
import {
  probeWindowsExecutableVersion,
  superviseWindowsStructuredProcess,
} from "../src/supervision/windows-process";

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe("admitted Windows process-host integration", () => {
  test("streams fragmented child JSONL through the existing protocol and redacts child diagnostics", async () => {
    const fixture = await createFixture();
    const protocol = new FixtureProtocol();
    const result = await superviseWindowsStructuredProcess(request(fixture, protocol), {
      admissionEnabled: true,
      expectedSha256: fixture.digest,
      wirePath,
    });
    expect(result).toMatchObject({
      executable: fixture.target,
      pid: 1337,
      processGroupId: 1337,
      exitCode: 0,
      signal: null,
      terminalEventObserved: true,
      harnessVersion: "9.9.9",
      cleanupVerified: true,
      cancellationReason: null,
      error: null,
    });
    expect(result.events.map((event) => event.type)).toEqual([
      "session.started", "assistant.message", "session.completed",
    ]);
    expect(result.stderr).toBe("failure [REDACTED]");
  });

  test("requires compiled admission and a byte-exact canonical sibling before probing it", async () => {
    const fixture = await createFixture();
    await expect(superviseWindowsStructuredProcess(request(fixture, new FixtureProtocol()), {
      admissionEnabled: false,
      expectedSha256: fixture.digest,
      wirePath,
    })).rejects.toMatchObject({ code: "TRANSPORT_UNSUPPORTED" });
    await expect(superviseWindowsStructuredProcess(request(fixture, new FixtureProtocol()), {
      admissionEnabled: true,
      expectedSha256: "0".repeat(64),
      wirePath,
    })).rejects.toMatchObject({ code: "HARNESS_INCOMPATIBLE" });
  });

  test("sends one cancel and trusts only the helper's empty-Job terminal", async () => {
    const fixture = await createFixture();
    const input = request(fixture, new FixtureProtocol());
    input.argv = ["--wait-cancel"];
    input.cancelAfterMs = 20;
    const result = await superviseWindowsStructuredProcess(input, {
      admissionEnabled: true,
      expectedSha256: fixture.digest,
      wirePath,
    });
    expect(result).toMatchObject({
      cleanupVerified: true,
      cancellationReason: "caller",
      error: { code: "CANCELLED" },
    });
  });

  test("maps a child startup deadline through helper cancellation after cleanup", async () => {
    const fixture = await createFixture();
    const input = request(fixture, new FixtureProtocol());
    input.argv = ["--startup-hang"];
    input.startupTimeoutMs = 25;
    const result = await superviseWindowsStructuredProcess(input, {
      admissionEnabled: true,
      expectedSha256: fixture.digest,
      wirePath,
    });
    expect(result).toMatchObject({
      cleanupVerified: true,
      cancellationReason: null,
      error: { code: "STARTUP_TIMEOUT" },
    });
  });

  test("does not charge helper identity startup against the child startup deadline", async () => {
    const fixture = await createFixture({ identityDelayMs: 1_100 });
    const input = request(fixture, new FixtureProtocol());
    input.argv = ["--startup-hang"];
    input.startupTimeoutMs = 25;
    const result = await superviseWindowsStructuredProcess(input, {
      admissionEnabled: true,
      expectedSha256: fixture.digest,
      wirePath,
    });
    expect(result).toMatchObject({
      cleanupVerified: true,
      cancellationReason: null,
      error: { code: "STARTUP_TIMEOUT" },
    });
  });

  test("continues draining to authoritative cleanup after malformed child JSONL", async () => {
    const fixture = await createFixture();
    const input = request(fixture, new FixtureProtocol());
    input.argv = ["--malformed-child"];
    const result = await superviseWindowsStructuredProcess(input, {
      admissionEnabled: true,
      expectedSha256: fixture.digest,
      wirePath,
    });
    expect(result).toMatchObject({
      cleanupVerified: true,
      error: { code: "PROTOCOL_MALFORMED" },
    });
  });

  test("fails closed when the helper disconnects after start without empty-Job evidence", async () => {
    const fixture = await createFixture();
    const input = request(fixture, new FixtureProtocol());
    input.argv = ["--disconnect"];
    const result = await superviseWindowsStructuredProcess(input, {
      admissionEnabled: true,
      expectedSha256: fixture.digest,
      wirePath,
    });
    expect(result).toMatchObject({
      cleanupVerified: false,
      error: { code: "PROCESS_CLEANUP_FAILED" },
    });
  });

  test("routes target version probes through the verified helper", async () => {
    const fixture = await createFixture();
    const version = await probeWindowsExecutableVersion(
      fixture.target,
      fixture.root,
      { PATH: process.env.PATH },
      fixture.wrapper,
      2_000,
      /^fixture-harness 1\.2\.3$/,
      "stdout",
      { admissionEnabled: true, expectedSha256: fixture.digest, wirePath },
    );
    expect(version).toBe("fixture-harness 1.2.3");
  });
});

interface Fixture {
  root: string;
  wrapper: string;
  helper: string;
  target: string;
  digest: string;
}

async function createFixture(options: { identityDelayMs?: number } = {}): Promise<Fixture> {
  const root = await mkdtemp(join(tmpdir(), "openprose-windows-host-"));
  roots.push(root);
  const wrapper = join(root, "prose.exe");
  const helper = join(root, "openprose-windows-process-host.exe");
  const target = join(root, "fixture-harness.exe");
  await writeFile(wrapper, "wrapper fixture\n", { mode: 0o755 });
  await writeFile(target, "target fixture\n", { mode: 0o755 });
  await writeFile(helper, fakeHostSource(options.identityDelayMs ?? 0), { mode: 0o755 });
  await chmod(helper, 0o755);
  const digest = createHash("sha256").update(await Bun.file(helper).bytes()).digest("hex");
  return {
    root: await realpath(root),
    wrapper: await realpath(wrapper),
    helper: await realpath(helper),
    target: await realpath(target),
    digest,
  };
}

function request(fixture: Fixture, protocol: StructuredProtocolState): ProcessSupervisionRequest {
  return {
    executable: fixture.target,
    argv: ["run"],
    cwd: fixture.root,
    environment: {
      PATH: process.env.PATH,
      OPENAI_API_KEY: "fixture-secret",
    },
    additionalEnvironmentNames: ["OPENAI_API_KEY"],
    invocationId: "fixture-invocation",
    recursionToken: "fixture-recursion",
    runNonce: "fixture-nonce",
    wrapperExecutable: fixture.wrapper,
    startupTimeoutMs: 1_000,
    runTimeoutMs: 2_000,
    graceMs: 20,
    hardKillAfterMs: 1_000,
    protocol,
    platform: "win32" as const,
  };
}

function wirePath(canonicalPath: string, kind: "executable" | "wrapper" | "cwd"): string {
  if (kind === "cwd") return "C:\\fixture";
  const name = canonicalPath.split("/").at(-1)!;
  return `C:\\fixture\\${name}`;
}

class FixtureProtocol implements StructuredProtocolState {
  private started = false;
  private terminal = false;
  terminalEnvelope: Record<string, unknown> | null = null;
  harnessVersion: string | null = null;
  readonly descendants = [];
  readonly descendantPids = [];

  get terminalEventObserved(): boolean { return this.terminal; }

  accept(value: unknown): RawTransportEvent {
    const record = value as Record<string, unknown>;
    if (record.schema !== "fixture.child/1") throw new Error("wrong child schema");
    if (record.type === "session.started" && !this.started) {
      this.started = true;
      this.harnessVersion = String(record.harnessVersion);
      return { type: "session.started", harnessVersion: this.harnessVersion };
    }
    if (record.type === "assistant.message" && this.started && !this.terminal) {
      return { type: "assistant.message", text: String(record.text) };
    }
    if (record.type === "session.completed" && this.started && !this.terminal) {
      this.terminal = true;
      this.terminalEnvelope = record.terminalEnvelope as Record<string, unknown>;
      return { type: "session.completed", terminalEnvelope: this.terminalEnvelope };
    }
    throw new Error("invalid child sequence");
  }
}

function fakeHostSource(identityDelayMs: number): string {
  return `#!/usr/bin/env bun
import { createInterface } from "node:readline";

const identity = {
  schema: "openprose.windows-process-host.identity/1",
  component: "openprose-windows-process-host",
  componentVersion: "0.1.0",
  target: { os: "windows", architecture: "x86_64", nativeWindowsImplementation: true },
  protocol: {
    request: "openprose.windows-process-host.request/1",
    control: "openprose.windows-process-host.control/1",
    event: "openprose.windows-process-host.event/1",
    error: "openprose.windows-process-host.error/1",
    maxRequestBytes: 67108864,
    maxControlBytes: 4096,
    limits: {
      maxDecodedStdinBytes: 16777215,
      maxStdinBase64Characters: 22369620,
      maxArgvItems: 64,
      maxArgumentCharacters: 32766,
      maxEnvironmentItems: 64,
      maxEnvironmentNameCharacters: 128,
      maxEnvironmentValueCharacters: 8192,
      maxPathCharacters: 32766,
      maxEnvironmentBlockBytes: 4194304,
      maxRenderedCommandLineUtf16Units: 32766
    }
  },
  claims: { providerCallsMade: false, nativeWindowsRuntimeEvidence: false, strictWindowsContainmentReady: false }
};
if (process.argv[2] === "--identity-json") {
  if (${identityDelayMs} > 0) await Bun.sleep(${identityDelayMs});
  process.stdout.write(JSON.stringify(identity) + "\\n");
  process.exit(0);
}
if (process.env.OPENPROSE_RECURSION_TOKEN !== undefined || process.env.OPENAI_API_KEY !== undefined) {
  process.stderr.write(JSON.stringify({ schema: "openprose.windows-process-host.error/1", code: "RECURSIVE_INVOCATION", operation: "ambient helper environment", win32: null }) + "\\n");
  process.exit(20);
}

let request;
let sequence = 0;
let childStdoutBytes = 0;
let childStderrBytes = 0;
const emit = (type, payload) => process.stdout.write(JSON.stringify({
  schema: "openprose.windows-process-host.event/1",
  requestId: request.requestId,
  sequence: sequence++,
  type,
  payload
}) + "\\n");
const started = () => emit("host.started", {
  pid: 1337,
  suspendedCreate: true,
  strictHandleList: true,
  inheritedHandleCount: 3,
  jobAssignedBeforeResume: true,
  killOnJobClose: true,
  newProcessGroup: true,
  outerPty: false,
  shell: false
});
const chunk = (stream, bytes) => {
  if (stream === "stdout") childStdoutBytes += bytes.length;
  else childStderrBytes += bytes.length;
  emit("child." + stream, { encoding: "base64", data: bytes.toString("base64"), stream });
};
const exited = (termination, processExitCode = 0) => emit("host.exited", {
  pid: 1337,
  processExitCode,
  termination,
  gracefulControlAttempted: termination !== "natural",
  gracefulControlDelivered: false,
  hardKillUsed: termination !== "natural",
  activeProcessesBeforeCleanup: 0,
  activeProcessesAfterCleanup: 0,
  cleanupVerified: true,
  stdoutBytes: childStdoutBytes,
  stderrBytes: childStderrBytes
});

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on("line", (line) => {
  if (request === undefined) {
    request = JSON.parse(line);
    started();
    if (request.argv.length === 1 && request.argv[0] === "--version") {
      chunk("stdout", Buffer.from("fixture-harness 1.2.3\\n"));
      exited("natural");
      setTimeout(() => process.exit(0), 5);
      return;
    }
    if (request.argv[0] === "--startup-hang") return;
    const start = Buffer.from(JSON.stringify({ schema: "fixture.child/1", type: "session.started", harnessVersion: "9.9.9" }) + "\\n");
    chunk("stdout", start);
    if (request.argv[0] === "--wait-cancel") return;
    if (request.argv[0] === "--disconnect") {
      setTimeout(() => process.exit(70), 5);
      return;
    }
    if (request.argv[0] === "--malformed-child") {
      chunk("stdout", Buffer.from("{not-json}\\n"));
      exited("natural");
      setTimeout(() => process.exit(0), 5);
      return;
    }
    const remainder = Buffer.from(
      JSON.stringify({ schema: "fixture.child/1", type: "assistant.message", text: "opaque" }) + "\\n" +
      JSON.stringify({ schema: "fixture.child/1", type: "session.completed", terminalEnvelope: { schema: "fixture.terminal/1" } }) + "\\n"
    );
    const secret = request.environment.find((entry) => entry.name === "OPENAI_API_KEY").value;
    chunk("stdout", remainder.subarray(0, 17));
    chunk("stderr", Buffer.from("failure " + secret));
    chunk("stdout", remainder.subarray(17));
    exited("natural");
    setTimeout(() => process.exit(0), 5);
    return;
  }
  const control = JSON.parse(line);
  if (control.type === "cancel") {
    exited("cancelled", 1);
    setTimeout(() => process.exit(0), 5);
  }
});
`;
}
