import { describe, expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { WINDOWS_HOST_ADMISSION_ENABLED, WINDOWS_HOST_EXPECTED_SHA256 } from "../src/core/build";

const bunRoot = resolve(import.meta.dir, "..");
const buildScript = join(bunRoot, "scripts", "image-bundle.ts");

describe("Windows host build admission", () => {
  test("source execution defaults to unavailable", () => {
    expect(WINDOWS_HOST_ADMISSION_ENABLED).toBe(false);
    expect(WINDOWS_HOST_EXPECTED_SHA256).toBeNull();
  });

  test.each([
    ["malformed admission", { OPENPROSE_WINDOWS_HOST_ADMISSION: "true" }],
    ["malformed digest", { OPENPROSE_WINDOWS_HOST_ADMISSION: "0", OPENPROSE_WINDOWS_HOST_SHA256: "A".repeat(64) }],
    ["admission without digest", { OPENPROSE_WINDOWS_HOST_ADMISSION: "1" }],
  ])("rejects %s at build time", async (_label, environment) => {
    const result = await runBuild(environment);
    expect(result.exitCode).not.toBe(0);
    expect(result.stderr).toContain("Windows host");
  });

  test("accepts an exact digest while compiling admission off", async () => {
    const root = await mkdtemp(join(tmpdir(), "openprose-windows-build-"));
    try {
      const outfile = join(root, "prose");
      const result = await runBuild({
        OPENPROSE_WINDOWS_HOST_ADMISSION: "0",
        OPENPROSE_WINDOWS_HOST_SHA256: "a".repeat(64),
      }, outfile);
      expect(result.exitCode, result.stderr).toBe(0);
      expect(await Bun.file(outfile).exists()).toBe(true);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }, 15_000);

  test("accepts admission only when paired with an exact digest", async () => {
    const root = await mkdtemp(join(tmpdir(), "openprose-windows-admitted-build-"));
    try {
      const outfile = join(root, "prose");
      const result = await runBuild({
        OPENPROSE_WINDOWS_HOST_ADMISSION: "1",
        OPENPROSE_WINDOWS_HOST_SHA256: "b".repeat(64),
      }, outfile);
      expect(result.exitCode, result.stderr).toBe(0);
      expect(await Bun.file(outfile).exists()).toBe(true);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }, 15_000);
});

async function runBuild(environment: Record<string, string>, outfile = "/tmp/openprose-invalid-build-never-written"): Promise<{
  exitCode: number;
  stdout: string;
  stderr: string;
}> {
  const child = Bun.spawn([
    process.execPath,
    "--no-env-file",
    buildScript,
    "build",
    "--outfile",
    outfile,
  ], {
    cwd: bunRoot,
    env: { PATH: process.env.PATH, HOME: process.env.HOME, ...environment },
    stdin: "ignore",
    stdout: "pipe",
    stderr: "pipe",
  });
  const [exitCode, stdout, stderr] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ]);
  return { exitCode, stdout, stderr };
}
