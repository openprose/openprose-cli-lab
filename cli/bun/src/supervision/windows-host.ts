import { failure } from "../core/errors";
import type { RunnerFailure } from "../core/types";
import type { CancellationReason, TransportLimits } from "./types";

export const WINDOWS_HOST_REQUEST_SCHEMA = "openprose.windows-process-host.request/1" as const;
export const WINDOWS_HOST_CONTROL_SCHEMA = "openprose.windows-process-host.control/1" as const;
export const WINDOWS_HOST_EVENT_SCHEMA = "openprose.windows-process-host.event/1" as const;
export const WINDOWS_HOST_ERROR_SCHEMA = "openprose.windows-process-host.error/1" as const;
export const WINDOWS_HOST_IDENTITY_SCHEMA = "openprose.windows-process-host.identity/1" as const;

const MAX_REQUEST_BYTES = 67_108_864;
const MAX_CONTROL_BYTES = 4_096;
const MAX_CHILD_STDIN_BYTES = 16_777_215;
const MAX_STDIN_BASE64_CHARS = 22_369_620;
const MAX_DIAGNOSTIC_BYTES = 65_536;
const MAX_IDENTIFIER_BYTES = 128;
const MAX_ARGUMENTS = 64;
const MAX_ARGUMENT_CHARS = 32_766;
const MAX_ENVIRONMENT_ENTRIES = 64;
const MAX_ENVIRONMENT_NAME_CHARS = 128;
const MAX_ENVIRONMENT_VALUE_CHARS = 8_192;
const MAX_PATH_CHARS = 32_766;
const MAX_ENVIRONMENT_BLOCK_BYTES = 4_194_304;
const MAX_STREAM_BYTES = 67_108_864;
const MAX_QUEUED_CHUNKS = 1_024;
const MAX_TIMEOUT_MS = 86_400_000;
const MAX_CANCELLATION_MS = 60_000;
const textEncoder = new TextEncoder();
const strictTextDecoder = new TextDecoder("utf-8", { fatal: true });

export interface WindowsHostRequest {
  schema: typeof WINDOWS_HOST_REQUEST_SCHEMA;
  requestId: string;
  executable: string;
  wrapperExecutable: string;
  argv: string[];
  cwd: string;
  environment: Array<{ name: string; value: string }>;
  stdinBase64: string;
  limits: {
    maxStdoutBytes: number;
    maxStderrBytes: number;
    maxQueuedChunks: number;
  };
  cancellation: {
    graceful: "ctrl-break" | "none";
    graceMs: number;
    hardKillAfterMs: number;
  };
  runTimeoutMs: number;
}

export interface WindowsHostRequestInput {
  requestId: string;
  executable: string;
  wrapperExecutable: string;
  argv: readonly string[];
  cwd: string;
  environment: Readonly<Record<string, string>>;
  stdinBytes?: Uint8Array;
  limits: TransportLimits;
  graceful: "ctrl-break" | "none";
  graceMs: number;
  hardKillAfterMs: number;
  runTimeoutMs: number;
}

export interface WindowsHostVersionProbeInput {
  requestId: string;
  executable: string;
  wrapperExecutable: string;
  cwd: string;
  environment: Readonly<Record<string, string>>;
  timeoutMs: number;
}

export type WindowsHostTermination =
  | "natural"
  | "cancelled"
  | "parent-disconnected"
  | "timeout"
  | "output-limit"
  | "io-failure"
  | "protocol-failure"
  | "backpressure";

export interface WindowsHostTerminal {
  pid: number;
  processExitCode: number;
  termination: WindowsHostTermination;
  gracefulControlAttempted: boolean;
  gracefulControlDelivered: boolean;
  hardKillUsed: boolean;
  activeProcessesBeforeCleanup: number;
  activeProcessesAfterCleanup: number;
  cleanupVerified: boolean;
  stdoutBytes: number;
  stderrBytes: number;
}

export type WindowsHostAcceptedEvent =
  | { type: "host.started"; pid: number }
  | { type: "child.stdout" | "child.stderr"; bytes: Uint8Array }
  | { type: "host.exited"; terminal: WindowsHostTerminal };

export interface WindowsHostOutcome {
  hostExitCode: number | null;
  pid: number | null;
  childExitCode: number | null;
  termination: WindowsHostTermination | null;
  stdout: Uint8Array;
  stderr: Uint8Array;
  cleanupVerified: boolean;
  cancellationReason: CancellationReason;
  error: RunnerFailure | null;
}

export interface WindowsHostIdentity {
  schema: typeof WINDOWS_HOST_IDENTITY_SCHEMA;
  component: "openprose-windows-process-host";
  componentVersion: string;
  target: { os: string; architecture: string; nativeWindowsImplementation: boolean };
  protocol: {
    request: typeof WINDOWS_HOST_REQUEST_SCHEMA;
    control: typeof WINDOWS_HOST_CONTROL_SCHEMA;
    event: typeof WINDOWS_HOST_EVENT_SCHEMA;
    error: typeof WINDOWS_HOST_ERROR_SCHEMA;
    maxRequestBytes: typeof MAX_REQUEST_BYTES;
    maxControlBytes: typeof MAX_CONTROL_BYTES;
    limits: {
      maxDecodedStdinBytes: typeof MAX_CHILD_STDIN_BYTES;
      maxStdinBase64Characters: typeof MAX_STDIN_BASE64_CHARS;
      maxArgvItems: typeof MAX_ARGUMENTS;
      maxArgumentCharacters: typeof MAX_ARGUMENT_CHARS;
      maxEnvironmentItems: typeof MAX_ENVIRONMENT_ENTRIES;
      maxEnvironmentNameCharacters: typeof MAX_ENVIRONMENT_NAME_CHARS;
      maxEnvironmentValueCharacters: typeof MAX_ENVIRONMENT_VALUE_CHARS;
      maxPathCharacters: typeof MAX_PATH_CHARS;
      maxEnvironmentBlockBytes: typeof MAX_ENVIRONMENT_BLOCK_BYTES;
      maxRenderedCommandLineUtf16Units: 32_766;
    };
  };
  claims: {
    providerCallsMade: false;
    nativeWindowsRuntimeEvidence: false;
    strictWindowsContainmentReady: false;
  };
}

