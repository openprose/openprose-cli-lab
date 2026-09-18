import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { emptyCheckpoint, reconcile, settlePending } from './index.mjs';
const rows = readFileSync(new URL('../fixtures/lifecycle.tsv', import.meta.url), 'utf8').trim().split('\n').filter(l => !l.startsWith('#'));
function initial(kind) {
  const cp = emptyCheckpoint();
  if (['cached', 'expired', 'rebound'].includes(kind)) Object.assign(cp, { binding: kind === 'rebound' ? 'v0' : 'v1', evidence: 'a', disposition: 'satisfied', validUntil: kind === 'expired' ? 9 : 100 });
  if (['pending', 'exhausted'].includes(kind)) { cp.attempts = 1; cp.binding = 'v1'; }
  if (kind === 'pending') cp.pending = 'prior';
  return cp;
}
function hostFor(obs, judges, outcome, failSave = 0) {
  let observes = 0, assesses = 0, acts = 0, saves = [];
  return {
    observe() { const c = obs[observes++]; assert.ok(c && c !== '-'); return { identity: c, payload: c, observedAt: c === 'f' ? 11 : 0, validUntil: c === 'x' ? 10 : 100, gap: c === 'g' }; },
    assess() { const c = judges[assesses++]; assert.ok('swu'.includes(c)); return { s: 'satisfied', w: 'work-needed', u: 'unknown' }[c]; },
    act(e, id) { assert.equal(saves.at(-1).pending, id); assert.ok(saves.at(-1).attempts > 0); acts++; if (outcome === 'fail') throw Error('uncertain'); },
    save(cp) { if (saves.length + 1 === failSave) throw Error('save failed'); saves.push(structuredClone(cp)); },
    clock: () => 10, newId: () => 'attempt-1', counts: () => [observes, assesses, acts, saves.length],
  };
}
for (const line of rows) {
  const [name, kind, obs, judges, action, limit, status, disposition, attempts, pending, ...counts] = line.split('\t');
  const cp = initial(kind), original = structuredClone(cp), host = hostFor(obs, judges, action);
  const result = reconcile('v1', cp, host, Number(limit));
  assert.deepEqual([result.status, result.checkpoint.disposition, result.checkpoint.attempts, result.checkpoint.pending, ...host.counts()], [status, disposition, Number(attempts), pending === '-' ? null : pending, ...counts.map(Number)], name);
  assert.deepEqual(cp, original, `${name}: input immutable`);
}
for (const failAt of [1, 2, 3, 4]) {
  const host = hostFor('aabb', 'ws', 'ok', failAt);
  assert.throws(() => reconcile('v1', emptyCheckpoint(), host), /save failed/);
  assert.equal(host.counts()[2], failAt <= 2 ? 0 : 1);
}
const pending = initial('pending');
for (const outcome of ['completed', 'not-applied']) {
  const cp = settlePending(pending, 'v1', 'prior', outcome, 'receipt-1');
  assert.equal(cp.pending, null); assert.equal(cp.attempts, 1); assert.equal(cp.disposition, 'unknown'); assert.equal(cp.validUntil, 0); assert.equal(cp.settlement.receipt, 'receipt-1');
  assert.throws(() => settlePending(cp, 'v1', 'prior', outcome, 'receipt-1'));
}
for (const args of [['v0', 'prior', 'completed', 'receipt'], ['v1', 'other', 'completed', 'receipt'], ['v1', 'prior', 'unknown', 'receipt'], ['v1', 'prior', 'completed', ' ']]) assert.throws(() => settlePending(pending, ...args));
assert.equal(pending.pending, 'prior');
console.log(`PASS ${rows.length} shared lifecycle cases; 4 save failures; settlement acceptance/rejection and input immutability`);
// Snapshot isolation: observers may reuse mutable buffers between observations.
const shared = { identity: 'a', payload: 'good', observedAt: 0, validUntil: 100, gap: false };
const aliasHost = hostFor('aa', 's', 'ok');
aliasHost.observe = () => shared;
aliasHost.assess = evidence => { assert.ok(Object.isFrozen(evidence)); shared.identity = 'b'; shared.payload = 'bad'; return 'satisfied'; };
assert.equal(reconcile('v1', emptyCheckpoint(), aliasHost).status, 'stale-assessment');
for (const method of ['observe', 'assess', 'save', 'clock', 'newId']) {
  const host = hostFor('aabb', 'ws', 'ok');
  host[method] = () => Promise.resolve();
  assert.throws(() => reconcile('v1', emptyCheckpoint(), host), /synchronous host required/, method);
  assert.equal(host.counts()[2], 0);
}
const asyncActor = hostFor('aa', 'w', 'ok');
asyncActor.act = () => Promise.resolve();
const uncertain = reconcile('v1', emptyCheckpoint(), asyncActor);
assert.equal(uncertain.status, 'action-outcome-unknown'); assert.equal(uncertain.checkpoint.pending, 'attempt-1');
for (const settlement of [{}, {binding:'v1',attempt:'prior',outcome:'unknown',receipt:'r'}, {binding:'v1',attempt:'prior',outcome:'completed',receipt:'r',extra:1}]) {
  assert.throws(() => reconcile('v1', {...emptyCheckpoint(), settlement}, hostFor('aa','s','ok')), /invalid settlement record/);
}
console.log('PASS mutable observer snapshots, synchronous callback enforcement, and checkpoint settlement validation');
// Deterministic sequential stress: independent world truth, cumulative budget.
let world = 'good', now = 10, actions = 0, assessments = 0, latestSaved = null;
let checkpoint = emptyCheckpoint();
const stressHost = {
  observe: () => ({ identity: world, payload: world, observedAt: now, validUntil: now + 2, gap: false }),
  assess(e) { assessments++; return e.payload === 'good' ? 'satisfied' : e.payload === 'bad' ? 'work-needed' : 'unknown'; },
  act(e, attempt) { assert.equal(latestSaved.pending, attempt); assert.equal(e.payload, 'bad'); actions++; world = 'good'; },
  save(cp) { latestSaved = structuredClone(cp); },
  clock: () => now,
  newId: () => `stress-${actions + 1}`,
};
for (let i = 0; i < 10000; i++) {
  const mode = i % 7;
  world = mode === 0 || mode === 4 ? 'bad' : mode === 2 || mode === 5 ? 'unknown' : 'good';
  if (mode === 6) now += 3;
  const binding = i % 11 === 0 ? 'v2' : 'v1';
  const before = structuredClone(checkpoint), inputWorld = world, beforeActions = actions, beforeAssessments = assessments;
  const canReuse = before.binding === binding && before.evidence === inputWorld && before.disposition === 'satisfied' && now < before.validUntil;
  const result = reconcile(binding, checkpoint, stressHost, 2000);
  checkpoint = result.checkpoint;
  const expectedActions = inputWorld === 'bad' && before.attempts < 2000 ? 1 : 0;
  assert.equal(actions - beforeActions, expectedActions);
  assert.equal(checkpoint.attempts, actions);
  assert.equal(checkpoint.pending, null);
  assert.equal(result.status === 'reused', canReuse);
  assert.equal(assessments - beforeAssessments, canReuse ? 0 : expectedActions ? 2 : 1);
  assert.equal(checkpoint.disposition, inputWorld === 'unknown' ? 'unknown' : inputWorld === 'bad' && !expectedActions ? 'work-needed' : 'satisfied');
  assert.equal(result.status, canReuse ? 'reused' : inputWorld === 'unknown' ? 'unknown' : inputWorld === 'bad' && !expectedActions ? 'attempt-limit' : 'satisfied');
}
assert.equal(actions, 2000);
console.log('PASS 10000 sequential world transitions; 2000 cumulative actions; binding/expiry/reuse/budget assertions');
