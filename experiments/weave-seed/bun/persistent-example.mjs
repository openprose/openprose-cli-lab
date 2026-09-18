/** Synthetic local-file walkthrough; no models, kernel loader, or automatic binding. */
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
import { FileHost } from './host.mjs';

if (process.argv.length !== 3) {
  console.error('Usage: bun experiments/weave-seed/bun/persistent-example.mjs NEW_DIRECTORY');
  process.exit(2);
}
const root = resolve(process.argv[2]);
// A new leaf is required. Never initialize or overwrite an existing directory.
try { mkdirSync(root, { mode: 0o700 }); }
catch (error) {
  if (error.code !== 'EEXIST') throw error;
  console.error('Refusing an existing path; choose a new directory whose parent exists.');
  process.exit(1);
}
const desiredPath = join(root, 'desired.txt');
const actualPath = join(root, 'actual.txt');
const checkpointDirectory = join(root, 'host');
writeFileSync(desiredPath, 'Tuesday', { flag: 'wx', mode: 0o600 });
writeFileSync(actualPath, 'Monday', { flag: 'wx', mode: 0o600 });
let actions = 0;
const now = () => Math.floor(Date.now() / 1000);
const decode = bytes => new TextDecoder('utf-8', { fatal: true }).decode(bytes);
const capabilities = {
  observe() {
    const observedAt = now();
    try {
      const desired = decode(readFileSync(desiredPath));
      const actual = decode(readFileSync(actualPath));
      const payload = JSON.stringify({ desired, actual });
      const identity = createHash('sha256').update(JSON.stringify([root, 'desired.txt', 'actual.txt', payload])).digest('hex');
      return { identity, payload, observedAt, validUntil: observedAt + 60, gap: false };
    } catch {
      return { identity: 'unavailable-source', payload: '', observedAt, validUntil: observedAt + 60, gap: true };
    }
  },
  assess(evidence) {
    const { desired, actual } = JSON.parse(evidence.payload);
    return desired === actual ? 'satisfied' : 'work-needed';
  },
  act(evidence, attempt) {
    const stored = JSON.parse(readFileSync(join(checkpointDirectory, 'checkpoint.json'), 'utf8'));
    assert.equal(stored.pending, attempt, 'the host must persist pending before action');
    const { desired } = JSON.parse(evidence.payload);
    actions++;
    writeFileSync(actualPath, desired, { encoding: 'utf8', mode: 0o600 });
  },
  clock: now,
  newId: () => `example-attempt-${actions + 1}`,
};
function step(event, expectedStatus, expectedAttempts) {
  // Reopen and load the on-disk checkpoint for every event.
  const result = new FileHost(checkpointDirectory).step('synthetic-file-date-rule-v1', capabilities, 2);
  assert.equal(result.status, expectedStatus);
  assert.equal(result.checkpoint.attempts, expectedAttempts);
  assert.equal(actions, expectedAttempts);
  console.log(JSON.stringify({ event, status: result.status, attempts: result.checkpoint.attempts, actions, actual: decode(readFileSync(actualPath)) }));
}
step('initial-repair', 'satisfied', 1);
step('reopened-reuse', 'reused', 1);
writeFileSync(desiredPath, 'Wednesday');
step('desired-changed', 'satisfied', 2);
writeFileSync(desiredPath, Buffer.from([0xff]));
step('source-corrupt', 'evidence-gap', 2);
unlinkSync(desiredPath);
step('source-unavailable', 'evidence-gap', 2);