export interface WindowsHostApplicability {
  applicable: boolean;
  reason: "applicable" | "not-native-windows" | "native-evidence-unavailable" | "strict-containment-unavailable";
}

/** Builds only the language-opaque process-host envelope; payload bytes are never interpreted. */
export function buildWindowsHostRequest(input: WindowsHostRequestInput): WindowsHostRequest {
  validateIdentifier(input.requestId, "requestId");
  validateExecutablePath(input.executable, "executable");
  validateExecutablePath(input.wrapperExecutable, "wrapperExecutable");
  if (input.executable.toLocaleLowerCase("en-US") === input.wrapperExecutable.toLocaleLowerCase("en-US")) {
    malformed("The child executable equals the wrapper executable.", "executable");
  }
  validateAbsoluteWindowsPath(input.cwd, "cwd");
  if (input.argv.length > MAX_ARGUMENTS) malformed("The argv item limit was exceeded.", "argv");
  const argv = input.argv.map((argument) => {
    validateText(argument, "argv");
    if (unicodeScalarLength(argument) > MAX_ARGUMENT_CHARS) malformed("One argument exceeds the closed character limit.", "argv");
    return argument;
  });
  const environmentNames = Object.keys(input.environment).sort(compareOrdinal);
  if (environmentNames.length > MAX_ENVIRONMENT_ENTRIES) {
    malformed("The environment entry limit was exceeded.", "environment");
  }
  const folded = new Set<string>();
  const environment = environmentNames.map((name) => {
    if (name.length > MAX_ENVIRONMENT_NAME_CHARS || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
      malformed("An environment name is invalid.", "environment.name");
    }
    const normalized = name.toLocaleUpperCase("en-US");
    if (folded.has(normalized)) malformed("Environment names collide case-insensitively.", "environment.name");
    folded.add(normalized);
    const value = input.environment[name];
    if (value === undefined) malformed("An environment value is missing.", "environment.value");
    validateText(value, "environment.value");
    if (unicodeScalarLength(value) > MAX_ENVIRONMENT_VALUE_CHARS) {
      malformed("An environment value exceeds the closed character limit.", "environment.value");
    }
    return { name, value };
  });
  validateRequiredMetadata(environment);
  validateEnvironmentBlockSize(environment);
  validateCommandLineSize(input.executable, argv);
  validateHostLimits(input.limits);
  boundedInteger(input.graceMs, 0, MAX_CANCELLATION_MS, "cancellation.graceMs");
  boundedInteger(input.hardKillAfterMs, 1, MAX_CANCELLATION_MS, "cancellation.hardKillAfterMs");
  boundedInteger(input.runTimeoutMs, 1, MAX_TIMEOUT_MS, "runTimeoutMs");
  if ((input.stdinBytes?.byteLength ?? 0) > MAX_CHILD_STDIN_BYTES) {
    malformed("Decoded child stdin exceeds the host limit.", "stdinBytes");
  }

  const request: WindowsHostRequest = {
    schema: WINDOWS_HOST_REQUEST_SCHEMA,
    requestId: input.requestId,
    executable: input.executable,
    wrapperExecutable: input.wrapperExecutable,
    argv,
    cwd: input.cwd,
    environment,
    stdinBase64: Buffer.from(input.stdinBytes ?? new Uint8Array()).toString("base64"),
    limits: {
      maxStdoutBytes: input.limits.maxAggregateStdoutBytes,
      maxStderrBytes: input.limits.maxAggregateStderrBytes,
      maxQueuedChunks: input.limits.maxQueuedRecords,
    },
    cancellation: {
      graceful: input.graceful,
      graceMs: input.graceMs,
      hardKillAfterMs: input.hardKillAfterMs,
    },
    runTimeoutMs: input.runTimeoutMs,
  };
  // Validate the actual wire size now, rather than trusting independent component caps.
  encodeWindowsHostRequest(request);
  return request;
}

/** Version probes use the same explicit .exe and Job-contained host boundary as normal runs. */
export function buildWindowsHostVersionProbeRequest(input: WindowsHostVersionProbeInput): WindowsHostRequest {
  return buildWindowsHostRequest({
    requestId: input.requestId,
    executable: input.executable,
    wrapperExecutable: input.wrapperExecutable,
    argv: ["--version"],
    cwd: input.cwd,
    environment: input.environment,
    stdinBytes: new Uint8Array(),
    limits: {
      maxRecordBytes: MAX_REQUEST_BYTES,
      maxAggregateStdoutBytes: MAX_DIAGNOSTIC_BYTES,
      maxAggregateStderrBytes: MAX_DIAGNOSTIC_BYTES,
      maxQueuedRecords: 32,
    },
    graceful: "none",
    graceMs: 0,
    hardKillAfterMs: Math.min(input.timeoutMs, MAX_CANCELLATION_MS),
    runTimeoutMs: input.timeoutMs,
  });
}

