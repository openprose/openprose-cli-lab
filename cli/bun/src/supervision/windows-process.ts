import { createHash } from "node:crypto";
import { lstat, open, realpath, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import transportLimits from "../../../shared/capabilities/transport-limits.v1.json" with { type: "json" };
import { WINDOWS_HOST_ADMISSION_ENABLED, WINDOWS_HOST_EXPECTED_SHA256 } from "../core/build";
import { failure } from "../core/errors";
import { RunnerFailure } from "../core/types";
import { buildChildEnvironment, collectSecretValues, redactDiagnostic } from "./environment";
import { BoundedJsonLineDecoder, normalizeProtocolFailure, readBoundedJsonLines } from "./jsonl";
import type {
  ProcessSupervisionRequest,
  ProcessSupervisionResult,
  RawTransportEvent,
  StructuredProtocolState,
  TransportLimits,
} from "./types";
import {
  WindowsHostControlWriter,
  WindowsHostEventParser,
  acceptWindowsVersionProbe,
  buildWindowsHostRequest,
  buildWindowsHostVersionProbeRequest,
  encodeWindowsHostRequest,
  parseWindowsHostIdentity,
  type WindowsHostAcceptedEvent,
  type WindowsHostIdentity,
  type WindowsHostOutcome,
  type WindowsHostRequest,
} from "./windows-host";

const HOST_FILENAME = "openprose-windows-process-host.exe";
const MAX_HOST_BINARY_BYTES = 512 * 1_048_576;
const MAX_HOST_PROBE_BYTES = 65_536;
// Starting the authenticated helper is a separate phase from starting the
// harness inside its Job. Keep its handshake bounded without spending the
// caller's child-startup or version-probe budget on wrapper launch overhead.
const HOST_IDENTITY_HANDSHAKE_TIMEOUT_MS = 5_000;
const strictDecoder = new TextDecoder("utf-8", { fatal: true });
const diagnosticDecoder = new TextDecoder("utf-8");
const hostEnvironmentNames = [
  "PATH", "HOME", "USERPROFILE", "SystemRoot", "WINDIR", "COMSPEC",
  "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
] as const;

export interface WindowsHostIntegrationDependencies {
  admissionEnabled: boolean;
  expectedSha256: string | null;
  /** Provider-free host-neutral tests only; product calls never supply this. */
  wirePath?: (canonicalPath: string, kind: "executable" | "wrapper" | "cwd") => string;
}

const compiledDependencies: WindowsHostIntegrationDependencies = {
  admissionEnabled: WINDOWS_HOST_ADMISSION_ENABLED,
  expectedSha256: WINDOWS_HOST_EXPECTED_SHA256,
};

export async function superviseWindowsStructuredProcess(
  request: ProcessSupervisionRequest,
  dependencies: WindowsHostIntegrationDependencies = compiledDependencies,
  defaultProtocolFactory?: (processGroupId: number) => StructuredProtocolState,
): Promise<ProcessSupervisionResult> {
  rejectAmbientRecursion(request.environment);
  if (request.cancelSignal?.aborted === true) {
    throw failure("CANCELLED", { reason: "caller", phase: "before-spawn" });
  }
  const prepared = await prepareWindowsExecution(
    request.executable,
    request.cwd,
    request.environment,
    request.wrapperExecutable,
    HOST_IDENTITY_HANDSHAKE_TIMEOUT_MS,
    dependencies,
  );
  const limits = request.limits ?? transportLimits;
  const childEnvironment = buildChildEnvironment(request.environment, {
    invocationId: request.invocationId,
    recursionToken: request.recursionToken,
    runNonce: request.runNonce,
  }, request.additionalEnvironmentNames);
  const hostRequest = buildWindowsHostRequest({
    requestId: request.invocationId,
    executable: dependencies.wirePath?.(prepared.executable, "executable") ?? prepared.executable,
    wrapperExecutable: dependencies.wirePath?.(prepared.wrapper, "wrapper") ?? prepared.wrapper,
    argv: request.argv,
    cwd: dependencies.wirePath?.(prepared.cwd, "cwd") ?? prepared.cwd,
    environment: childEnvironment,
    ...(request.stdinBytes === undefined ? {} : { stdinBytes: request.stdinBytes }),
    limits,
    graceful: "ctrl-break",
    graceMs: request.graceMs,
    hardKillAfterMs: request.hardKillAfterMs,
    runTimeoutMs: request.runTimeoutMs,
  });

  let protocol = request.protocol ?? null;
  const events: RawTransportEvent[] = [];
  let protocolError: RunnerFailure | null = null;
  let firstChildRecordObserved = false;
  const childDecoder = new BoundedJsonLineDecoder(limits);
  const acceptChildRecord = async (record: unknown): Promise<void> => {
    firstChildRecordObserved = true;
    if (protocol === null) throw failure("INTERNAL_ERROR", { reason: "No structured protocol was available after host.started." });
    const parsed = protocol.accept(record);
    if (parsed !== null) {
      events.push(parsed);
      if (parsed.type === "assistant.message") {
        await request.onAcceptedAssistantMessage?.(parsed.text ?? "");
      }
    }
  };

  let startupTimedOut = false;
  const startTimer = setTimeout(() => {
    if (!firstChildRecordObserved) startupTimedOut = true;
  }, request.startupTimeoutMs);
  let raw: RawHostRun;
  try {
    raw = await runHostRequest({
      helper: prepared.helper,
      ambient: request.environment,
      request: hostRequest,
      limits,
      ...(request.cancelSignal === undefined ? {} : { cancelSignal: request.cancelSignal }),
      ...(request.cancelAfterMs === undefined ? {} : { cancelAfterMs: request.cancelAfterMs }),
      outerTimeoutMs: boundedOuterTimeout(request.runTimeoutMs, request.graceMs, request.hardKillAfterMs),
      startupCancelled: () => startupTimedOut,
      protocolCancelled: () => protocolError !== null,
      onEvent: async (event) => {
        if (event.type === "host.started" && protocol === null) {
          protocol = defaultProtocolFactory?.(event.pid) ?? null;
        } else if (event.type === "child.stdout") {
          if (protocolError === null) {
            try {
              await childDecoder.push(event.bytes, acceptChildRecord);
            } catch (caught) {
              protocolError = normalizeProtocolFailure(caught);
            }
          }
        }
      },
    });
  } finally {
    clearTimeout(startTimer);
    if (protocolError === null) {
      try { childDecoder.finish(); } catch (caught) { protocolError = normalizeProtocolFailure(caught); }
    }
  }

  const host = raw.outcome;
  const activeProtocol = protocol;
  let error = chooseWindowsError({
    host,
    hostStreamError: raw.streamError,
    hostDiagnosticError: raw.diagnosticError,
    controlError: raw.controlError,
    protocolError,
    startupTimedOut,
    outerTimedOut: raw.outerTimedOut,
    terminalObserved: activeProtocol?.terminalEventObserved ?? false,
  });
  let cancellationReason = host.cancellationReason;
  if (error?.code === "STARTUP_TIMEOUT" || error?.code === "PROCESS_CLEANUP_FAILED") {
    cancellationReason = null;
  }
  const secrets = collectSecretValues(request.environment);
  const stderr = redactDiagnostic(diagnosticDecoder.decode(host.stderr), secrets);
  // The host's empty-Job evidence always outranks nominal child/protocol completion.
  if (!host.cleanupVerified && error?.code !== "PROCESS_CLEANUP_FAILED") {
    error = failure("PROCESS_CLEANUP_FAILED", { reason: "The Windows host did not prove an empty owned Job." });
    cancellationReason = null;
  }
  return {
    executable: prepared.executable,
    pid: host.pid ?? 0,
    processGroupId: host.pid,
    exitCode: host.childExitCode,
    signal: null,
    events,
    terminalEventObserved: activeProtocol?.terminalEventObserved ?? false,
    terminalEnvelope: activeProtocol?.terminalEnvelope ?? null,
    harnessVersion: activeProtocol?.harnessVersion ?? null,
    stderr,
    error,
    cancellationReason,
    cleanupVerified: host.cleanupVerified,
  };
}

export async function probeWindowsExecutableVersion(
  executableInput: string,
  cwdInput: string,
  ambient: Readonly<Record<string, string | undefined>>,
  wrapperExecutable: string | undefined,
  timeoutMs: number,
  expected: RegExp,
  versionStream: "stdout" | "stderr" = "stdout",
  dependencies: WindowsHostIntegrationDependencies = compiledDependencies,
): Promise<string> {
  rejectAmbientRecursion(ambient);
  const prepared = await prepareWindowsExecution(
    executableInput,
    cwdInput,
    ambient,
    wrapperExecutable,
    HOST_IDENTITY_HANDSHAKE_TIMEOUT_MS,
    dependencies,
  );
  const environment = buildChildEnvironment(ambient, {
    invocationId: "probe",
    recursionToken: "openprose:probe",
    runNonce: "probe",
  });
  const request = buildWindowsHostVersionProbeRequest({
    requestId: "probe",
    executable: dependencies.wirePath?.(prepared.executable, "executable") ?? prepared.executable,
    wrapperExecutable: dependencies.wirePath?.(prepared.wrapper, "wrapper") ?? prepared.wrapper,
    cwd: dependencies.wirePath?.(prepared.cwd, "cwd") ?? prepared.cwd,
    environment,
    timeoutMs,
  });
  const limits: TransportLimits = {
    maxRecordBytes: MAX_HOST_PROBE_BYTES,
    maxAggregateStdoutBytes: MAX_HOST_PROBE_BYTES,
    maxAggregateStderrBytes: MAX_HOST_PROBE_BYTES,
    maxQueuedRecords: 32,
  };
  const raw = await runHostRequest({
    helper: prepared.helper,
    ambient,
    request,
    limits,
    outerTimeoutMs: boundedOuterTimeout(timeoutMs, 0, Math.min(timeoutMs, 60_000)),
  });
  if (raw.outerTimedOut) throw failure("PROCESS_CLEANUP_FAILED", { phase: "version-probe", reason: "The Windows host exceeded its outer watchdog." });
  if (raw.streamError !== null) throw raw.streamError;
  if (raw.diagnosticError !== null) throw raw.diagnosticError;
  if (raw.controlError !== null) throw raw.controlError;
  return acceptWindowsVersionProbe(raw.outcome, expected, versionStream);
}

interface PreparedWindowsExecution {
  executable: string;
  wrapper: string;
  cwd: string;
  helper: string;
  identity: WindowsHostIdentity;
}

async function prepareWindowsExecution(
  executableInput: string,
  cwdInput: string,
  ambient: Readonly<Record<string, string | undefined>>,
  wrapperInput: string | undefined,
  identityTimeoutMs: number,
  dependencies: WindowsHostIntegrationDependencies,
): Promise<PreparedWindowsExecution> {
  requireCompiledAdmission(dependencies);
  const wrapper = await canonicalRegularFile(wrapperInput ?? process.execPath, "wrapper executable");
  const executable = await canonicalRegularFile(executableInput, "harness executable");
  if (sameWindowsPath(executable, wrapper)) {
    throw failure("RECURSIVE_INVOCATION", { executable, wrapperExecutable: wrapper });
  }
  const cwd = await canonicalDirectory(cwdInput);
  const expectedSha256 = dependencies.expectedSha256!;
  const helper = await verifySiblingHost(wrapper, expectedSha256);
  const identity = await probeHostIdentity(helper, ambient, identityTimeoutMs);
  if (identity.target.os !== "windows" || !identity.target.nativeWindowsImplementation) {
    throw failure("TRANSPORT_UNSUPPORTED", {
      reason: "The verified sibling is not a native Windows process host.",
      target: identity.target,
    });
  }
  // Re-resolve and re-hash after the probe so replacement cannot ride the
  // identity process into the actual supervision spawn.
  const verifiedAgain = await verifySiblingHost(wrapper, expectedSha256);
  if (!sameWindowsPath(helper, verifiedAgain)) {
    throw failure("HARNESS_INCOMPATIBLE", { reason: "The Windows process-host identity changed between probe and spawn." });
  }
  return { executable, wrapper, cwd, helper: verifiedAgain, identity };
}

function requireCompiledAdmission(dependencies: WindowsHostIntegrationDependencies): void {
  if (!dependencies.admissionEnabled) {
    throw failure("TRANSPORT_UNSUPPORTED", {
      reason: "Strict Windows process transport is not admitted in this build.",
      compiledAdmission: false,
    });
  }
  if (dependencies.expectedSha256 === null || !/^[0-9a-f]{64}$/.test(dependencies.expectedSha256)) {
    throw failure("TRANSPORT_UNSUPPORTED", {
      reason: "Strict Windows process transport has no compiled helper digest.",
      compiledAdmission: true,
    });
  }
}

async function verifySiblingHost(wrapper: string, expectedSha256: string): Promise<string> {
  const candidate = join(dirname(wrapper), HOST_FILENAME);
  let candidateMetadata;
  try {
    candidateMetadata = await lstat(candidate);
  } catch (caught) {
    throw failure("HARNESS_UNAVAILABLE", { helper: candidate, reason: safeMessage(caught) });
  }
  if (!candidateMetadata.isFile() || candidateMetadata.isSymbolicLink()) {
    throw failure("HARNESS_INCOMPATIBLE", { helper: candidate, reason: "The sibling helper is not a direct regular file." });
  }
  const canonical = await canonicalRegularFile(candidate, "Windows process host");
  if (!sameWindowsPath(dirname(canonical), dirname(wrapper))) {
    throw failure("HARNESS_INCOMPATIBLE", { helper: canonical, reason: "The canonical helper is not a sibling of the canonical wrapper." });
  }
  const handle = await open(canonical, "r");
  let verifiedSnapshot: { dev: number; ino: number; size: number; mtimeMs: number } | null = null;
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size > MAX_HOST_BINARY_BYTES) {
      throw failure("HARNESS_INCOMPATIBLE", { helper: canonical, reason: "The helper binary is not a bounded regular file." });
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (!sameFileSnapshot(before, after, bytes.byteLength)) {
      throw failure("HARNESS_INCOMPATIBLE", { helper: canonical, reason: "The helper binary changed while it was being verified." });
    }
    verifiedSnapshot = { dev: after.dev, ino: after.ino, size: after.size, mtimeMs: after.mtimeMs };
    const observed = createHash("sha256").update(bytes).digest("hex");
    if (observed !== expectedSha256) {
      throw failure("HARNESS_INCOMPATIBLE", {
        helper: canonical,
        reason: "The sibling helper bytes do not match the compiled SHA-256.",
        expectedSha256,
        observedSha256: observed,
      });
    }
  } finally {
    await handle.close();
  }
  const finalCanonical = await realpath(candidate);
  if (!sameWindowsPath(finalCanonical, canonical)) {
    throw failure("HARNESS_INCOMPATIBLE", { helper: candidate, reason: "The helper path changed after byte verification." });
  }
  const finalMetadata = await stat(finalCanonical);
  if (verifiedSnapshot === null || !sameFileSnapshot(verifiedSnapshot, finalMetadata, finalMetadata.size)) {
    throw failure("HARNESS_INCOMPATIBLE", { helper: candidate, reason: "The helper file identity changed after byte verification." });
  }
  return canonical;
}

