import { describe, expect, test } from "bun:test";
import { nativeCompileTarget } from "../scripts/compile-target";

describe("native standalone compile target", () => {
  test("uses baseline runtimes for every admitted x64 build host", () => {
    expect(nativeCompileTarget("darwin", "x64")).toBe("bun-darwin-x64-baseline");
    expect(nativeCompileTarget("linux", "x64")).toBe("bun-linux-x64-baseline");
    expect(nativeCompileTarget("win32", "x64")).toBe("bun-windows-x64-baseline");
  });

  test("uses exact native ARM64 runtimes where Bun has no baseline variant", () => {
    expect(nativeCompileTarget("darwin", "arm64")).toBe("bun-darwin-arm64");
    expect(nativeCompileTarget("linux", "arm64")).toBe("bun-linux-arm64");
  });

  test("refuses unsupported build hosts instead of emitting a mislabeled binary", () => {
    expect(() => nativeCompileTarget("freebsd", "x64")).toThrow(
      "unsupported Bun standalone build host: freebsd-x64",
    );
    expect(() => nativeCompileTarget("win32", "arm64")).toThrow(
      "unsupported Bun standalone build host: win32-arm64",
    );
  });
});
