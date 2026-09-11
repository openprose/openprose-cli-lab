import { failure } from "../core/errors";
import type { RunnerInvocation, VerifiedRuntimeImage } from "../core/types";
import { createPrivateTransportFiles } from "./files";
import { probeExecutableVersion, superviseStructuredProcess } from "./process";
import type { FakeProcessOptions, FakeProcessResult } from "./types";

export async function runFakeProcessTransport(
  invocation: RunnerInvocation,
  image: VerifiedRuntimeImage,
  ambient: Readonly<Record<string, string | undefined>>,
  timeoutMs: number,
  options: FakeProcessOptions,
): Promise<FakeProcessResult> {
  if (options.cancelSignal?.aborted === true) {
    throw failure("CANCELLED", { reason: "caller", phase: "before-spawn" });
  }
  const files = await createPrivateTransportFiles(image, invocation);
  const observationFile = options.observationFile ?? files.observationPath;
  const descendantPidFile = options.descendantPidFile ?? files.descendantPidPath;
  const scenario = options.scenario ?? "success";
  try {
    await probeExecutableVersion(
      options.executable,
      invocation.cwd,
      ambient,
      options.wrapperExecutable,
      5_000,
      /^openprose-fake-harness 1\.0\.0$/u,
      options.platform,
    );
    const argv = [
      "run",
      "--scenario", scenario,
      "--image-file", files.imagePath,
      "--task-file", files.taskPath,
      "--observation-file", observationFile,
    ];
    if (scenario === "delay" && options.delayMs !== undefined) argv.push("--delay-ms", String(options.delayMs));
    if (scenario === "descendant") argv.push("--descendant-pid-file", descendantPidFile);
    const result = await superviseStructuredProcess({
      executable: options.executable,
      argv,
      cwd: invocation.cwd,
      environment: ambient,
      invocationId: invocation.invocationId,
      recursionToken: invocation.recursionToken,
      runNonce: `nonce:${invocation.invocationId}`,
      ...(options.wrapperExecutable === undefined ? {} : { wrapperExecutable: options.wrapperExecutable }),
      startupTimeoutMs: options.startupTimeoutMs ?? Math.min(30_000, timeoutMs),
      runTimeoutMs: timeoutMs,
      graceMs: 250,
      hardKillAfterMs: 1_000,
      ...(options.cancelAfterMs === undefined ? {} : { cancelAfterMs: options.cancelAfterMs }),
      ...(options.cancelSignal === undefined ? {} : { cancelSignal: options.cancelSignal }),
      ...(options.platform === undefined ? {} : { platform: options.platform }),
    });
    return {
      ...result,
      deliveredImageSha256: image.aggregateSha256,
      observationFile,
    };
  } finally {
    // Caller-owned evidence paths live outside this private directory. The
    // prompt material itself is always removed after containment settles.
    await files.cleanup();
  }
}

export function parseDurationMs(value: string): number {
  const match = /^([1-9][0-9]*)(ms|s|m|h)$/u.exec(value);
  if (match === null) throw new Error(`invalid duration: ${value}`);
  const magnitude = Number(match[1]);
  const multiplier = match[2] === "ms" ? 1 : match[2] === "s" ? 1_000 : match[2] === "m" ? 60_000 : 3_600_000;
  return Math.min(Number.MAX_SAFE_INTEGER, magnitude * multiplier);
}
