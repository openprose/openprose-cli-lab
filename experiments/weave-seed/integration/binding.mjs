/** Explicit source binding. Reads bytes; never interprets Markdown. Trusted local host. */
import { openSync, closeSync, fstatSync, readSync, constants, realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
const digest = value => createHash('sha256').update(value).digest('hex');
const decode = new TextDecoder('utf-8', { fatal: true });
export function bindFiles({ root, kernel, contracts, evidence, policy, ttlMs = 60000, limit = 262144 }) {
  if (!Array.isArray(contracts) || !contracts.length || !Array.isArray(evidence) || !evidence.length || typeof kernel !== 'string' || typeof policy !== 'string' || !policy.trim()) throw Error('explicit kernel, contracts, evidence, and policy required');
  if (![ttlMs, limit].every(n => Number.isSafeInteger(n) && n > 0)) throw Error('invalid bounds');
  const base = realpathSync(root);
  const sources = [{role:'kernel', path:kernel}, ...contracts.map(path => ({role:'contract', path})), ...evidence.map(path => ({role:'evidence', path}))];
  if (sources.some(s => typeof s.path !== 'string' || !s.path)) throw Error('invalid source path');
  const declaration = sources.map(s => ({...s, path:resolve(base,s.path)}));
  const binding = digest(JSON.stringify({version:1, sources:declaration, policy, ttlMs, limit}));
  return { binding, observe(now = Date.now()) {
    if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(now+ttlMs)) throw Error('invalid time');
    let total = 0;
    try {
      const files = declaration.map(s => {
        const canonical = realpathSync(s.path);
        if (canonical !== base && !canonical.startsWith(base.endsWith('/') ? base : base + '/')) throw Error('source outside root');
        const fd = openSync(canonical, constants.O_RDONLY | constants.O_NONBLOCK);
        let bytes;
        try {
          const info = fstatSync(fd);
          if (!info.isFile() || info.size > limit-total) throw Error('source limit or non-file');
          // Allocate for actual input, not an arbitrarily large configured allowance.
          const chunks = []; let length = 0;
          for (;;) {
            const buffer = Buffer.alloc(Math.min(65536, limit-total+1-length));
            const count = readSync(fd, buffer, 0, buffer.length, null);
            if (!count) break;
            chunks.push(buffer.subarray(0,count)); length += count;
            if (length > limit-total) throw Error('aggregate source limit');
          }
          bytes = Buffer.concat(chunks,length);
        } finally { closeSync(fd); }
        total += bytes.length;
        if (total > limit) throw Error('aggregate source limit');
        return {...s, sha256:digest(bytes), content:decode.decode(bytes)};
      });
      const payload = JSON.stringify({version:1, policy, files});
      return {identity:digest(payload), payload, observedAt:now, validUntil:now+ttlMs, gap:false};
    } catch {
      const payload = JSON.stringify({version:1, error:'required-source-unavailable', policy});
      return {identity:digest(payload),payload,observedAt:now,validUntil:now+ttlMs,gap:true};
    }
  }};
}
