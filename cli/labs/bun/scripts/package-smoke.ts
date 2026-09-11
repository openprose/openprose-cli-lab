import {
  copyFile,
  mkdir,
  mkdtemp,
  rm,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";

interface Candidate {
  readonly binary: string;
  readonly expected: string;
  readonly expectedExit: number;
  readonly sidecars?: readonly string[];
}

const root = resolve(import.meta.dir, "..");
const dist = resolve(root, "dist");
const candidates: readonly Candidate[] = [
  {
    binary: "codex-lab",
    expected: "Unable to locate Codex CLI binaries",
    expectedExit: 1,
  },
  {
    binary: "claude-lab",
    expected: '"extracted":true',
    expectedExit: 0,
  },
  {
    binary: "pi-lab",
    expected: '"openRouterModels":',
    expectedExit: 0,
    sidecars: ["package.json"],
  },
  {
    binary: "omp-lab",
    expected: '"packageVersion":"18.0.8"',
    expectedExit: 0,
    sidecars: ["pi_natives.darwin-arm64.node"],
  },
];

for (const candidate of candidates) {
  await smoke(candidate);
}

async function smoke(candidate: Candidate): Promise<void> {
  const artifactRoot = await mkdtemp(
    join(tmpdir(), `openprose-${candidate.binary}-`),
  );
  try {
    const binary = join(artifactRoot, candidate.binary);
    await copyFile(join(dist, candidate.binary), binary);
    for (const sidecar of candidate.sidecars ?? []) {
      await copyFile(join(dist, sidecar), join(artifactRoot, basename(sidecar)));
    }
    const home = join(artifactRoot, "home");
    await mkdir(home);
    const child = Bun.spawn([binary], {
      cwd: artifactRoot,
      env: { HOME: home, PATH: "/usr/bin:/bin" },
      stderr: "pipe",
      stdout: "pipe",
    });
    const [exitCode, stdout, stderr] = await Promise.all([
      child.exited,
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
    ]);
    const output = `${stdout}\n${stderr}`;
    if (exitCode !== candidate.expectedExit || !output.includes(candidate.expected)) {
      throw new Error(
        `${candidate.binary}: exit=${exitCode}; missing ${JSON.stringify(candidate.expected)}\n${output}`,
      );
    }
    process.stdout.write(
      `${candidate.binary}: ${exitCode === 0 ? "packaged" : "expected-blocker"}\n`,
    );
  } finally {
    await rm(artifactRoot, { force: true, recursive: true });
  }
}