export function encodeWindowsHostRequest(request: WindowsHostRequest): string {
  validateRequestShape(request);
  const line = `${JSON.stringify(request)}\n`;
  if (Buffer.byteLength(line, "utf8") > MAX_REQUEST_BYTES) {
    malformed("The encoded request exceeds the process-host line limit.", "request");
  }
  return line;
}

/** Serializes at most one cancel record, even when multiple cancellation sources race. */
export class WindowsHostControlWriter {
  private readonly writeLine: (line: string) => void | Promise<void>;
  private pending: Promise<void> | null = null;

  constructor(writeLine: (line: string) => void | Promise<void>) {
    this.writeLine = writeLine;
  }

  get cancelRequested(): boolean {
    return this.pending !== null;
  }

  cancel(reason: string): Promise<void> {
    try {
      validateIdentifier(reason, "control.reason");
    } catch (caught) {
      return Promise.reject(caught);
    }
    if (this.pending === null) {
      const line = `${JSON.stringify({ type: "cancel", schema: WINDOWS_HOST_CONTROL_SCHEMA, reason })}\n`;
      if (Buffer.byteLength(line, "utf8") > MAX_CONTROL_BYTES) malformed("The cancel control exceeds its wire limit.");
      this.pending = Promise.resolve().then(() => this.writeLine(line));
    }
    return this.pending;
  }
}

/**
 * Independent closed-schema parser for stdout records from the native host.
 * It validates containment evidence but deliberately does not inspect child output.
 */
export class WindowsHostEventParser {
  private readonly requestId: string;
  private readonly limits: TransportLimits;
  private nextSequence = 0;
  private pid: number | null = null;
  private terminal: WindowsHostTerminal | null = null;
  private stdoutChunks: Uint8Array[] = [];
  private stderrChunks: Uint8Array[] = [];
  private stdoutBytes = 0;
  private stderrBytes = 0;
  private poisoned: RunnerFailure | null = null;
  private finished = false;

  constructor(requestId: string, limits: TransportLimits) {
    validateIdentifier(requestId, "requestId");
    validateHostLimits(limits);
    this.requestId = requestId;
    this.limits = limits;
  }

  accept(value: unknown): WindowsHostAcceptedEvent {
    if (this.poisoned !== null) throw this.poisoned;
    if (this.finished) throw failure("PROTOCOL_MALFORMED", { reason: "A host event arrived after client finalization." });
    try {
      return this.acceptValidated(value);
    } catch (caught) {
      this.poisoned = caught instanceof Error && "code" in caught
        ? caught as RunnerFailure
        : failure("PROTOCOL_MALFORMED", { reason: "The process-host event could not be validated." });
      throw this.poisoned;
    }
  }

  finish(hostExitCode: number | null, diagnosticBytes: Uint8Array): WindowsHostOutcome {
    if (this.finished) malformed("The process-host parser was finalized more than once.");
    this.finished = true;
    const stdout = concatenate(this.stdoutChunks, this.stdoutBytes);
    const stderr = concatenate(this.stderrChunks, this.stderrBytes);
    const base = {
      hostExitCode,
      pid: this.pid,
      childExitCode: this.terminal?.processExitCode ?? null,
      termination: this.terminal?.termination ?? null,
      stdout,
      stderr,
    };
    if (this.terminal === null) {
      if (this.pid !== null || this.poisoned !== null) {
        return {
          ...base,
          cleanupVerified: false,
          cancellationReason: null,
          error: failure("PROCESS_CLEANUP_FAILED", {
            hostExitCode,
            pid: this.pid,
            priorErrorCode: this.poisoned?.code ?? "PROTOCOL_TRUNCATED",
            reason: "No authoritative host.exited Job accounting was accepted after host output began.",
          }),
        };
      }
      const error = this.pid === null
        ? bootstrapOrDisconnectFailure(hostExitCode, diagnosticBytes)
        : failure("PROTOCOL_TRUNCATED", { reason: "The native host disconnected before host.exited.", hostExitCode });
      return { ...base, cleanupVerified: false, cancellationReason: null, error };
    }
    const terminal = this.terminal;
    const cleanupVerified = hostExitCode === 0
      && terminal.cleanupVerified
      && terminal.activeProcessesAfterCleanup === 0;
    if (!cleanupVerified) {
      return {
        ...base,
        cleanupVerified: false,
        cancellationReason: null,
        error: failure("PROCESS_CLEANUP_FAILED", {
          hostExitCode,
          pid: terminal.pid,
          activeProcessesBeforeCleanup: terminal.activeProcessesBeforeCleanup,
          activeProcessesAfterCleanup: terminal.activeProcessesAfterCleanup,
        }),
      };
    }
    if (this.poisoned !== null) {
      return { ...base, cleanupVerified: true, cancellationReason: null, error: this.poisoned };
    }
    if (diagnosticBytes.byteLength !== 0) {
      return {
        ...base,
        cleanupVerified: true,
        cancellationReason: null,
        error: failure("HARNESS_FAILED", { reason: "The native host emitted unexpected bootstrap diagnostics after starting." }),
      };
    }
    const mapped = mapTerminal(terminal);
    return { ...base, cleanupVerified: true, ...mapped };
  }