async function probeHostIdentity(
  helper: string,
  ambient: Readonly<Record<string, string | undefined>>,
  timeoutMs: number,
): Promise<WindowsHostIdentity> {
  let child: Bun.Subprocess<"ignore", "pipe", "pipe">;
  try {
    child = Bun.spawn({
      cmd: [helper, "--identity-json"],
      env: closedHostEnvironment(ambient),
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
    });
  } catch (caught) {
    throw failure("HARNESS_UNAVAILABLE", { helper, reason: safeMessage(caught) });
  }
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    try { child.kill(); } catch { /* classified below */ }
  }, timeoutMs);
  let exitCode: number;
  let stdout: Uint8Array;
  let stderr: Uint8Array;
  try {
    [exitCode, stdout, stderr] = await Promise.all([
      child.exited,
      readBoundedBytes(child.stdout, MAX_HOST_PROBE_BYTES),
      readBoundedBytes(child.stderr, MAX_HOST_PROBE_BYTES),
    ]);
  } catch {
    try { child.kill(); } catch { /* the exit wait below is still authoritative */ }
    await child.exited;
    throw failure("HARNESS_INCOMPATIBLE", { helper, reason: "The helper identity probe exceeded its closed output contract." });
  } finally {
    clearTimeout(timer);
  }
  if (timedOut) throw failure("STARTUP_TIMEOUT", { phase: "windows-host-identity", helper });
  if (exitCode !== 0 || stderr.byteLength !== 0) {
    throw failure("HARNESS_INCOMPATIBLE", { helper, probeExitCode: exitCode, reason: "The helper identity probe failed." });
  }
  const value = parseSingleJsonLine(stdout, "Windows helper identity");
  try {
    return parseWindowsHostIdentity(value);
  } catch {
    throw failure("HARNESS_INCOMPATIBLE", { helper, reason: "The helper identity is not compatible with the compiled client protocol." });
  }
}

