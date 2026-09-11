import { describe, expect, test } from "bun:test";
import {
  WindowsHostControlWriter,
  WindowsHostEventParser,
  acceptWindowsVersionProbe,
  buildWindowsHostRequest,
  buildWindowsHostVersionProbeRequest,
  encodeWindowsHostRequest,
  parseWindowsHostIdentity,
  type WindowsHostRequest,
  windowsHostApplicability,
} from "../src/supervision/windows-host";

const requestId = "run.fixture-1";
const limits = {
  maxRecordBytes: 1_048_576,
  maxAggregateStdoutBytes: 64,
  maxAggregateStderrBytes: 32,
  maxQueuedRecords: 8,
};

function request(): WindowsHostRequest {
  return buildWindowsHostRequest({
    requestId,
    executable: "C:\\Tools\\prime-agent.exe",
    wrapperExecutable: "C:\\OpenProse\\prose.exe",
    argv: ["run", "--model", "fixture"],
    cwd: "C:\\work",
    environment: {
      OPENPROSE_INVOCATION_ID: "invocation",
      OPENPROSE_RECURSION_TOKEN: "recursion",
      OPENPROSE_RUN_NONCE: "nonce",
      PATH: "C:\\Windows",
    },
    stdinBytes: Uint8Array.from([0, 1, 2, 255]),
    limits,
    graceful: "ctrl-break",
    graceMs: 250,
    hardKillAfterMs: 2_000,
    runTimeoutMs: 30_000,
  });
}

function started(sequence = 0): Record<string, unknown> {
  return {
    schema: "openprose.windows-process-host.event/1",
    requestId,
    sequence,
    type: "host.started",
    payload: {
      pid: 4242,
      suspendedCreate: true,
      strictHandleList: true,
      inheritedHandleCount: 3,
      jobAssignedBeforeResume: true,
      killOnJobClose: true,
      newProcessGroup: true,
      outerPty: false,
      shell: false,
    },
  };
}

function stream(kind: "stdout" | "stderr", data: string, sequence: number): Record<string, unknown> {
  return {
    schema: "openprose.windows-process-host.event/1",
    requestId,
    sequence,
    type: `child.${kind}`,
    payload: { encoding: "base64", data, stream: kind },
  };
}

function exited(
  sequence: number,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema: "openprose.windows-process-host.event/1",
    requestId,
    sequence,
    type: "host.exited",
    payload: {
      pid: 4242,
      processExitCode: 0,
      termination: "natural",
      gracefulControlAttempted: false,
      gracefulControlDelivered: false,
      hardKillUsed: false,
      activeProcessesBeforeCleanup: 0,
      activeProcessesAfterCleanup: 0,
      cleanupVerified: true,
      stdoutBytes: 0,
      stderrBytes: 0,
      ...overrides,
    },
  };
}

function validParser(): WindowsHostEventParser {
  return new WindowsHostEventParser(requestId, limits);
}

