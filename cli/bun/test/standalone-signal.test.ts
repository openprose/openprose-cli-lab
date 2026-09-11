import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const bunRoot = resolve(import.meta.dir, "..");
const buildScript = join(bunRoot, "scripts", "image-bundle.ts");
const fakeHarness = resolve(bunRoot, "../conformance/fake-harness/fake_harness.py");
const imageGenerator = resolve(bunRoot, "../shared/image/bundle/image_bundle.py");
const sentinelImage = resolve(bunRoot, "../shared/image/sentinel-v1");
const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe("built standalone POSIX cancellation", () => {
  test.skipIf(process.platform === "win32")(
    "normalizes real SIGINT once and reaps the controlled in-group descendants",
    async () => {
      const root = await mkdtemp(join(tmpdir(), "openprose-bun-sigint-"));
      roots.push(root);
      const executable = join(root, "prose-test-seams");
      const identitiesPath = join(root, "descendants.json");
      const bundle = join(root, "sentinel.bundle.bin");
      const checksum = join(root, "sentinel.bundle.sha256");
      const generated = Bun.spawn(["python3", imageGenerator, "build", sentinelImage, bundle, "--checksum", checksum], {
        cwd: root,
        env: { PATH: process.env.PATH },
        stdout: "pipe",
        stderr: "pipe",
      });
      const [generatedExit, generatedStderr] = await Promise.all([
        generated.exited,
        new Response(generated.stderr).text(),
      ]);
      expect(generatedExit, generatedStderr).toBe(0);
      const build = Bun.spawn([
        process.execPath,
        "--no-env-file",
        buildScript,
        "build",
        "--outfile",
        executable,
        "--image-dir",
        sentinelImage,
        "--bundle",
        bundle,
        "--checksum",
        checksum,
        "--test-seams",
      ], {
        cwd: bunRoot,
        env: { PATH: process.env.PATH, OPENPROSE_BUILD_COMMIT: "signal-test-commit" },
        stdout: "pipe",
        stderr: "pipe",
      });
      const [buildExit, buildStderr] = await Promise.all([
        build.exited,
        new Response(build.stderr).text(),
      ]);
      expect(buildExit, buildStderr).toBe(0);

      const child = Bun.spawn([
        executable,
        "--harness=mock",
        "--transport=fake-process",
        "--timeout=20s",
        "--output=jsonl",
        "run",
      ], {
        cwd: root,
        env: {
          PATH: process.env.PATH,
          HOME: join(root, "home"),
          XDG_CONFIG_HOME: join(root, "config"),
          OPENPROSE_CONFORMANCE_FAKE_HARNESS: fakeHarness,
          OPENPROSE_CONFORMANCE_FAKE_SCENARIO: "descendant",
          OPENPROSE_CONFORMANCE_DESCENDANT_IDENTITIES: identitiesPath,
        },
        stdin: "ignore",
        stdout: "pipe",
        stderr: "pipe",
      });
      const stdoutPromise = new Response(child.stdout).text();
      const stderrPromise = new Response(child.stderr).text();
      try {
        await waitForFile(identitiesPath, 5_000);
        const identities = validatedFixtureIdentities(
          JSON.parse(await readFile(identitiesPath, "utf8")),
        );
        expect(identities).not.toBeNull();
        process.kill(child.pid, "SIGINT");
        const exitCode = await settleWithin(child.exited, 8_000, "standalone did not settle SIGINT");
        const [stdout, stderr] = await Promise.all([
          stdoutPromise,
          stderrPromise,
        ]);
        expect(exitCode).toBe(24);
        expect(stderr).toBe("");
        const records = stdout.trimEnd().split("\n").map((line) => JSON.parse(line));
        const terminal = records.filter((record) =>
          record.type === "runner.failed" || record.type === "runner.completed"
        );
        expect(terminal).toHaveLength(1);
        expect(terminal[0]).toMatchObject({
          type: "runner.failed",
          payload: { kind: "runner.failed", error: { code: "CANCELLED", exitCode: 24 } },
        });
        expect(records.filter((record) => record.type === "runner.cancelled")).toHaveLength(1);
        expect(records.filter((record) => record.payload?.error?.code === "CANCELLED")).toHaveLength(1);
        for (const pid of [identities!.childPid, identities!.grandchildPid]) {
          await waitForProcessExit(pid, 2_000);
          expect(processExists(pid)).toBeFalse();
        }
      } finally {
        await cleanupPublishedFixture(child, identitiesPath);
        await Promise.allSettled([stdoutPromise, stderrPromise]);
      }
    },
    30_000,
  );
});

