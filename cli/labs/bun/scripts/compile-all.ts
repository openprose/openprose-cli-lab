import { resolve } from "node:path";

const root = resolve(import.meta.dir, "..");
for (const script of [
  "compile:codex",
  "compile:claude",
  "compile:pi",
  "compile:omp",
]) {
  await run(["bun", "run", script]);
}

async function run(argv: string[]): Promise<void> {
  const child = Bun.spawn(argv, {
    cwd: root,
    stderr: "inherit",
    stdout: "inherit",
  });
  const exitCode = await child.exited;
  if (exitCode !== 0) process.exit(exitCode);
}
