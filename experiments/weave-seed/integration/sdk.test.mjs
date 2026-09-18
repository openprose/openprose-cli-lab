/** Fresh consumer copies: package imports cannot resolve back into this checkout. */
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, cpSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const seed = fileURLToPath(new URL('../', import.meta.url));
const root = mkdtempSync(join(tmpdir(), 'weave-sdk-consumer-'));
const cargo = process.env.WEAVE_CARGO ?? 'cargo';
function run(executable, args, cwd) {
  const child = spawnSync(executable, args, { cwd, encoding: 'utf8', timeout: 120000, maxBuffer: 1024 * 1024,
    env: { PATH: process.env.PATH, HOME: process.env.HOME, CARGO_HOME: process.env.CARGO_HOME ?? join(process.env.HOME, '.cargo'), CARGO_TARGET_DIR: join(root, 'target') } });
  assert.equal(child.error, undefined, child.error?.message);
  assert.equal(child.status, 0, child.stderr);
  return child.stdout;
}
try {
  const js = join(root, 'javascript');
  const packageRoot = join(js, 'node_modules/@openprose/weave-experimental');
  mkdirSync(packageRoot, { recursive: true });
  cpSync(join(seed, 'package.json'), join(packageRoot, 'package.json'));
  for (const file of ['bun/index.mjs', 'bun/host.mjs', 'integration/binding.mjs', 'integration/process.mjs', 'integration/run.mjs', 'integration/config.mjs', 'local/coordinator.mjs']) {
    mkdirSync(resolve(packageRoot, file, '..'), { recursive: true });
    cpSync(join(seed, file), join(packageRoot, file));
  }
  const example = readFileSync(join(seed, 'examples/local.mjs'), 'utf8')
    .replace("'../bun/index.mjs'", "'@openprose/weave-experimental'");
  writeFileSync(join(js, 'consumer.mjs'), example + `\nawait import('@openprose/weave-experimental/host');\nawait import('@openprose/weave-experimental/binding');\nawait import('@openprose/weave-experimental/process');\nawait import('@openprose/weave-experimental/local');\n`);
  const bunOutput = run(process.execPath, ['--no-env-file', 'consumer.mjs'], js);
  assert.deepEqual(JSON.parse(bunOutput), { first: 'satisfied', replay: 'reused', actions: 1 });
  const rust = join(root, 'rust-consumer');
  const rustPackage = join(root, 'rust-package');
  mkdirSync(join(rust, 'src'), { recursive: true });
  mkdirSync(rustPackage);
  for (const file of ['Cargo.toml', 'Cargo.lock', 'lib.rs']) cpSync(join(seed, 'rust', file), join(rustPackage, file));
  writeFileSync(join(rust, 'Cargo.toml'), '[package]\nname="weave-consumer-check"\nversion="0.0.0"\nedition="2021"\n[dependencies]\nopenprose-weave-experimental={path="../rust-package"}\n');
  const rustExample = readFileSync(join(seed, 'examples/local.rs'), 'utf8')
    .replace('#[allow(dead_code)]\n#[path = "../rust/lib.rs"]\nmod weave;', 'use openprose_weave_experimental as weave;');
  writeFileSync(join(rust, 'src/main.rs'), rustExample);
  const rustOutput = run(cargo, ['run', '--offline', '--quiet'], rust);
  assert.equal(rustOutput.trim(), 'first=satisfied replay=reused actions=1');
  console.log('Fresh Bun package import and Rust path-dependency consumers passed. No provider calls.');
} finally {
  rmSync(root, { recursive: true, force: true });
}
