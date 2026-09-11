import type { EffectiveConfiguration, OutputMode, RunnerErrorShape } from "./types";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { RUNNER_BUILD_COMMIT, RUNNER_VERSION } from "./build";

function shellSingleQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

const NON_COPYABLE_RUNNER_GUIDANCE = "No copyable runner command is available because its path contains control characters; reinstall OpenProse in a path without control characters.";

/** Makes one dynamic value safe to place on one physical terminal line. */
export function humanSafeScalar(value: string): string {
  return humanSafe(value, false);
}

/** Preserves intentional LF-separated prose while escaping terminal controls and literal backslashes. */
export function humanSafeMultiline(value: string): string {
  return humanSafe(value, true);
}

function humanSafe(value: string, preserveLineFeeds: boolean): string {
  let rendered = "";
  for (const character of value) {
    const codePoint = character.codePointAt(0)!;
    if (character === "\\") rendered += "\\\\";
    else if (character === "\b") rendered += "\\b";
    else if (character === "\t") rendered += "\\t";
    else if (character === "\n" && preserveLineFeeds) rendered += "\n";
    else if (character === "\n") rendered += "\\n";
    else if (character === "\f") rendered += "\\f";
    else if (character === "\r") rendered += "\\r";
    else if (
      (codePoint >= 0 && codePoint <= 0x1f)
      || (codePoint >= 0x7f && codePoint <= 0x9f)
      || codePoint === 0x2028
      || codePoint === 0x2029
    ) {
      rendered += `\\u{${codePoint.toString(16).toUpperCase().padStart(4, "0")}}`;
    } else rendered += character;
  }
  return rendered;
}

function hasTerminalControl(value: string): boolean {
  return [...value].some((character) => {
    const codePoint = character.codePointAt(0)!;
    return (codePoint >= 0 && codePoint <= 0x1f)
      || (codePoint >= 0x7f && codePoint <= 0x9f)
      || codePoint === 0x2028
      || codePoint === 0x2029;
  });
}

function runnerPath(value: string): string {
  return hasTerminalControl(value) ? NON_COPYABLE_RUNNER_GUIDANCE : shellSingleQuote(value);
}

/** A copyable reference to the exact running executable; never consults PATH. */
export function humanRunnerExecutable(): string {
  return runnerPath(process.execPath);
}

let cachedDefaultRunnerInvocation: string | undefined;

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
  }
  const encoded = JSON.stringify(value);
  if (encoded === undefined) throw new Error("value is not canonical JSON");
  return encoded;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function samePath(left: string, right: string): boolean {
  return process.platform === "win32"
    ? resolve(left).toLocaleLowerCase("en-US") === resolve(right).toLocaleLowerCase("en-US")
    : resolve(left) === resolve(right);
}