describe("Windows process-host request client", () => {
  test("renders one deterministic closed request with sorted environment and binary stdin", () => {
    const value = request();
    expect(value).toEqual({
      schema: "openprose.windows-process-host.request/1",
      requestId,
      executable: "C:\\Tools\\prime-agent.exe",
      wrapperExecutable: "C:\\OpenProse\\prose.exe",
      argv: ["run", "--model", "fixture"],
      cwd: "C:\\work",
      environment: [
        { name: "OPENPROSE_INVOCATION_ID", value: "invocation" },
        { name: "OPENPROSE_RECURSION_TOKEN", value: "recursion" },
        { name: "OPENPROSE_RUN_NONCE", value: "nonce" },
        { name: "PATH", value: "C:\\Windows" },
      ],
      stdinBase64: "AAEC/w==",
      limits: { maxStdoutBytes: 64, maxStderrBytes: 32, maxQueuedChunks: 8 },
      cancellation: { graceful: "ctrl-break", graceMs: 250, hardKillAfterMs: 2_000 },
      runTimeoutMs: 30_000,
    });
    const encoded = encodeWindowsHostRequest(value);
    expect(encoded.endsWith("\n")).toBe(true);
    expect(encoded.split("\n")).toHaveLength(2);
    expect(JSON.parse(encoded)).toEqual(value);
  });

  test("uses the same contained request protocol for a bounded version probe", () => {
    const probe = buildWindowsHostVersionProbeRequest({
      requestId: "probe.1",
      executable: "C:\\Tools\\prime-agent.exe",
      wrapperExecutable: "C:\\OpenProse\\prose.exe",
      cwd: "C:\\work",
      environment: {
        OPENPROSE_INVOCATION_ID: "probe",
        OPENPROSE_RECURSION_TOKEN: "probe-recursion",
        OPENPROSE_RUN_NONCE: "probe-nonce",
        PATH: "C:\\Windows",
      },
      timeoutMs: 5_000,
    });
    expect(probe.argv).toEqual(["--version"]);
    expect(probe.stdinBase64).toBe("");
    expect(probe.limits).toEqual({ maxStdoutBytes: 65_536, maxStderrBytes: 65_536, maxQueuedChunks: 32 });
    expect(probe.runTimeoutMs).toBe(5_000);
  });

  test.each([
    ["bad request id", { requestId: "bad id" }],
    ["relative executable", { executable: "prime-agent.exe" }],
    ["non-exe target", { executable: "C:\\Tools\\prime-agent.cmd" }],
    ["relative cwd", { cwd: "work" }],
    ["invalid environment name", { environment: { "BAD-NAME": "x" } }],
    ["NUL argument", { argv: ["a\0b"] }],
  ])("rejects %s before writing to the host", (_label, override) => {
    const base = {
      requestId,
      executable: "C:\\Tools\\prime-agent.exe",
      wrapperExecutable: "C:\\OpenProse\\prose.exe",
      argv: ["run"],
      cwd: "C:\\work",
      environment: {
        OPENPROSE_INVOCATION_ID: "invocation",
        OPENPROSE_RECURSION_TOKEN: "recursion",
        OPENPROSE_RUN_NONCE: "nonce",
        PATH: "C:\\Windows",
      },
      limits,
      graceful: "ctrl-break" as const,
      graceMs: 250,
      hardKillAfterMs: 2_000,
      runTimeoutMs: 30_000,
    };
    expect(() => buildWindowsHostRequest({ ...base, ...override })).toThrow();
  });

  test("writes one canonical cancel control even when cancellation races", async () => {
    const lines: string[] = [];
    const writer = new WindowsHostControlWriter(async (line) => { lines.push(line); });
    await Promise.all([writer.cancel("caller"), writer.cancel("timeout"), writer.cancel("caller")]);
    expect(lines).toEqual([
      '{"type":"cancel","schema":"openprose.windows-process-host.control/1","reason":"caller"}\n',
    ]);
    expect(writer.cancelRequested).toBe(true);
    await expect(new WindowsHostControlWriter(() => {}).cancel("bad reason")).rejects.toMatchObject({ code: "PROTOCOL_MALFORMED" });
  });

  test("enforces the reconciled request component caps and required audit metadata", () => {
    const base = {
      requestId,
      executable: "C:\\Tools\\prime-agent.exe",
      wrapperExecutable: "C:\\OpenProse\\prose.exe",
      argv: ["run"],
      cwd: "C:\\work",
      environment: {
        OPENPROSE_INVOCATION_ID: "invocation",
        OPENPROSE_RECURSION_TOKEN: "recursion",
        OPENPROSE_RUN_NONCE: "nonce",
      },
      limits,
      graceful: "ctrl-break" as const,
      graceMs: 250,
      hardKillAfterMs: 2_000,
      runTimeoutMs: 30_000,
    };
    expect(() => buildWindowsHostRequest({ ...base, argv: Array(65).fill("") })).toThrow();
    expect(() => buildWindowsHostRequest({ ...base, environment: { ...base.environment, OPENPROSE_RUN_NONCE: "" } })).toThrow();
    expect(() => buildWindowsHostRequest({ ...base, environment: { ...base.environment, LONG: "x".repeat(8_193) } })).toThrow();
    expect(() => buildWindowsHostRequest({ ...base, stdinBytes: new Uint8Array(16_777_216) })).toThrow();
  });

  test("parses host identity probes but does not turn self-report into native admission", () => {
    const identity = parseWindowsHostIdentity({
      schema: "openprose.windows-process-host.identity/1",
      component: "openprose-windows-process-host",
      componentVersion: "0.1.0",
      target: { os: "windows", architecture: "x86_64", nativeWindowsImplementation: true },
      protocol: {
        request: "openprose.windows-process-host.request/1",
        control: "openprose.windows-process-host.control/1",
        event: "openprose.windows-process-host.event/1",
        error: "openprose.windows-process-host.error/1",
        maxRequestBytes: 67_108_864,
        maxControlBytes: 4_096,
        limits: {
          maxDecodedStdinBytes: 16_777_215,
          maxStdinBase64Characters: 22_369_620,
          maxArgvItems: 64,
          maxArgumentCharacters: 32_766,
          maxEnvironmentItems: 64,
          maxEnvironmentNameCharacters: 128,
          maxEnvironmentValueCharacters: 8_192,
          maxPathCharacters: 32_766,
          maxEnvironmentBlockBytes: 4_194_304,
          maxRenderedCommandLineUtf16Units: 32_766,
        },
      },
      claims: {
        providerCallsMade: false,
        nativeWindowsRuntimeEvidence: false,
        strictWindowsContainmentReady: false,
      },
    });
    expect(windowsHostApplicability(identity)).toEqual({
      applicable: false,
      reason: "native-evidence-unavailable",
    });
    expect(() => parseWindowsHostIdentity({ ...identity, extra: true })).toThrow();
    expect(() => parseWindowsHostIdentity({
      ...identity,
      protocol: { ...identity.protocol, maxRequestBytes: 1_048_576 },
    })).toThrow();
    expect(() => parseWindowsHostIdentity({
      ...identity,
      claims: { ...identity.claims, strictWindowsContainmentReady: true },
    })).toThrow();
  });
});

