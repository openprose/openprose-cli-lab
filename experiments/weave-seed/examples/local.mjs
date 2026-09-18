import assert from 'node:assert/strict';
import { emptyCheckpoint, reconcile } from '../bun/index.mjs';

// Synthetic state and in-memory saves demonstrate callbacks, not durable storage.
let actual = 'Monday';
const desired = 'Tuesday';
let saved = emptyCheckpoint();
let actions = 0;
const host = {
  observe: () => ({ identity: `${desired}/${actual}`, payload: actual, observedAt: 0, validUntil: 100, gap: false }),
  assess: evidence => evidence.payload === desired ? 'satisfied' : 'work-needed',
  save: checkpoint => { saved = structuredClone(checkpoint); },
  act: (_evidence, attempt) => {
    assert.equal(saved.pending, attempt);
    actions += 1;
    actual = desired;
  },
  clock: () => 10,
  newId: () => 'example-attempt-1',
};
const repaired = reconcile('synthetic-date-rule-v1', emptyCheckpoint(), host, 1);
const replay = reconcile('synthetic-date-rule-v1', repaired.checkpoint, host, 1);
assert.equal(repaired.status, 'satisfied');
assert.equal(replay.status, 'reused');
assert.equal(actions, 1);
console.log(JSON.stringify({ first: repaired.status, replay: replay.status, actions }));
