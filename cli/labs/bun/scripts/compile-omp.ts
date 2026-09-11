import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dir, "..");
const output = resolve(root, "dist/omp-lab");
await compile([
  "bun",
  "build",
  "--compile",
  "--minify",
  "--target=bun-darwin-arm64",
  "--external",
  "omp-legacy-pi-modules",
  resolve(root, "src/compile/omp.ts"),
  "--outfile",
  output,
]);

// The published OMP package does not embed its 18.0.8 N-API addon. Its loader
// explicitly checks beside process.execPath, so the lab artifact is two files.
await Bun.write(
  resolve(dirname(output), "pi_natives.darwin-arm64.node"),
  Bun.file(
    resolve(
      root,
      "node_modules/@oh-my-pi/pi-natives-darwin-arm64/pi_natives.darwin-arm64.node",
    ),
  ),
);

async function compile(argv: string[]): Promise<void> {
  const child = Bun.spawn(argv, { stderr: "inherit", stdout: "inherit" });
  const exitCode = await child.exited;
  if (exitCode !== 0) process.exit(exitCode);
}