interface RunHostRequestInput {
  helper: string;
  ambient: Readonly<Record<string, string | undefined>>;
  request: WindowsHostRequest;
  limits: TransportLimits;
  outerTimeoutMs: number;
  cancelSignal?: AbortSignal;
  cancelAfterMs?: number;
  startupCancelled?: () => boolean;
  protocolCancelled?: () => boolean;
  onEvent?: (event: WindowsHostAcceptedEvent) => void | Promise<void>;
}

interface RawHostRun {
  outcome: WindowsHostOutcome;
  streamError: RunnerFailure | null;
  diagnosticError: RunnerFailure | null;
  controlError: RunnerFailure | null;
  outerTimedOut: boolean;
}

async function runHostRequest(input: RunHostRequestInput): Promise<RawHostRun> {
  let child: Bun.Subprocess<"pipe", "pipe", "pipe">;
  try {
    child = Bun.spawn({
      cmd: [input.helper],
      env: closedHostEnvironment(input.ambient),
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    });
  } catch (caught) {
    throw failure("HARNESS_UNAVAILABLE", { helper: input.helper, reason: safeMessage(caught) });
  }
  const sink = child.stdin;
  const control = new WindowsHostControlWriter(async (line) => {
    sink.write(line);
    await sink.flush();
  });
  let streamError: RunnerFailure | null = null;
  let diagnosticError: RunnerFailure | null = null;
  let controlError: RunnerFailure | null = null;
  let diagnostic: Uint8Array<ArrayBufferLike> = new Uint8Array();
  let outerTimedOut = false;
  const parser = new WindowsHostEventParser(input.request.requestId, input.limits);
  const requestLine = encodeWindowsHostRequest(input.request);

  const requestCancel = (reason: string): void => {
    void control.cancel(reason).catch((caught) => {
      controlError = caught instanceof RunnerFailure
        ? caught
        : failure("HARNESS_FAILED", { reason: "The Windows host cancel control could not be written." });
    });
  };
  const abort = (): void => requestCancel("caller");
  input.cancelSignal?.addEventListener("abort", abort, { once: true });
  if (input.cancelSignal?.aborted === true) requestCancel("caller");
  const cancelTimer = input.cancelAfterMs === undefined
    ? undefined
    : setTimeout(() => requestCancel("caller"), input.cancelAfterMs);
  const cancellationPoll = input.startupCancelled === undefined && input.protocolCancelled === undefined
    ? undefined
    : setInterval(() => {
      if (input.startupCancelled?.()) requestCancel("startup-timeout");
      else if (input.protocolCancelled?.()) requestCancel("protocol-failure");
    }, 5);
  const outerTimer = setTimeout(() => {
    outerTimedOut = true;
    try { child.kill(); } catch { /* missing cleanup evidence is authoritative below */ }
  }, input.outerTimeoutMs);

  const stdoutPromise = readBoundedJsonLines(child.stdout, hostWireLimits(input.limits), async (record) => {
    const event = parser.accept(record);
    await input.onEvent?.(event);
  }).catch((caught) => {
    streamError = normalizeProtocolFailure(caught);
    requestCancel("protocol-failure");
  });
  const stderrPromise = readBoundedBytes(child.stderr, MAX_HOST_PROBE_BYTES)
    .then((bytes) => { diagnostic = bytes; })
    .catch((caught) => {
      diagnosticError = caught instanceof RunnerFailure
        ? caught
        : failure("HARNESS_FAILED", { reason: "Windows host diagnostics could not be read." });
      requestCancel("diagnostic-failure");
    });
  try {
    sink.write(requestLine);
    await sink.flush();
  } catch {
    requestCancel("request-write-failure");
    try { sink.end(); } catch { /* host exit remains bounded by watchdog */ }
  }
  const hostExitCode = await child.exited;
  await Promise.all([stdoutPromise, stderrPromise]);
  clearTimeout(outerTimer);
  if (cancelTimer !== undefined) clearTimeout(cancelTimer);
  if (cancellationPoll !== undefined) clearInterval(cancellationPoll);
  input.cancelSignal?.removeEventListener("abort", abort);
  try { sink.end(); } catch { /* already closed by process exit */ }
  const outcome = parser.finish(hostExitCode, diagnostic);
  if (outerTimedOut && outcome.error?.code !== "PROCESS_CLEANUP_FAILED") {
    outcome.cleanupVerified = false;
    outcome.error = failure("PROCESS_CLEANUP_FAILED", { reason: "The Windows host exceeded the outer cleanup watchdog." });
  }
  return { outcome, streamError, diagnosticError, controlError, outerTimedOut };
}

