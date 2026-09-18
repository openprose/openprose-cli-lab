/** Local-only coordinator; no language parsing or account/provider selection. */
import { readFileSync, mkdirSync, lstatSync, rmdirSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import { runConfig } from '../integration/run.mjs';
import { readConfigBytes, parseConfigBytes } from '../integration/config.mjs';
import { decodeCheckpoint, FileHost } from '../bun/host.mjs';

function selection(path) {
  const configPath = resolve(path), bytes = readConfigBytes(configPath);
  const config = parseConfigBytes(bytes,{stripBOM:true});
  if (config.schema !== 1 || typeof config.checkpointDirectory !== 'string' || !config.checkpointDirectory.trim()) throw Error('explicit checkpointDirectory required');
  return { configPath, digest: createHash('sha256').update(bytes).digest('hex'), directory: resolve(dirname(configPath), config.checkpointDirectory) };
}
const present = path => { try { return lstatSync(path); } catch (error) { if (error.code === 'ENOENT') return null; throw error; } };

/** Non-mutating diagnostic, not an atomic read or authority to apply effects. */
export function statusConfig(path) {
  const selected = selection(path);
  let checkpoint = null;
  try { checkpoint = decodeCheckpoint(readFileSync(join(selected.directory, 'checkpoint.json'))); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  return { serviceOwned: !!present(join(selected.directory, 'service.lock')), checkpointLocked: !!present(join(selected.directory, 'lock')), checkpoint };
}

export function acquireOwner(path) {
  const selected = selection(path), lock = join(selected.directory, 'service.lock');
  mkdirSync(selected.directory, { recursive: true, mode: 0o700 });
  try { mkdirSync(lock, { mode: 0o700 }); }
  catch (error) { if (error.code === 'EEXIST') throw Error('local service busy; existing owner lock requires completion or trusted reconciliation'); throw error; }
  const identity = lstatSync(lock);
  let active = true;
  function check() {
    if (!active) throw Error('service owner released');
    const current = present(lock);
    if (!current || current.dev !== identity.dev || current.ino !== identity.ino) throw Error('service lock ownership changed');
  }
  return Object.freeze({
    step() {
      check();
      if (selection(selected.configPath).digest !== selected.digest) throw Error('configuration changed during service; stop and review before restart');
      return runConfig(selected.configPath);
    },
    settle(binding, attempt, outcome, receipt) {
      check();
      if (selection(selected.configPath).digest !== selected.digest) throw Error('configuration changed during service; stop and review before restart');
      return new FileHost(selected.directory).settle(binding, attempt, outcome, receipt);
    },
    release() { check(); rmdirSync(lock); active = false; },
  });
}
export function stepConfig(path) {
  const owner = acquireOwner(path);
  try { return owner.step(); } finally { owner.release(); }
}
/** Explicit trusted settlement; no evidence observation, provider call, or budget reset. */
export function settleConfig(path, binding, attempt, outcome, receipt) {
  const owner = acquireOwner(path);
  try { return owner.settle(binding, attempt, outcome, receipt); } finally { owner.release(); }
}
function pause(ms, signal) {
  if (signal?.aborted) return Promise.resolve();
  return new Promise(resolve => {
    const done = () => { clearTimeout(timer); signal?.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, ms);
    signal?.addEventListener('abort', done, { once: true });
  });
}
/** Every poll observes files; fresh identical accepted evidence avoids assessor/actor calls. */
export async function serveConfig(path, { pollMs, maxSteps, signal, onStep = () => {} } = {}) {
  if (!Number.isSafeInteger(pollMs) || pollMs < 1 || pollMs > 3600000 || !Number.isSafeInteger(maxSteps) || maxSteps < 1 || maxSteps > 1000000) throw Error('bounded pollMs (1..3600000) and maxSteps (1..1000000) required');
  const owner = acquireOwner(path);
  let steps = 0, last = null;
  try {
    while (steps < maxSteps && !signal?.aborted) {
      last = owner.step(); steps++;
      await onStep(last, steps);
      if (last.checkpoint.pending !== null) break;
      if (steps < maxSteps && !signal?.aborted) await pause(pollMs, signal);
    }
    return { steps, stopped: signal?.aborted ? 'cancelled' : last?.checkpoint.pending !== null ? 'pending' : 'step-limit', last };
  } finally { owner.release(); }
}