  private acceptValidated(value: unknown): WindowsHostAcceptedEvent {
    const record = asRecord(value);
    if (!hasExactKeys(record, ["schema", "requestId", "sequence", "type", "payload"])) {
      malformed("A host event carried unknown or missing fields.");
    }
    if (record.schema !== WINDOWS_HOST_EVENT_SCHEMA) malformed("The host event schema is unsupported.");
    if (record.requestId !== this.requestId) malformed("The host event request identity does not match.");
    if (!nonNegativeInteger(record.sequence) || record.sequence !== this.nextSequence) {
      malformed("The host event sequence is not contiguous from zero.");
    }
    if (this.terminal !== null) malformed("A host event arrived after host.exited.");
    const payload = asRecord(record.payload);
    let accepted: WindowsHostAcceptedEvent;
    if (record.type === "host.started") accepted = this.acceptStarted(payload);
    else if (record.type === "child.stdout" || record.type === "child.stderr") {
      accepted = this.acceptStream(record.type, payload);
    } else if (record.type === "host.exited") accepted = this.acceptExited(payload);
    else malformed("The host event type is unsupported.");
    this.nextSequence += 1;
    return accepted;
  }

  private acceptStarted(payload: Record<string, unknown>): WindowsHostAcceptedEvent {
    if (this.pid !== null) malformed("host.started was emitted more than once.");
    if (!hasExactKeys(payload, [
      "pid", "suspendedCreate", "strictHandleList", "inheritedHandleCount",
      "jobAssignedBeforeResume", "killOnJobClose", "newProcessGroup", "outerPty", "shell",
    ])) malformed("host.started carried unknown or missing fields.");
    if (
      !positiveInteger(payload.pid)
      || payload.suspendedCreate !== true
      || payload.strictHandleList !== true
      || payload.inheritedHandleCount !== 3
      || payload.jobAssignedBeforeResume !== true
      || payload.killOnJobClose !== true
      || payload.newProcessGroup !== true
      || payload.outerPty !== false
      || payload.shell !== false
    ) malformed("host.started did not prove the required containment policy.");
    this.assertRecordBound("host.started", payload);
    this.pid = payload.pid;
    return { type: "host.started", pid: payload.pid };
  }

  private acceptStream(
    type: "child.stdout" | "child.stderr",
    payload: Record<string, unknown>,
  ): WindowsHostAcceptedEvent {
    if (this.pid === null) malformed("A child stream event arrived before host.started.");
    if (!hasExactKeys(payload, ["encoding", "data", "stream"])) {
      malformed("A child stream event carried unknown or missing fields.");
    }
    const streamName = type === "child.stdout" ? "stdout" : "stderr";
    if (payload.encoding !== "base64" || payload.stream !== streamName || typeof payload.data !== "string") {
      malformed("A child stream event has an invalid encoding or stream identity.");
    }
    if (payload.data.length > this.limits.maxRecordBytes) malformed("A child stream record exceeds its wire bound.");
    this.assertRecordBound(type, payload);
    const remaining = type === "child.stdout"
      ? this.limits.maxAggregateStdoutBytes - this.stdoutBytes
      : this.limits.maxAggregateStderrBytes - this.stderrBytes;
    const bytes = decodeCanonicalBase64(payload.data, remaining);
    if (type === "child.stdout") {
      const next = this.stdoutBytes + bytes.byteLength;
      if (!Number.isSafeInteger(next) || next > this.limits.maxAggregateStdoutBytes) {
        malformed("Decoded child stdout exceeded its declared bound.");
      }
      this.stdoutBytes = next;
      this.stdoutChunks.push(bytes);
    } else {
      const next = this.stderrBytes + bytes.byteLength;
      if (!Number.isSafeInteger(next) || next > this.limits.maxAggregateStderrBytes) {
        malformed("Decoded child stderr exceeded its declared bound.");
      }
      this.stderrBytes = next;
      this.stderrChunks.push(bytes);
    }
    return { type, bytes: bytes.slice() };
  }