function directRegularFile(rootInput: string, fileInput: string): string {
  const root = resolve(rootInput);
  const file = resolve(fileInput);
  const suffix = relative(root, file);
  if (suffix === "" || suffix.startsWith(`..${sep}`) || isAbsolute(suffix)) {
    throw new Error("file is outside the npm install root");
  }
  const paths = [root];
  let candidate = root;
  for (const component of suffix.split(sep)) {
    candidate = join(candidate, component);
    paths.push(candidate);
  }
  for (const [index, path] of paths.entries()) {
    const info = lstatSync(path);
    if (info.isSymbolicLink()) throw new Error("npm package ancestry contains a symlink");
    if (index === paths.length - 1 ? !info.isFile() : !info.isDirectory()) {
      throw new Error("npm package ancestry has an unexpected file type");
    }
    if (!samePath(realpathSync(path), path)) throw new Error("npm package ancestry contains an alias");
  }
  return file;
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/**
 * A packaged platform binary derives its sibling npm meta launcher from the
 * closed package layout and authenticates both package manifests, itself, and
 * the launcher before recommending that launcher. No environment value can
 * redirect this path.
 */
function authenticatedNpmMetaLauncher(executableInput: string): string | undefined {
  try {
    const executable = resolve(executableInput);
    const binRoot = dirname(executable);
    const platformRoot = dirname(binRoot);
    const platformBasename = basename(platformRoot);
    if (
      basename(binRoot) !== "bin"
      || !/^prose-cli-(?:darwin-(?:arm64|x64)|linux-(?:arm64|x64)-(?:gnu|musl)|win32-(?:arm64|x64))$/u.test(platformBasename)
      || !/^prose(?:\.exe)?$/iu.test(basename(executable))
    ) return undefined;
    const platformScopeRoot = dirname(platformRoot);
    const platformNodeModules = dirname(platformScopeRoot);
    if (basename(platformScopeRoot) !== "@openprose" || basename(platformNodeModules) !== "node_modules") {
      return undefined;
    }
    const nestedMetaRoot = dirname(platformNodeModules);
    const isNested = basename(nestedMetaRoot) === "prose-cli"
      && basename(dirname(nestedMetaRoot)) === "@openprose"
      && basename(dirname(dirname(nestedMetaRoot))) === "node_modules";
    const metaRoot = isNested ? nestedMetaRoot : join(platformScopeRoot, "prose-cli");
    const installRoot = isNested ? dirname(dirname(nestedMetaRoot)) : platformNodeModules;

    const exactExecutable = directRegularFile(installRoot, executable);
    const platformManifestPath = directRegularFile(installRoot, join(platformRoot, "package.json"));
    const metaManifestPath = directRegularFile(installRoot, join(metaRoot, "package.json"));
    const metaLauncher = directRegularFile(installRoot, join(metaRoot, "bin", "prose.js"));
    const platformManifest = record(JSON.parse(readFileSync(platformManifestPath, "utf8")));
    const metaManifest = record(JSON.parse(readFileSync(metaManifestPath, "utf8")));
    if (platformManifest === undefined || metaManifest === undefined) return undefined;

    const platformName = `@openprose/${platformBasename}`;
    const platformId = platformBasename.slice("prose-cli-".length);
    const platformCohort = record(platformManifest.openproseCohort);
    const metaCohort = record(metaManifest.openproseCohort);
    const optionalDependencies = record(metaManifest.optionalDependencies);
    const launcherIdentity = record(metaManifest.openproseLauncher);
    const admittedPlatforms = platformCohort?.admittedPlatforms;
    const exactOptionalDependencies = Array.isArray(admittedPlatforms)
      && admittedPlatforms.length > 0
      && admittedPlatforms.every((value) => typeof value === "string")
      && new Set(admittedPlatforms).size === admittedPlatforms.length
      ? Object.fromEntries(
          admittedPlatforms.map((platform) => [`@openprose/prose-cli-${platform}`, RUNNER_VERSION]),
        )
      : undefined;
    if (
      platformManifest.name !== platformName
      || platformManifest.version !== RUNNER_VERSION
      || platformManifest.openprosePlatform !== platformId
      || platformManifest.openproseBinary !== `bin/${basename(executable)}`
      || platformManifest.openproseSourceRevision !== RUNNER_BUILD_COMMIT
      || metaManifest.name !== "@openprose/prose-cli"
      || metaManifest.version !== RUNNER_VERSION
      || platformCohort === undefined
      || metaCohort === undefined
      || exactOptionalDependencies === undefined
      || canonicalJson(optionalDependencies) !== canonicalJson(exactOptionalDependencies)
      || optionalDependencies?.[platformName] !== RUNNER_VERSION
      || platformCohort.version !== RUNNER_VERSION
      || platformCohort.sourceRevision !== RUNNER_BUILD_COMMIT
      || canonicalJson(platformCohort) !== canonicalJson(metaCohort)
      || launcherIdentity?.path !== "bin/prose.js"
    ) return undefined;

    const executableBytes = readFileSync(exactExecutable);
    if (
      platformManifest.openproseBinaryByteLength !== executableBytes.byteLength
      || platformManifest.openproseBinarySha256 !== sha256(executableBytes)
    ) return undefined;
    const launcherBytes = readFileSync(metaLauncher);
    const launcherStat = lstatSync(metaLauncher);
    if (
      launcherIdentity.byteLength !== launcherBytes.byteLength
      || launcherIdentity.sha256 !== sha256(launcherBytes)
      || (process.platform !== "win32" && (launcherStat.mode & 0o111) === 0)
    ) return undefined;
    return metaLauncher;
  } catch {
    return undefined;
  }
}

function isBunVirtualEntrypoint(entrypoint: string): boolean {
  const portable = entrypoint.replaceAll("\\", "/");
  return /(?:^|\/)(?:\$bunfs|~bun)(?:\/|$)/iu.test(portable);
}

/**
 * A copyable invocation of this runner. A compiled Bun executable owns its
 * virtual `/$bunfs/` entrypoint; a source run must retain the real script path
 * after the exact Bun runtime.
 */
export function humanRunnerInvocation(
  executable: string = process.execPath,
  entrypoint?: string,
): string {
  const defaultInvocation = arguments.length === 0;
  if (defaultInvocation && cachedDefaultRunnerInvocation !== undefined) {
    return cachedDefaultRunnerInvocation;
  }
  const effectiveEntrypoint = arguments.length >= 2 ? entrypoint : process.argv[1];
  const runtime = runnerPath(executable);
  if (runtime === NON_COPYABLE_RUNNER_GUIDANCE) return runtime;
  let invocation: string;
  if (
    effectiveEntrypoint !== undefined
    && isAbsolute(effectiveEntrypoint)
    && !isBunVirtualEntrypoint(effectiveEntrypoint)
  ) {
    if (hasTerminalControl(effectiveEntrypoint)) return NON_COPYABLE_RUNNER_GUIDANCE;
    invocation = `${runtime} ${shellSingleQuote(effectiveEntrypoint)}`;
  } else {
    const metaLauncher = authenticatedNpmMetaLauncher(executable);
    invocation = metaLauncher === undefined ? runtime : runnerPath(metaLauncher);
  }
  if (defaultInvocation) cachedDefaultRunnerInvocation = invocation;
  return invocation;
}

/** A copyable command against the exact running executable. */
export function humanRunnerCommand(arguments_: string): string {
  const invocation = humanRunnerInvocation();
  return invocation === NON_COPYABLE_RUNNER_GUIDANCE || hasTerminalControl(arguments_)
    ? NON_COPYABLE_RUNNER_GUIDANCE
    : `${invocation} ${arguments_}`;
}

export function jsonLine(value: unknown): string {
  return `${JSON.stringify(value)}\n`;
}

export function formatHumanError(error: RunnerErrorShape): string {
  const reasonValue = typeof error.details?.reason === "string" ? error.details.reason : undefined;
  const sourceValue = typeof error.details?.source === "string" ? error.details.source : undefined;
  const source = sourceValue !== undefined
    ? `\nSource: ${humanSafeScalar(sourceValue)}`
    : error.boundary === "configuration" ? "\nSource: unavailable" : "";
  const reason = reasonValue !== undefined
    ? `\nDetail: ${humanSafeScalar(reasonValue)}`
    : error.boundary === "configuration" ? "\nDetail: unavailable" : "";
  const version = humanVersionRepairDetails(error).map((line) => `\n${line}`).join("");
  const cleanupArgv = error.details?.cleanupArgv;
  const recovery = Array.isArray(cleanupArgv)
    && cleanupArgv.length === 4
    && cleanupArgv[0] === "cli"
    && cleanupArgv[1] === "cleanup"
    && cleanupArgv[2] === "prime"
    && typeof cleanupArgv[3] === "string"
    && validRecoveryHandle(cleanupArgv[3])
    ? `\nRecovery: preserve the original temporary-root environment, then run: ${humanRunnerCommand(`cli cleanup prime ${cleanupArgv[3]}`)}`
    : "";
  return `[${humanSafeScalar(error.boundary)}] ${humanSafeScalar(error.code)}: ${humanSafeScalar(error.message)}${source}${reason}${version}\nAction: ${humanAction(error)}${recovery}\n`;
}

export function humanAction(error: Pick<RunnerErrorShape, "action">): string {
  const invocation = humanRunnerInvocation();
  const action = humanSafeScalar(error.action);
  return invocation === NON_COPYABLE_RUNNER_GUIDANCE
    ? `${NON_COPYABLE_RUNNER_GUIDANCE} ${action}`
    : `Use the exact runner invocation ${invocation} for runner operations. ${action}`;
}

function validRecoveryHandle(handle: string): boolean {
  if (handle.length > 160 || !/^[\x20-\x7e]+$/u.test(handle)) return false;
  const parts = handle.split(".");
  if (parts.length !== 3 || parts[0] !== "prime-v1") return false;
  const directory = parts[1]!;
  const prefix = "openprose-prime-";
  if (!directory.startsWith(prefix)) return false;
  const suffix = directory.slice(prefix.length);
  return suffix.length >= 6
    && suffix.length <= 64
    && /^[A-Za-z0-9-]+$/u.test(suffix)
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(parts[2]!);
}

export function humanVersionRepairDetails(error: RunnerErrorShape): string[] {
  if (error.code !== "HARNESS_UNAVAILABLE" && error.code !== "HARNESS_INCOMPATIBLE") return [];
  const runtime = error.details?.runtimePrerequisite;
  const runtimeDetails = typeof runtime === "object" && runtime !== null
    && "runtime" in runtime && runtime.runtime === "bun"
    && "versionRange" in runtime && runtime.versionRange === ">=1.3.14"
    && "detectedVersion" in runtime
    && (runtime.detectedVersion === null || typeof runtime.detectedVersion === "string")
    && "repairCommand" in runtime
    && runtime.repairCommand === "npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9"
    ? [
        "Runtime prerequisite: bun",
        `Detected runtime version: ${humanSafeScalar(runtime.detectedVersion ?? "missing")}`,
        "Required runtime version: >=1.3.14",
        `Repair: ${humanSafeScalar(runtime.repairCommand)}`,
      ]
    : [];
  const detected = typeof error.details?.detectedVersion === "string"
    ? [`Detected version: ${humanSafeScalar(error.details.detectedVersion)}`]
    : [];
  const admitted = Array.isArray(error.details?.admittedVersions)
    && error.details.admittedVersions.every((value) => typeof value === "string")
    ? [`Admitted versions: ${error.details.admittedVersions.map(humanSafeScalar).join(", ")}`]
    : [];
  const repair = typeof error.details?.repairCommand === "string"
    ? [`Repair: ${humanSafeScalar(error.details.repairCommand)}`]
    : [];
  return [...runtimeDetails, ...detected, ...admitted, ...repair];
}

export function configurationExplanation(config: EffectiveConfiguration): Record<string, unknown> {
  const values = Object.fromEntries(
    (Object.keys(config.values) as Array<keyof typeof config.values>).map((key) => [key, {
      value: config.values[key],
      source: config.sources[key],
    }]),
  );
  return {
    schema: "openprose.configuration-explanation/1",
    cwd: { value: config.cwd, source: config.cwdSource },
    projectConfigPath: config.projectConfigPath,
    userConfigPath: config.userConfigPath,
    values,
  };
}

export function humanConfiguration(config: EffectiveConfiguration): string {
  const lines = [`cwd = ${humanSafeScalar(config.cwd)} (${humanSafeScalar(config.cwdSource.kind)}: ${humanSafeScalar(config.cwdSource.location)})`];
  for (const key of Object.keys(config.values) as Array<keyof typeof config.values>) {
    const raw = config.values[key];
    const value = raw === null ? "unset" : humanSafeScalar(String(raw));
    const source = config.sources[key] ?? {kind:"default",location:"built-in"};
    lines.push(`${key} = ${value} (${humanSafeScalar(source.kind)}: ${humanSafeScalar(source.location)})`);
  }
  return `${lines.join("\n")}\n`;
}

export function machineMode(mode: OutputMode): boolean {
  return mode === "json" || mode === "jsonl";
}