function chooseWindowsError(input: {
  host: WindowsHostOutcome;
  hostStreamError: RunnerFailure | null;
  hostDiagnosticError: RunnerFailure | null;
  controlError: RunnerFailure | null;
  protocolError: RunnerFailure | null;
  startupTimedOut: boolean;
  outerTimedOut: boolean;
  terminalObserved: boolean;
}): RunnerFailure | null {
  if (input.outerTimedOut || !input.host.cleanupVerified || input.host.error?.code === "PROCESS_CLEANUP_FAILED") {
    return input.host.error?.code === "PROCESS_CLEANUP_FAILED"
      ? input.host.error
      : failure("PROCESS_CLEANUP_FAILED", { reason: "The Windows helper did not provide authoritative empty-Job evidence." });
  }
  if (input.startupTimedOut) return failure("STARTUP_TIMEOUT");
  if (input.hostStreamError !== null) return input.hostStreamError;
  if (input.hostDiagnosticError !== null) return input.hostDiagnosticError;
  if (input.protocolError !== null && input.protocolError.code !== "PROTOCOL_TRUNCATED") return input.protocolError;
  if (input.host.error !== null) return input.host.error;
  if (input.protocolError !== null) return input.protocolError;
  if (input.controlError !== null && !input.terminalObserved) return input.controlError;
  if (!input.terminalObserved) {
    return failure("PROTOCOL_TRUNCATED", { reason: "The contained harness reached EOF without its terminal record." });
  }
  return null;
}