  private acceptExited(payload: Record<string, unknown>): WindowsHostAcceptedEvent {
    if (this.pid === null) malformed("host.exited arrived before host.started.");
    if (!hasExactKeys(payload, [
      "pid", "processExitCode", "termination", "gracefulControlAttempted",
      "gracefulControlDelivered", "hardKillUsed", "activeProcessesBeforeCleanup",
      "activeProcessesAfterCleanup", "cleanupVerified", "stdoutBytes", "stderrBytes",
    ])) malformed("host.exited carried unknown or missing fields.");
    if (payload.pid !== this.pid) malformed("host.exited changed the owned process identity.");
    if (!uint32(payload.processExitCode)) malformed("host.exited processExitCode is invalid.");
    if (!isTermination(payload.termination)) malformed("host.exited termination is invalid.");
    for (const name of ["gracefulControlAttempted", "gracefulControlDelivered", "hardKillUsed", "cleanupVerified"] as const) {
      if (typeof payload[name] !== "boolean") malformed(`host.exited ${name} is invalid.`);
    }
    if (payload.gracefulControlDelivered && !payload.gracefulControlAttempted) {
      malformed("Graceful control cannot be delivered without being attempted.");
    }
    for (const name of ["activeProcessesBeforeCleanup", "activeProcessesAfterCleanup", "stdoutBytes", "stderrBytes"] as const) {
      if (!nonNegativeInteger(payload[name])) malformed(`host.exited ${name} is invalid.`);
    }
    const activeProcessesAfterCleanup = payload.activeProcessesAfterCleanup as number;
    const stdoutBytes = payload.stdoutBytes as number;
    const stderrBytes = payload.stderrBytes as number;
    const activeProcessesBeforeCleanup = payload.activeProcessesBeforeCleanup as number;
    if (activeProcessesAfterCleanup > activeProcessesBeforeCleanup) {
      malformed("Job accounting grew during final cleanup.");
    }
    if (payload.cleanupVerified !== (activeProcessesAfterCleanup === 0)) {
      malformed("cleanupVerified does not match authoritative Job accounting.");
    }
    if (activeProcessesBeforeCleanup > 0 && payload.hardKillUsed !== true) {
      malformed("Remaining Job members were not reported as hard-killed.");
    }
    if (stdoutBytes < this.stdoutBytes || stderrBytes < this.stderrBytes) {
      malformed("Host byte counters are smaller than emitted child stream bytes.");
    }
    if (payload.termination === "output-limit") {
      if (
        stdoutBytes <= this.limits.maxAggregateStdoutBytes
        && stderrBytes <= this.limits.maxAggregateStderrBytes
      ) malformed("An output-limit terminal did not identify an exceeded stream bound.");
    } else if (stdoutBytes !== this.stdoutBytes || stderrBytes !== this.stderrBytes) {
      malformed("Host byte counters do not match emitted child stream bytes.");
    }
    this.assertRecordBound("host.exited", payload);
    const terminal = payload as unknown as WindowsHostTerminal;
    this.terminal = terminal;
    return { type: "host.exited", terminal };
  }

  private assertRecordBound(type: string, payload: Record<string, unknown>): void {
    const canonicalRecord = {
      schema: WINDOWS_HOST_EVENT_SCHEMA,
      requestId: this.requestId,
      sequence: this.nextSequence,
      type,
      payload,
    };
    if (Buffer.byteLength(JSON.stringify(canonicalRecord), "utf8") > this.limits.maxRecordBytes) {
      malformed("A process-host event exceeds the declared record byte bound.");
    }
  }
}

/** Interprets only the transport result of a contained `--version` request. */
export function acceptWindowsVersionProbe(
  outcome: WindowsHostOutcome,
  expected: RegExp,
  versionStream: "stdout" | "stderr" = "stdout",
): string {
  if (outcome.error !== null) throw outcome.error;
  if (outcome.childExitCode !== 0) {
    throw failure("HARNESS_INCOMPATIBLE", { reason: "The contained version probe exited unsuccessfully." });
  }
  let version: string;
  try {
    version = strictTextDecoder.decode(versionStream === "stderr" ? outcome.stderr : outcome.stdout).trim();
  } catch {
    throw failure("HARNESS_INCOMPATIBLE", { reason: "The contained version probe did not emit UTF-8." });
  }
  // Avoid carrying arbitrary probe output into diagnostics when it fails validation.
  expected.lastIndex = 0;
  if (version.includes("\0") || !expected.test(version)) {
    throw failure("HARNESS_INCOMPATIBLE", { reason: "The contained version probe output did not match the adapter requirement." });
  }
  return version;
}

/** Parses the host's direct `--identity-json` probe without trusting self-reported readiness. */
export function parseWindowsHostIdentity(value: unknown): WindowsHostIdentity {
  const record = asRecord(value);
  if (!hasExactKeys(record, ["schema", "component", "componentVersion", "target", "protocol", "claims"])) {
    malformed("The Windows host identity carried unknown or missing fields.");
  }
  if (
    record.schema !== WINDOWS_HOST_IDENTITY_SCHEMA
    || record.component !== "openprose-windows-process-host"
    || typeof record.componentVersion !== "string"
    || !/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$/.test(record.componentVersion)
  ) malformed("The Windows host component identity is incompatible.");
  const target = asRecord(record.target);
  if (!hasExactKeys(target, ["os", "architecture", "nativeWindowsImplementation"])
    || typeof target.os !== "string" || target.os.length < 1 || target.os.length > 64
    || typeof target.architecture !== "string" || target.architecture.length < 1 || target.architecture.length > 64
    || typeof target.nativeWindowsImplementation !== "boolean") {
    malformed("The Windows host target identity is invalid.");
  }
  const protocol = asRecord(record.protocol);
  if (!hasExactKeys(protocol, ["request", "control", "event", "error", "maxRequestBytes", "maxControlBytes", "limits"])
    || protocol.request !== WINDOWS_HOST_REQUEST_SCHEMA
    || protocol.control !== WINDOWS_HOST_CONTROL_SCHEMA
    || protocol.event !== WINDOWS_HOST_EVENT_SCHEMA
    || protocol.error !== WINDOWS_HOST_ERROR_SCHEMA
    || protocol.maxRequestBytes !== MAX_REQUEST_BYTES
    || protocol.maxControlBytes !== MAX_CONTROL_BYTES) {
    malformed("The Windows host protocol identity is incompatible.");
  }
  const identityLimits = asRecord(protocol.limits);
  if (!hasExactKeys(identityLimits, [
    "maxDecodedStdinBytes", "maxStdinBase64Characters", "maxArgvItems", "maxArgumentCharacters",
    "maxEnvironmentItems", "maxEnvironmentNameCharacters", "maxEnvironmentValueCharacters",
    "maxPathCharacters", "maxEnvironmentBlockBytes", "maxRenderedCommandLineUtf16Units",
  ])
    || identityLimits.maxDecodedStdinBytes !== MAX_CHILD_STDIN_BYTES
    || identityLimits.maxStdinBase64Characters !== MAX_STDIN_BASE64_CHARS
    || identityLimits.maxArgvItems !== MAX_ARGUMENTS
    || identityLimits.maxArgumentCharacters !== MAX_ARGUMENT_CHARS
    || identityLimits.maxEnvironmentItems !== MAX_ENVIRONMENT_ENTRIES
    || identityLimits.maxEnvironmentNameCharacters !== MAX_ENVIRONMENT_NAME_CHARS
    || identityLimits.maxEnvironmentValueCharacters !== MAX_ENVIRONMENT_VALUE_CHARS
    || identityLimits.maxPathCharacters !== MAX_PATH_CHARS
    || identityLimits.maxEnvironmentBlockBytes !== MAX_ENVIRONMENT_BLOCK_BYTES
    || identityLimits.maxRenderedCommandLineUtf16Units !== 32_766) {
    malformed("The Windows host protocol limit identity is incompatible.");
  }
  const claims = asRecord(record.claims);
  if (!hasExactKeys(claims, ["providerCallsMade", "nativeWindowsRuntimeEvidence", "strictWindowsContainmentReady"])
    || claims.providerCallsMade !== false
    || claims.nativeWindowsRuntimeEvidence !== false
    || claims.strictWindowsContainmentReady !== false) {
    malformed("The Windows host evidence claims are invalid.");
  }
  return record as unknown as WindowsHostIdentity;
}

