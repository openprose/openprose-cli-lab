/** Provider-neutral synchronous experiment. The host owns persistence and effects. */
const judgments = new Set(['unknown', 'satisfied', 'work-needed']);
const scalarText = value => typeof value === 'string' && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value);
const nonblank = value => scalarText(value) && /[^\s\u0085]/u.test(value);
const integer = n => Number.isSafeInteger(n) && n >= 0;
export const emptyCheckpoint = () => ({ binding: '', evidence: '', disposition: 'unknown', validUntil: 0, pending: null, attempts: 0, settlement: null });
function sync(value) {
  if (value !== null && (typeof value === 'object' || typeof value === 'function') && typeof value.then === 'function') throw new Error('synchronous host required');
  return value;
}
function snapshot(value) { return Object.freeze(structuredClone(sync(value))); }
function validate(cp) {
  if (!scalarText(cp.binding) || !scalarText(cp.evidence) || !judgments.has(cp.disposition) || !integer(cp.validUntil) || !integer(cp.attempts) || (cp.pending !== null && !nonblank(cp.pending))) throw new Error('invalid checkpoint');
  if (cp.settlement !== null) {
    const r = cp.settlement;
    if (!r || typeof r !== 'object' || Object.keys(r).sort().join(',') !== 'attempt,binding,outcome,receipt' || Object.values(r).some(v => !nonblank(v)) || !['completed', 'not-applied'].includes(r.outcome)) throw new Error('invalid settlement record');
  }
}
function fresh(e, now) {
  if (!scalarText(e.identity) || typeof e.payload !== 'string' || typeof e.gap !== 'boolean' || !integer(e.observedAt) || !integer(e.validUntil) || !integer(now)) throw new Error('invalid observation or clock');
  return !e.gap && e.observedAt <= now && now < e.validUntil;
}
export function reconcile(binding, checkpoint, host, maxAttempts = 1) {
  validate(checkpoint);
  if (!nonblank(binding) || !integer(maxAttempts)) throw new Error('invalid binding or limit');
  const cp = structuredClone(checkpoint);
  const done = status => ({ checkpoint: cp, status });
  const save = () => sync(host.save(structuredClone(cp)));
  if (cp.pending) return done('recovery-needed');
  const first = snapshot(host.observe());
  const now = sync(host.clock());
  if (!fresh(first, now)) { cp.disposition = 'unknown'; save(); return done('evidence-gap'); }
  if (cp.binding === binding && cp.evidence === first.identity && cp.disposition === 'satisfied' && now < cp.validUntil) return done('reused');
  function judge(e) {
    const result = sync(host.assess(e));
    if (!judgments.has(result)) throw new Error('invalid assessment');
    const next = snapshot(host.observe());
    const stale = !fresh(e, sync(host.clock())) || !fresh(next, sync(host.clock())) || next.identity !== e.identity;
    return { result: stale ? 'unknown' : result, next, stale };
  }
  let judged = judge(first);
  Object.assign(cp, { binding, evidence: judged.next.identity, disposition: judged.result, validUntil: Math.min(first.validUntil, judged.next.validUntil) });
  save();
  if (judged.stale) return done('stale-assessment');
  if (judged.result !== 'work-needed') return done(judged.result);
  if (cp.attempts >= maxAttempts) return done('attempt-limit');
  const attempt = sync(host.newId());
  if (!nonblank(attempt)) throw new Error('invalid attempt identity');
  cp.pending = attempt; cp.attempts++; save();
  try { sync(host.act(first, attempt)); } catch { return done('action-outcome-unknown'); }
  cp.pending = null; cp.disposition = 'unknown'; save();
  const after = snapshot(host.observe());
  if (!fresh(after, sync(host.clock()))) return done('evidence-gap');
  judged = judge(after);
  Object.assign(cp, { evidence: judged.next.identity, disposition: judged.result, validUntil: Math.min(after.validUntil, judged.next.validUntil) });
  save();
  return done(judged.stale ? 'stale-assessment' : judged.result);
}
export function settlePending(checkpoint, binding, attempt, outcome, receipt) {
  validate(checkpoint);
  if (!attempt || checkpoint.pending !== attempt || checkpoint.binding !== binding || !['completed', 'not-applied'].includes(outcome) || !nonblank(receipt)) throw new Error('invalid settlement');
  return { ...structuredClone(checkpoint), pending: null, disposition: 'unknown', validUntil: 0, settlement: { binding, attempt, outcome, receipt } };
}
