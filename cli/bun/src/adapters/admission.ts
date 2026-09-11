import transportLimitsJson from "../../../shared/capabilities/transport-limits.v1.json" with { type: "json" };
import { failure } from "../core/errors";
import { installedAdapterDefinition } from "./recipes";
import type { InstalledAdapterId } from "./types";

const encoder = new TextEncoder();
const limits = transportLimitsJson.argv;

export interface HostIdentity {
  platform?: NodeJS.Platform | undefined;
  arch?: NodeJS.Architecture | undefined;
  libc?: "gnu" | "musl" | undefined;
}

export function assertInstalledAdapterPlatform(adapterId: InstalledAdapterId, host: HostIdentity = {}): string {
  const platform = host.platform ?? process.platform;
  const arch = host.arch ?? process.arch;
  const architecture = arch === "arm64" || arch === "x64" ? arch : null;
  const report = process.report?.getReport() as { header?: { glibcVersionRuntime?: string } } | undefined;
  const libc = host.libc ?? (platform === "linux"
    ? report?.header?.glibcVersionRuntime === undefined ? "musl" : "gnu"
    : undefined);
  const candidates = architecture === null ? [] : platform === "linux"
    ? libc === "gnu"
      ? [`linux-${architecture}-gnu`, `linux-${architecture}-musl`, `linux-${architecture}`]
      : [`linux-${architecture}-musl`, `linux-${architecture}`]
    : platform === "darwin" ? [`darwin-${architecture}`]
      : platform === "win32" ? [`win32-${architecture}`]
        : [];
  const supported = installedAdapterDefinition(adapterId).recipe.support.platforms;
  const selected = candidates.find((candidate) => supported.includes(candidate));
  if (selected === undefined) {
    throw failure("HARNESS_INCOMPATIBLE", {
      adapterId,
      hostPlatform: platform,
      hostArchitecture: arch,
      supportedPlatforms: supported,
      fallbackAttempted: false,
    });
  }
  return selected;
}

export function assertInstalledAdapterArgv(adapterId: InstalledAdapterId, argv: readonly string[], host: HostIdentity = {}): void {
  if (argv.length > limits.maxItems) {
    throw failure("CONFIG_INVALID", { adapterId, reason: "Installed-adapter argv contains too many arguments.", argumentCount: argv.length, maximumArguments: limits.maxItems });
  }
  if ((host.platform ?? process.platform) === "win32") {
    let total = 0;
    for (const argument of argv) {
      const units = argument.length;
      if (units > limits.windows.maxArgumentUtf16CodeUnits) {
        throw failure("CONFIG_INVALID", { adapterId, reason: "Installed-adapter argument exceeds the Windows UTF-16 limit.", argumentUtf16CodeUnits: units, maximumUtf16CodeUnits: limits.windows.maxArgumentUtf16CodeUnits });
      }
      total += units * 2 + 3;
    }
    if (total > limits.windows.maxTotalChargedUtf16CodeUnits) {
      throw failure("CONFIG_INVALID", { adapterId, reason: "Installed-adapter argv exceeds the conservative Windows command-line limit.", chargedUtf16CodeUnits: total, maximumChargedUtf16CodeUnits: limits.windows.maxTotalChargedUtf16CodeUnits });
    }
    return;
  }
  let total = 0;
  for (const argument of argv) {
    const bytes = encoder.encode(argument).byteLength;
    if (bytes > limits.posix.maxArgumentBytes) {
      throw failure("CONFIG_INVALID", { adapterId, reason: "Installed-adapter argument exceeds the POSIX byte limit.", argumentBytes: bytes, maximumBytes: limits.posix.maxArgumentBytes });
    }
    total += bytes + 1;
  }
  if (total > limits.posix.maxTotalBytes) {
    throw failure("CONFIG_INVALID", { adapterId, reason: "Installed-adapter argv exceeds the conservative POSIX byte limit.", totalBytes: total, maximumBytes: limits.posix.maxTotalBytes });
  }
}