/** A compatible identity is necessary but explicit native evidence remains the admission authority. */
export function windowsHostApplicability(identity: WindowsHostIdentity): WindowsHostApplicability {
  if (identity.target.os !== "windows" || !identity.target.nativeWindowsImplementation) {
    return { applicable: false, reason: "not-native-windows" };
  }
  if (!identity.claims.nativeWindowsRuntimeEvidence) {
    return { applicable: false, reason: "native-evidence-unavailable" };
  }
  if (!identity.claims.strictWindowsContainmentReady) {
    return { applicable: false, reason: "strict-containment-unavailable" };
  }
  return { applicable: true, reason: "applicable" };
}

function validateRequestShape(request: WindowsHostRequest): void {
  const record = request as unknown as Record<string, unknown>;
  if (!hasExactKeys(record, [
    "schema", "requestId", "executable", "wrapperExecutable", "argv", "cwd", "environment",
    "stdinBase64", "limits", "cancellation", "runTimeoutMs",
  ])) malformed("The process-host request carried unknown or missing fields.", "request");
  if (request.schema !== WINDOWS_HOST_REQUEST_SCHEMA) malformed("The request schema is unsupported.", "schema");
  validateIdentifier(request.requestId, "requestId");
  validateExecutablePath(request.executable, "executable");
  validateExecutablePath(request.wrapperExecutable, "wrapperExecutable");
  if (request.executable.toLocaleLowerCase("en-US") === request.wrapperExecutable.toLocaleLowerCase("en-US")) {
    malformed("The child executable equals the wrapper executable.", "executable");
  }
  validateAbsoluteWindowsPath(request.cwd, "cwd");
  if (!Array.isArray(request.argv) || request.argv.length > MAX_ARGUMENTS) malformed("argv is invalid.", "argv");
  for (const argument of request.argv) {
    validateText(argument, "argv");
    if (unicodeScalarLength(argument) > MAX_ARGUMENT_CHARS) malformed("One argument exceeds the closed character limit.", "argv");
  }
  if (!Array.isArray(request.environment) || request.environment.length > MAX_ENVIRONMENT_ENTRIES) {
    malformed("environment is invalid.", "environment");
  }
  let priorName: string | null = null;
  const foldedNames = new Set<string>();
  for (const entry of request.environment) {
    if (!hasExactKeys(entry as unknown as Record<string, unknown>, ["name", "value"])) {
      malformed("An environment entry carried unknown or missing fields.", "environment");
    }
    if (entry.name.length > MAX_ENVIRONMENT_NAME_CHARS || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(entry.name)) {
      malformed("An environment name is invalid.", "environment.name");
    }
    validateText(entry.value, "environment.value");
    if (unicodeScalarLength(entry.value) > MAX_ENVIRONMENT_VALUE_CHARS) {
      malformed("An environment value exceeds the closed character limit.", "environment.value");
    }
    const foldedName = entry.name.toLocaleUpperCase("en-US");
    if (foldedNames.has(foldedName)) malformed("Environment names collide case-insensitively.", "environment");
    foldedNames.add(foldedName);
    if (priorName !== null && compareOrdinal(priorName, entry.name) >= 0) {
      malformed("Environment entries are not uniquely ordered.", "environment");
    }
    priorName = entry.name;
  }
  validateRequiredMetadata(request.environment);
  validateEnvironmentBlockSize(request.environment);
  validateCommandLineSize(request.executable, request.argv);
  if (request.stdinBase64.length > MAX_STDIN_BASE64_CHARS) {
    malformed("Encoded child stdin exceeds the closed request limit.", "stdinBase64");
  }
  decodeCanonicalBase64(request.stdinBase64, MAX_CHILD_STDIN_BYTES);
  const limitsRecord = request.limits as unknown as Record<string, unknown>;
  if (!hasExactKeys(limitsRecord, ["maxStdoutBytes", "maxStderrBytes", "maxQueuedChunks"])) {
    malformed("Request limits carried unknown or missing fields.", "limits");
  }
  validateHostLimits({
    maxRecordBytes: MAX_REQUEST_BYTES,
    maxAggregateStdoutBytes: request.limits.maxStdoutBytes,
    maxAggregateStderrBytes: request.limits.maxStderrBytes,
    maxQueuedRecords: request.limits.maxQueuedChunks,
  });
  const cancellationRecord = request.cancellation as unknown as Record<string, unknown>;
  if (!hasExactKeys(cancellationRecord, ["graceful", "graceMs", "hardKillAfterMs"])) {
    malformed("Cancellation policy carried unknown or missing fields.", "cancellation");
  }
  if (request.cancellation.graceful !== "ctrl-break" && request.cancellation.graceful !== "none") {
    malformed("Cancellation control is invalid.", "cancellation.graceful");
  }
  boundedInteger(request.cancellation.graceMs, 0, MAX_CANCELLATION_MS, "cancellation.graceMs");
  boundedInteger(request.cancellation.hardKillAfterMs, 1, MAX_CANCELLATION_MS, "cancellation.hardKillAfterMs");
  boundedInteger(request.runTimeoutMs, 1, MAX_TIMEOUT_MS, "runTimeoutMs");
}

