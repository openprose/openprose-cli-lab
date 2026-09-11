/**
 * Selects one explicit Bun standalone target for the native release host.
 *
 * x64 artifacts deliberately use Bun's baseline runtime. The ordinary x64
 * target embeds a runtime that may require AVX2, which is too narrow for an
 * artifact advertised by architecture alone. ARM64 has no baseline variant.
 */
export type NativeCompileTarget =
  | "bun-darwin-arm64"
  | "bun-darwin-x64-baseline"
  | "bun-linux-arm64"
  | "bun-linux-x64-baseline"
  | "bun-windows-x64-baseline";

export function nativeCompileTarget(
  platform: string = process.platform,
  architecture: string = process.arch,
): NativeCompileTarget {
  const host = `${platform}-${architecture}`;
  const targets: Readonly<Record<string, NativeCompileTarget>> = {
    "darwin-arm64": "bun-darwin-arm64",
    "darwin-x64": "bun-darwin-x64-baseline",
    "linux-arm64": "bun-linux-arm64",
    "linux-x64": "bun-linux-x64-baseline",
    "win32-x64": "bun-windows-x64-baseline",
  };
  const target = targets[host];
  if (target === undefined) {
    throw new Error(`unsupported Bun standalone build host: ${host}`);
  }
  return target;
}