function hostWireLimits(limits: TransportLimits): TransportLimits {
  const aggregate = (limits.maxAggregateStdoutBytes + limits.maxAggregateStderrBytes) * 4 + 16 * 1_048_576;
  return {
    maxRecordBytes: limits.maxRecordBytes,
    maxAggregateStdoutBytes: aggregate,
    maxAggregateStderrBytes: MAX_HOST_PROBE_BYTES,
    maxQueuedRecords: limits.maxQueuedRecords,
  };
}

function boundedOuterTimeout(runTimeoutMs: number, graceMs: number, hardKillAfterMs: number): number {
  return Math.min(86_500_000, runTimeoutMs + graceMs + hardKillAfterMs + 1_000);
}

async function canonicalRegularFile(input: string, label: string): Promise<string> {
  try {
    const canonical = await realpath(input);
    if (!(await stat(canonical)).isFile()) throw new Error("not a regular file");
    return canonical;
  } catch (caught) {
    throw failure("HARNESS_UNAVAILABLE", { executable: input, reason: `${label}: ${safeMessage(caught)}` });
  }
}

async function canonicalDirectory(input: string): Promise<string> {
  try {
    const canonical = await realpath(input);
    if (!(await stat(canonical)).isDirectory()) throw new Error("not a directory");
    return canonical;
  } catch (caught) {
    throw failure("CONFIG_INVALID", { cwd: input, reason: safeMessage(caught) });
  }
}

