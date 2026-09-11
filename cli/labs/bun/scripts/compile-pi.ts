import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dir, "..");
const output = resolve(root, "dist/pi-lab");
await compile([
  "bun",
  "build",
  "--compile",
  "--minify",
  "--target=bun-darwin-arm64",
  resolve(root, "src/compile/pi.ts"),
  "--outfile",
  output,
]);

// pi-coding-agent resolves package metadata relative to process.execPath before
// user code runs. This sidecar is therefore required by its current SDK build.
await Bun.write(
  resolve(dirname(output), "package.json"),
  Bun.file(resolve(root, "node_modules/@mariozechner/pi-coding-agent/package.json")),
);

async function compile(argv: string[]): Promise<void> {
  const child = Bun.spawn(argv, { stderr: "inherit", stdout: "inherit" });
  const exitCode = await child.exited;
  if (exitCode !== 0) process.exit(exitCode);
}
