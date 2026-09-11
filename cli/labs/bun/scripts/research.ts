import { resolve } from "node:path";

const root = resolve(import.meta.dir, "..");
for (const script of ["check", "compile", "package:smoke"]) {
  const child = Bun.spawn(["bun", "run", script], {
    cwd: root,
    stderr: "inherit",
    stdout: "inherit",
  });
  const exitCode = await child.exited;
  if (exitCode !== 0) process.exit(exitCode);
}