function closedHostEnvironment(ambient: Readonly<Record<string, string | undefined>>): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const name of hostEnvironmentNames) {
    const value = ambient[name];
    if (value !== undefined) environment[name] = value;
  }
  return environment;
}

async function readBoundedBytes(stream: ReadableStream<Uint8Array>, maximum: number): Promise<Uint8Array> {
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maximum) throw failure("HARNESS_FAILED", { reason: "A Windows host diagnostic channel exceeded its bound." });
      chunks.push(value.slice());
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function parseSingleJsonLine(bytes: Uint8Array, label: string): unknown {
  let text: string;
  try {
    text = strictDecoder.decode(bytes);
  } catch {
    throw failure("HARNESS_INCOMPATIBLE", { reason: `${label} was not UTF-8.` });
  }
  if (!text.endsWith("\n") || text.slice(0, -1).includes("\n") || text.includes("\r")) {
    throw failure("HARNESS_INCOMPATIBLE", { reason: `${label} was not one LF-terminated record.` });
  }
  try {
    return JSON.parse(text.slice(0, -1));
  } catch {
    throw failure("HARNESS_INCOMPATIBLE", { reason: `${label} was not JSON.` });
  }
}

function sameFileSnapshot(before: { dev: number; ino: number; size: number; mtimeMs: number }, after: {
  dev: number; ino: number; size: number; mtimeMs: number;
}, bytesRead: number): boolean {
  return before.dev === after.dev && before.ino === after.ino && before.size === after.size
    && before.mtimeMs === after.mtimeMs && after.size === bytesRead;
}

function sameWindowsPath(left: string, right: string): boolean {
  return left.toLocaleLowerCase("en-US") === right.toLocaleLowerCase("en-US");
}

function rejectAmbientRecursion(ambient: Readonly<Record<string, string | undefined>>): void {
  if (ambient.OPENPROSE_RECURSION_TOKEN !== undefined) {
    throw failure("RECURSIVE_INVOCATION", { reason: "A recursion marker was already present in the wrapper environment." });
  }
}

function safeMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "operation failed";
}