function validateHostLimits(limits: TransportLimits): void {
  boundedInteger(limits.maxRecordBytes, 1, MAX_STREAM_BYTES, "limits.maxRecordBytes");
  boundedInteger(limits.maxAggregateStdoutBytes, 1, MAX_STREAM_BYTES, "limits.maxStdoutBytes");
  boundedInteger(limits.maxAggregateStderrBytes, 1, MAX_STREAM_BYTES, "limits.maxStderrBytes");
  boundedInteger(limits.maxQueuedRecords, 1, MAX_QUEUED_CHUNKS, "limits.maxQueuedChunks");
}

function validateRequiredMetadata(environment: readonly { name: string; value: string }[]): void {
  const values = new Map(environment.map((entry) => [entry.name.toLocaleUpperCase("en-US"), entry.value]));
  for (const required of ["OPENPROSE_INVOCATION_ID", "OPENPROSE_RECURSION_TOKEN", "OPENPROSE_RUN_NONCE"]) {
    if ((values.get(required) ?? "").length === 0) {
      malformed("Required runner identity metadata is absent or empty.", "environment");
    }
  }
}

function validateEnvironmentBlockSize(environment: readonly { name: string; value: string }[]): void {
  let utf16Units = 1;
  for (const entry of environment) {
    utf16Units += entry.name.length + 1 + entry.value.length + 1;
  }
  if (utf16Units * 2 > MAX_ENVIRONMENT_BLOCK_BYTES) {
    malformed("The Unicode environment block exceeds the host limit.", "environment");
  }
}

function validateCommandLineSize(executable: string, argv: readonly string[]): void {
  let utf16Units = quotedWindowsArgumentUnits(executable);
  for (const argument of argv) utf16Units += 1 + quotedWindowsArgumentUnits(argument);
  if (utf16Units >= 32_767) {
    malformed("The rendered Windows command line exceeds 32766 UTF-16 code units.", "argv");
  }
}

