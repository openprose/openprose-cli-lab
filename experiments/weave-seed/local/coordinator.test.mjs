import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { stepConfig, serveConfig, statusConfig, acquireOwner } from './coordinator.mjs';
const root = mkdtempSync(join(tmpdir(), 'weave-local-'));
const fixture = resolve(import.meta.dir, 'fixture.mjs');
let checks = 0;
async function test(name, operation) { await operation(); checks++; console.log('PASS', name); }
function setup(name, changes = {}) {
  const dir = join(root, name); mkdirSync(dir);
  for (const [file, value] of Object.entries({ 'kernel.md': 'Synthetic kernel', 'program.md': 'Synthetic source/report equality', 'source.txt': 'one', 'report.txt': '' })) writeFileSync(join(dir, file), value);
  const config = { schema: 1, root: '.', kernel: 'kernel.md', contracts: ['program.md'], evidence: ['source.txt', 'report.txt'], capabilityVersion: 'synthetic-v1', assessor: [process.execPath, fixture, 'assess'], actor: [process.execPath, fixture, 'act'], checkpointDirectory: 'host', maxAttempts: 3, ttlMs: 60000, timeoutMs: 2000, ...changes };
  const path = join(dir, 'config.json'); writeFileSync(path, JSON.stringify(config));
  return { dir, path, config };
}
try {
  await test('read-only status does not create host; corrupt checkpoint rejected', () => {
    const s = setup('status'); assert.equal(statusConfig(s.path).checkpoint, null); assert(!existsSync(join(s.dir, 'host')));
    mkdirSync(join(s.dir, 'host')); writeFileSync(join(s.dir, 'host/checkpoint.json'), '{}'); assert.throws(() => statusConfig(s.path));
  });
  await test('step repair, restart reuse and cumulative source-change budget', () => {
    const s = setup('reuse', { maxAttempts: 2 }); assert.equal(stepConfig(s.path).status, 'satisfied');
    const calls = readFileSync(join(s.dir, 'calls.log'), 'utf8'); assert.equal(stepConfig(s.path).status, 'reused'); assert.equal(readFileSync(join(s.dir, 'calls.log'), 'utf8'), calls);
    writeFileSync(join(s.dir, 'source.txt'), 'two'); assert.equal(stepConfig(s.path).checkpoint.attempts, 2);
    writeFileSync(join(s.dir, 'source.txt'), 'three'); assert.equal(stepConfig(s.path).status, 'attempt-limit');
  });
  await test('service holds owner across idle polls and permits restart after release', async () => {
    const s = setup('owner'); const statuses = [];
    await serveConfig(s.path, { pollMs: 1, maxSteps: 3, onStep(result) { statuses.push(result.status); assert.throws(() => stepConfig(s.path), /busy/); assert(statusConfig(s.path).serviceOwned); } });
    assert.deepEqual(statuses, ['satisfied', 'reused', 'reused']); assert(!statusConfig(s.path).serviceOwned); assert.equal(stepConfig(s.path).status, 'reused');
  });
  await test('separate process cannot step under service owner', () => {
    const s = setup('process-owner'); const owner = acquireOwner(s.path);
    try {
      const child = spawnSync(process.execPath, ['--no-env-file', resolve(import.meta.dir, 'run.mjs'), 'step', s.path], { env: {}, encoding: 'utf8', timeout: 3000 });
      assert.equal(child.status, 1); assert.match(child.stderr, /busy/); assert(!existsSync(join(s.dir, 'calls.log')));
    } finally { owner.release(); }
  });
  await test('actual CLI SIGTERM during idle releases owner', async () => {
    const s = setup('signal');
    await new Promise((done, reject) => {
      const child = spawn(process.execPath, ['--no-env-file', resolve(import.meta.dir, 'run.mjs'), 'serve', s.path, '--poll-ms', '5000', '--max-steps', '10'], { env: {}, stdio: ['ignore', 'pipe', 'pipe'] });
      let output = '', sent = false;
      const timer = setTimeout(() => { child.kill('SIGKILL'); reject(Error('signal test timed out')); }, 5000);
      child.stdout.on('data', bytes => { output += bytes; if (!sent && output.includes('satisfied')) { sent = true; child.kill('SIGTERM'); } });
      child.on('error', reject);
      child.on('close', code => { clearTimeout(timer); try { assert.equal(code, 0); assert(output.includes('cancelled')); done(); } catch (error) { reject(error); } });
    });
    assert(!statusConfig(s.path).serviceOwned); assert.equal(stepConfig(s.path).status, 'reused');
  });
  await test('expiry reassesses without another effect', async () => {
    const s = setup('ttl', { ttlMs: 1000 }); const result = await serveConfig(s.path, { pollMs: 1100, maxSteps: 2 });
    assert.equal(result.steps, 2); assert.equal(result.last.status, 'satisfied'); assert.equal(result.last.checkpoint.attempts, 1);
    assert.equal(readFileSync(join(s.dir, 'calls.log'), 'utf8').split('act\n').length - 1, 1);
  });
  await test('gap never calls capability; source mutation during polling invalidates', async () => {
    const s = setup('gap'); rmSync(join(s.dir, 'source.txt')); assert.equal(stepConfig(s.path).status, 'evidence-gap'); assert(!existsSync(join(s.dir, 'calls.log')));
    writeFileSync(join(s.dir, 'source.txt'), 'one'); const states = [];
    await serveConfig(s.path, { pollMs: 1, maxSteps: 2, onStep(result, count) { states.push(result.status); if (count === 1) writeFileSync(join(s.dir, 'source.txt'), 'two'); } });
    assert.deepEqual(states, ['satisfied', 'satisfied']); assert.equal(readFileSync(join(s.dir, 'report.txt'), 'utf8'), 'two');
  });
  await test('uncertain actor stops service and restart does not replay', async () => {
    const s = setup('pending', { actor: [process.execPath, fixture, 'fail'] }); const result = await serveConfig(s.path, { pollMs: 1, maxSteps: 4 });
    assert.equal(result.stopped, 'pending'); assert.equal(result.steps, 1); assert(result.last.checkpoint.pending);
    const calls = readFileSync(join(s.dir, 'calls.log'), 'utf8'); assert.equal(stepConfig(s.path).status, 'recovery-needed'); assert.equal(readFileSync(join(s.dir, 'calls.log'), 'utf8'), calls);
  });
  await test('abort while idle releases owner without resetting checkpoint', async () => {
    const s = setup('abort'); const abort = new AbortController();
    const result = await serveConfig(s.path, { pollMs: 5000, maxSteps: 10, signal: abort.signal, onStep() { setTimeout(() => abort.abort(), 5); } });
    assert.equal(result.stopped, 'cancelled'); assert.equal(result.steps, 1); assert(!statusConfig(s.path).serviceOwned); assert.equal(statusConfig(s.path).checkpoint.attempts, 1);
  });
  await test('existing service/host locks never auto-recover; config change fails', () => {
    const s = setup('locks'); const owner = acquireOwner(s.path); writeFileSync(s.path, JSON.stringify({ ...s.config, maxAttempts: 99 })); assert.throws(() => owner.step(), /configuration changed/); owner.release();
    mkdirSync(join(s.dir, 'host/lock')); assert.throws(() => stepConfig(s.path), /busy/); assert(existsSync(join(s.dir, 'host/lock')));
    mkdirSync(join(s.dir, 'host/service.lock')); assert.throws(() => stepConfig(s.path), /busy/); assert(existsSync(join(s.dir, 'host/service.lock')));
  });
  await test('assessor process errors propagate and release only service lock', async () => {
    const s = setup('assessor-error', { assessor: [process.execPath, join(root, 'missing-capability.mjs')] });
    await assert.rejects(serveConfig(s.path, { pollMs: 1, maxSteps: 3 }), /capability process failed/);
    assert(!statusConfig(s.path).serviceOwned); assert(!existsSync(join(s.dir, 'report.txt.changed')));
    assert.equal(readFileSync(join(s.dir, 'report.txt'), 'utf8'), '');
  });
  await test('invalid serving bounds and forbidden literal environment fail', async () => {
    const s = setup('invalid', { environment: { SECRET: 'never-forward' } }); assert.throws(() => stepConfig(s.path), /environmentKeys/);
    await assert.rejects(serveConfig(s.path, { pollMs: 0, maxSteps: 3 }), /bounded/);
  });
  console.log(`PASS ${checks} local coordinator checks`);
} finally { rmSync(root, { recursive: true, force: true }); }