async function waitForFile(path: string, timeoutMs: number): Promise<void> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() <= deadline) {
    if (await Bun.file(path).exists()) return;
    await Bun.sleep(10);
  }
  throw new Error(`timed out waiting for ${path}`);
}

async function waitForProcessExit(pid: number, timeoutMs: number): Promise<void> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() <= deadline && processExists(pid)) await Bun.sleep(10);
}

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (caught) {
    return (caught as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

type PublishedFixtureIdentities = {
  attemptedDetachment: false;
  childPid: number;
  grandchildPid: number;
  processGroupId: number;
  runNonce: string;
};

type FixtureWrapper = {
  readonly pid: number;
  readonly exited: Promise<number>;
  readonly exitCode: number | null;
  kill(signal?: NodeJS.Signals | number): void;
};

function validatedFixtureIdentities(value: unknown): PublishedFixtureIdentities | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  const expected = [
    "attemptedDetachment",
    "childPid",
    "grandchildPid",
    "processGroupId",
    "runNonce",
  ];
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    return null;
  }
  if (record.attemptedDetachment !== false) return null;
  if (typeof record.runNonce !== "string" || !record.runNonce.startsWith("nonce:") || record.runNonce.length <= 6) {
    return null;
  }
  for (const key of ["childPid", "grandchildPid", "processGroupId"] as const) {
    if (!Number.isSafeInteger(record[key]) || (record[key] as number) <= 1) return null;
  }
  if (record.childPid === record.grandchildPid) return null;
  return record as PublishedFixtureIdentities;
}

async function settleWithin<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => reject(new Error(`${message} within ${timeoutMs}ms`)), timeoutMs);
      }),
    ]);
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}

async function processGroupFor(pid: number): Promise<number | null> {
  const probe = Bun.spawn(["ps", "-o", "pgid=", "-p", String(pid)], {
    env: { PATH: process.env.PATH },
    stdin: "ignore",
    stdout: "pipe",
    stderr: "ignore",
  });
  const [exitCode, stdout] = await Promise.all([
    probe.exited,
    new Response(probe.stdout).text(),
  ]);
  if (exitCode !== 0) return null;
  const group = Number(stdout.trim());
  return Number.isSafeInteger(group) && group > 1 ? group : null;
}

async function publishedFixtureIdentities(path: string): Promise<PublishedFixtureIdentities | null> {
  try {
    return validatedFixtureIdentities(JSON.parse(await readFile(path, "utf8")));
  } catch {
    return null;
  }
}

async function killValidatedPublishedGroup(path: string): Promise<void> {
  const identities = await publishedFixtureIdentities(path);
  if (identities === null) return;
  const currentGroup = await processGroupFor(process.pid);
  if (currentGroup === null || currentGroup === identities.processGroupId) return;

  let matchingLiveMember = false;
  for (const pid of [identities.childPid, identities.grandchildPid]) {
    const observedGroup = await processGroupFor(pid);
    if (observedGroup === null) continue;
    if (observedGroup !== identities.processGroupId) return;
    matchingLiveMember = true;
  }
  if (!matchingLiveMember) return;

  try {
    process.kill(-identities.processGroupId, "SIGKILL");
  } catch (caught) {
    if ((caught as NodeJS.ErrnoException).code !== "ESRCH") return;
  }
  await waitForProcessExit(-identities.processGroupId, 2_000);
}

async function cleanupPublishedFixture(child: FixtureWrapper, identitiesPath: string): Promise<void> {
  try {
    await killValidatedPublishedGroup(identitiesPath);
    if (child.exitCode === null) {
      try {
        child.kill("SIGKILL");
      } catch (caught) {
        if ((caught as NodeJS.ErrnoException).code !== "ESRCH") return;
      }
      try {
        await settleWithin(child.exited, 2_000, "fixture wrapper cleanup did not settle");
      } catch {
        // The final group pass below remains independently scoped to published identities.
      }
    }
    await killValidatedPublishedGroup(identitiesPath);
  } catch {
    // Cleanup is best-effort but never broadens beyond validated fixture identities.
  }
}