function quotedWindowsArgumentUnits(argument: string): number {
  if (argument.length > 0 && !/[\p{White_Space}"]/u.test(argument)) return argument.length;
  let units = 2;
  let backslashes = 0;
  for (const character of argument) {
    if (character === "\\") {
      backslashes += 1;
    } else if (character === '"') {
      units += backslashes * 2 + 2;
      backslashes = 0;
    } else {
      units += backslashes + character.length;
      backslashes = 0;
    }
  }
  return units + backslashes * 2;
}

function validateIdentifier(value: string, field: string): void {
  if (typeof value !== "string" || textEncoder.encode(value).byteLength > MAX_IDENTIFIER_BYTES || !/^[A-Za-z0-9_.:-]+$/.test(value)) {
    malformed("An identifier is empty, oversized, or contains a forbidden byte.", field);
  }
}

function validateExecutablePath(value: string, field: string): void {
  validateAbsoluteWindowsPath(value, field);
  if (!/\.exe$/i.test(value)) malformed("Only an explicit .exe target is accepted.", field);
}

function validateAbsoluteWindowsPath(value: string, field: string): void {
  validateText(value, field);
  if (unicodeScalarLength(value) > MAX_PATH_CHARS) malformed("A Windows path exceeds the closed character limit.", field);
  const drive = /^[A-Za-z]:[\\/]/.test(value);
  const unc = /^\\\\[^\\/]+[\\/][^\\/]+/.test(value);
  if (!drive && !unc) malformed("A Windows path is not absolute.", field);
}

function validateText(value: string, field: string): void {
  if (typeof value !== "string" || value.includes("\0") || !isWellFormedUtf16(value)) {
    malformed("Text contains NUL or an unpaired UTF-16 surrogate.", field);
  }
}

function isWellFormedUtf16(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function unicodeScalarLength(value: string): number {
  let length = 0;
  for (const _character of value) length += 1;
  return length;
}

function decodeCanonicalBase64(value: string, maximumDecodedBytes = MAX_STREAM_BYTES): Uint8Array {
  if (typeof value !== "string") malformed("Base64 is not a string.");
  const maximumEncodedLength = Math.ceil(maximumDecodedBytes / 3) * 4;
  if (value.length > maximumEncodedLength) {
    malformed("Decoded base64 would exceed its declared byte bound.");
  }
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    malformed("Base64 is not canonical padded RFC 4648 encoding.");
  }
  const bytes = Uint8Array.from(Buffer.from(value, "base64"));
  if (Buffer.from(bytes).toString("base64") !== value) {
    malformed("Base64 has noncanonical padding bits.");
  }
  return bytes;
}

function bootstrapOrDisconnectFailure(hostExitCode: number | null, bytes: Uint8Array): RunnerFailure {
  if (bytes.byteLength === 0) {
    return failure("PROTOCOL_TRUNCATED", { reason: "The native host disconnected before host.started.", hostExitCode });
  }
  if (bytes.byteLength > MAX_DIAGNOSTIC_BYTES) {
    return failure("HARNESS_FAILED", { reason: "The native host diagnostic exceeded its bounded channel." });
  }
  let text: string;
  try {
    text = strictTextDecoder.decode(bytes);
  } catch {
    return failure("PROTOCOL_MALFORMED", { reason: "The native host diagnostic was not UTF-8." });
  }
  if (!text.endsWith("\n") || text.slice(0, -1).includes("\n") || text.includes("\r")) {
    return failure("PROTOCOL_MALFORMED", { reason: "The native host diagnostic was not one LF-terminated record." });
  }
  let record: Record<string, unknown>;
  try {
    record = asRecord(JSON.parse(text.slice(0, -1)));
  } catch {
    return failure("PROTOCOL_MALFORMED", { reason: "The native host diagnostic was not a JSON object." });
  }
  if (!hasExactKeys(record, ["schema", "code", "operation", "win32"])
    || record.schema !== WINDOWS_HOST_ERROR_SCHEMA
    || typeof record.code !== "string"
    || record.code.length < 1
    || record.code.length > 128
    || typeof record.operation !== "string"
    || record.operation.length < 1
    || record.operation.length > 256
    || !(record.win32 === null || uint32(record.win32))) {
    return failure("PROTOCOL_MALFORMED", { reason: "The native host diagnostic violated its closed schema." });
  }
  const details = { hostCode: record.code, hostExitCode, win32: record.win32 };
  if (record.code === "RECURSIVE_INVOCATION") return failure("RECURSIVE_INVOCATION", details);
  if (record.code === "PLATFORM_UNSUPPORTED") return failure("TRANSPORT_UNSUPPORTED", details);
  if ([
    "JOB_ASSIGNMENT_FAILED", "RESUME_FAILED", "JOB_TERMINATION_FAILED", "PROCESS_WAIT_FAILED",
    "PROCESS_STATUS_FAILED", "JOB_QUERY_FAILED",
  ].includes(record.code)) return failure("PROCESS_CLEANUP_FAILED", details);
  if (record.code === "PATH_INVALID" && record.operation === "canonicalize cwd") {
    return failure("CONFIG_INVALID", details);
  }
  if (record.code === "CREATE_PROCESS_FAILED" || record.code === "PATH_INVALID") {
    return failure("HARNESS_UNAVAILABLE", details);
  }
  if ([
    "PROTOCOL_MALFORMED", "PROTOCOL_VERSION_UNSUPPORTED", "REQUEST_LIMIT_EXCEEDED",
    "SHELL_TARGET_REJECTED", "ENVIRONMENT_INVALID", "RECURSION_METADATA_MISSING",
  ].includes(record.code)) return failure("PROTOCOL_MALFORMED", details);
  return failure("HARNESS_FAILED", details);
}

function mapTerminal(terminal: WindowsHostTerminal): Pick<WindowsHostOutcome, "error" | "cancellationReason"> {
  if (terminal.termination === "natural") {
    return terminal.processExitCode === 0
      ? { error: null, cancellationReason: null }
      : { error: failure("HARNESS_FAILED", { childExitCode: terminal.processExitCode }), cancellationReason: null };
  }
  if (terminal.termination === "cancelled") {
    return { error: failure("CANCELLED", { reason: "caller" }), cancellationReason: "caller" };
  }
  if (terminal.termination === "timeout") {
    return { error: failure("CANCELLED", { reason: "timeout" }), cancellationReason: "timeout" };
  }
  return {
    error: failure("HARNESS_FAILED", { termination: terminal.termination, childExitCode: terminal.processExitCode }),
    cancellationReason: null,
  };
}

function concatenate(chunks: readonly Uint8Array[], length: number): Uint8Array {
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) malformed("Expected a structured object.");
  return value as Record<string, unknown>;
}

function hasExactKeys(record: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(record).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function boundedInteger(value: unknown, minimum: number, maximum: number, field: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) {
    malformed("An integer is outside its allowed bound.", field);
  }
}

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function positiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function uint32(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 0xffff_ffff;
}

function isTermination(value: unknown): value is WindowsHostTermination {
  return typeof value === "string" && [
    "natural", "cancelled", "parent-disconnected", "timeout", "output-limit",
    "io-failure", "protocol-failure", "backpressure",
  ].includes(value);
}

function compareOrdinal(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function malformed(reason: string, field?: string): never {
  throw failure("PROTOCOL_MALFORMED", { reason, ...(field === undefined ? {} : { field }) });
}