describe("Windows process-host event parser", () => {
  test("accepts one ordered lifecycle and preserves opaque binary child streams", () => {
    const parser = validParser();
    parser.accept(started());
    parser.accept(stream("stdout", "AAH/", 1));
    parser.accept(stream("stderr", "8J+agA==", 2));
    parser.accept(exited(3, { stdoutBytes: 3, stderrBytes: 4 }));
    const outcome = parser.finish(0, new Uint8Array());
    expect([...outcome.stdout]).toEqual([0, 1, 255]);
    expect([...outcome.stderr]).toEqual([240, 159, 154, 128]);
    expect(outcome.pid).toBe(4242);
    expect(outcome.childExitCode).toBe(0);
    expect(outcome.cleanupVerified).toBe(true);
    expect(outcome.error).toBeNull();
  });

  test.each([
    ["wrong request identity", { ...started(), requestId: "other" }],
    ["wrong first sequence", started(1)],
    ["stream before start", stream("stdout", "", 0)],
    ["unknown top-level key", { ...started(), extra: true }],
    ["unknown payload key", { ...started(), payload: { ...(started().payload as object), extra: true } }],
    ["false containment evidence", { ...started(), payload: { ...(started().payload as object), killOnJobClose: false } }],
  ])("rejects %s", (_label, event) => {
    expect(() => validParser().accept(event)).toThrowError();
    try { validParser().accept(event); } catch (caught) { expect(caught).toMatchObject({ code: "PROTOCOL_MALFORMED" }); }
  });

  test("rejects gaps, duplicates, records after terminal, PID changes, and stream/type mismatches", () => {
    const gap = validParser();
    gap.accept(started());
    expect(() => gap.accept(stream("stdout", "", 2))).toThrow();

    const duplicate = validParser();
    duplicate.accept(started());
    expect(() => duplicate.accept(started(1))).toThrow();

    const mismatch = validParser();
    mismatch.accept(started());
    expect(() => mismatch.accept({ ...stream("stdout", "", 1), type: "child.stderr" })).toThrow();

    const pid = validParser();
    pid.accept(started());
    expect(() => pid.accept(exited(1, { pid: 99 }))).toThrow();

    const after = validParser();
    after.accept(started());
    after.accept(exited(1));
    expect(() => after.accept(stream("stdout", "", 2))).toThrow();
    expect(() => after.finish(0, new Uint8Array())).not.toThrow();
    expect(() => after.finish(0, new Uint8Array())).toThrow();
  });

  test.each(["A", "AA=A", "AB==", "AAA", "AAAA=", "AA A="])("rejects noncanonical base64 %s", (data) => {
    const parser = validParser();
    parser.accept(started());
    expect(() => parser.accept(stream("stdout", data, 1))).toThrow();
  });

  test("bounds decoded streams independently and validates authoritative byte counters", () => {
    const stdoutOverflow = new WindowsHostEventParser(requestId, { ...limits, maxAggregateStdoutBytes: 2 });
    stdoutOverflow.accept(started());
    expect(() => stdoutOverflow.accept(stream("stdout", "AAH/", 1))).toThrow();

    const mismatch = validParser();
    mismatch.accept(started());
    mismatch.accept(stream("stdout", "AQ==", 1));
    expect(() => mismatch.accept(exited(2, { stdoutBytes: 2 }))).toThrow();

    const recordOverflow = new WindowsHostEventParser(requestId, {
      ...limits,
      maxRecordBytes: 512,
      maxAggregateStdoutBytes: 512,
    });
    recordOverflow.accept(started());
    expect(() => recordOverflow.accept(stream("stdout", Buffer.alloc(300).toString("base64"), 1))).toThrow();
  });

  test("admits output-limit accounting only when an authoritative counter exceeded its cap", () => {
    const parser = validParser();
    parser.accept(started());
    parser.accept(stream("stdout", Buffer.alloc(64).toString("base64"), 1));
    parser.accept(exited(2, { termination: "output-limit", stdoutBytes: 65 }));
    expect(parser.finish(0, new Uint8Array()).error).toMatchObject({ code: "HARNESS_FAILED" });

    const falseClaim = validParser();
    falseClaim.accept(started());
    expect(() => falseClaim.accept(exited(1, { termination: "output-limit" }))).toThrow();
  });

  test("makes host cleanup evidence authoritative over cancellation or child status", () => {
    const parser = validParser();
    parser.accept(started());
    parser.accept(exited(1, {
      processExitCode: 1,
      termination: "cancelled",
      gracefulControlAttempted: true,
      activeProcessesBeforeCleanup: 2,
      activeProcessesAfterCleanup: 1,
      cleanupVerified: false,
      hardKillUsed: true,
    }));
    expect(parser.finish(25, new Uint8Array())).toMatchObject({
      cleanupVerified: false,
      error: { code: "PROCESS_CLEANUP_FAILED" },
    });
  });

  test.each([
    ["cancelled", "CANCELLED", "caller"],
    ["timeout", "CANCELLED", "timeout"],
    ["parent-disconnected", "HARNESS_FAILED", null],
    ["io-failure", "HARNESS_FAILED", null],
    ["protocol-failure", "HARNESS_FAILED", null],
    ["backpressure", "HARNESS_FAILED", null],
  ])("maps %s termination without inventing semantic status", (termination, code, reason) => {
    const parser = validParser();
    parser.accept(started());
    parser.accept(exited(1, { termination, gracefulControlAttempted: true, hardKillUsed: true }));
    expect(parser.finish(0, new Uint8Array())).toMatchObject({
      error: { code },
      cancellationReason: reason,
    });
  });

  test("maps host bootstrap failure and silent disconnect while bounding diagnostics", () => {
    const bootstrap = validParser().finish(70, new TextEncoder().encode(
      '{"schema":"openprose.windows-process-host.error/1","code":"CREATE_PROCESS_FAILED","operation":"CreateProcessW","win32":2}\n',
    ));
    expect(bootstrap.error).toMatchObject({ code: "HARNESS_UNAVAILABLE" });

    const cleanup = validParser().finish(70, new TextEncoder().encode(
      '{"schema":"openprose.windows-process-host.error/1","code":"JOB_QUERY_FAILED","operation":"QueryInformationJobObject","win32":5}\n',
    ));
    expect(cleanup.error).toMatchObject({ code: "PROCESS_CLEANUP_FAILED" });

    expect(validParser().finish(70, new Uint8Array()).error).toMatchObject({ code: "PROTOCOL_TRUNCATED" });
    expect(validParser().finish(70, new Uint8Array(65_537)).error).toMatchObject({ code: "HARNESS_FAILED" });
  });

  test("rejects malformed, multi-record, non-UTF-8, or unexpected post-start diagnostics", () => {
    const malformed = validParser().finish(70, new TextEncoder().encode("not-json\n"));
    expect(malformed.error).toMatchObject({ code: "PROTOCOL_MALFORMED" });
    const multiple = validParser().finish(70, new TextEncoder().encode(
      '{"schema":"openprose.windows-process-host.error/1","code":"X","operation":"x","win32":null}\n{}\n',
    ));
    expect(multiple.error).toMatchObject({ code: "PROTOCOL_MALFORMED" });
    expect(validParser().finish(70, Uint8Array.from([0xff])).error).toMatchObject({ code: "PROTOCOL_MALFORMED" });

    const afterStart = validParser();
    afterStart.accept(started());
    expect(afterStart.finish(70, new TextEncoder().encode(
      '{"schema":"openprose.windows-process-host.error/1","code":"OUTPUT_DISCONNECTED","operation":"writer","win32":null}\n',
    )).error).toMatchObject({ code: "PROCESS_CLEANUP_FAILED" });

    const poisoned = validParser();
    poisoned.accept(started());
    try { poisoned.accept(stream("stdout", "", 3)); } catch { /* asserted by final classification */ }
    expect(poisoned.finish(70, new Uint8Array()).error).toMatchObject({ code: "PROCESS_CLEANUP_FAILED" });
  });

  test("uses contained stdout for version probes and rejects bad exit/output", () => {
    const parser = validParser();
    parser.accept(started());
    parser.accept(stream("stdout", Buffer.from("prime-agent 0.8.1\n").toString("base64"), 1));
    parser.accept(exited(2, { stdoutBytes: 18 }));
    expect(acceptWindowsVersionProbe(parser.finish(0, new Uint8Array()), /^prime-agent 0\.8\.1$/)).toBe("prime-agent 0.8.1");

    const stderrParser = validParser();
    stderrParser.accept(started());
    stderrParser.accept(stream("stderr", Buffer.from("prime-agent 0.8.1\n").toString("base64"), 1));
    stderrParser.accept(exited(2, { stderrBytes: 18 }));
    expect(acceptWindowsVersionProbe(
      stderrParser.finish(0, new Uint8Array()),
      /^prime-agent 0\.8\.1$/,
      "stderr",
    )).toBe("prime-agent 0.8.1");

    const failed = validParser();
    failed.accept(started());
    failed.accept(exited(1, { processExitCode: 1 }));
    expect(() => acceptWindowsVersionProbe(failed.finish(0, new Uint8Array()), /./)).toThrow();
  });
});
