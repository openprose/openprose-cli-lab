/** Native CLI transport adapter. Does not interpret Markdown or assess fulfillment. */
import { openSync, closeSync, fstatSync, readSync, realpathSync, constants, lstatSync, writeFileSync, fsyncSync } from 'node:fs';
import { resolve, dirname, isAbsolute, relative } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
export const MAX_INPUT = 1048576;
const MAX_FILE = 262144;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const fail = () => { throw Error('native actor rejected input, configuration, or evidence'); };
const integer = (n, min = 1, max = 9007199254740991) => Number.isSafeInteger(n) && n >= min && n <= max;
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const text = value => typeof value === 'string' && value.trim() && !value.includes('\0');
const exact = (value, keys) => value && !Array.isArray(value) && typeof value === 'object' && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
export function parseJSON(bytes) {
  const source = typeof bytes === 'string' ? bytes : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  const result = JSON.parse(source), frames = [];
  for (const [token] of source.matchAll(/"(?:\\.|[^"\\])*"|[{}\[\],:]|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g)) {
    const top = frames.at(-1);
    if (token === '{') frames.push({ keys: new Set(), key: true });
    else if (token === '[') frames.push({});
    else if (token === '}' || token === ']') frames.pop();
    else if (token === ',' && top?.keys) top.key = true;
    else if (token.startsWith('"')) { const value = JSON.parse(token); if (/\p{Surrogate}/u.test(value)) fail(); if (top?.keys && top.key) { if (top.keys.has(value)) fail(); top.keys.add(value); top.key = false; } }
    else if (/^-?\d/.test(token) && !Number.isFinite(Number(token))) fail();
  }
  return result;
}
export function boundedFile(path, limit = MAX_FILE) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NONBLOCK);
  try {
    const info = fstatSync(fd); if (!info.isFile() || info.size > limit) fail();
    const buffer = Buffer.alloc(Math.min(info.size + 1, limit + 1)); let size = 0, count;
    while (size < buffer.length && (count = readSync(fd, buffer, size, buffer.length - size, null)) > 0) size += count;
    if (size > limit || size > info.size) fail(); return buffer.subarray(0, size);
  } finally { closeSync(fd); }
}
export function loadConfig(path) {
  if (!isAbsolute(path)) fail();
  const configPath = realpathSync(path), bytes = boundedFile(configPath), c = parseJSON(bytes);
  const required = ['schema', 'executable', 'executableSha256', 'cwd', 'kernel', 'task', 'expectedImageSha256', 'harness', 'authProfile', 'model', 'environmentKeys'];
  const optional = ['maxTurns', 'nativeTimeoutMs', 'toolTimeoutMs', 'outerTimeoutMs', 'processTimeoutMs', 'readinessTimeoutMs', 'maxCaptureBytes', 'receiptDirectory'];
  if (!c || typeof c !== 'object' || required.some(k => !(k in c)) || Object.keys(c).some(k => ![...required, ...optional].includes(k)) || c.schema !== 1) fail();
  if (!isAbsolute(c.executable) || !sha(c.executableSha256) || !sha(c.expectedImageSha256) || ![c.cwd, c.kernel, c.task, c.model].every(text)) fail();
  if (c.harness !== 'agents-sdk' || c.authProfile !== 'openai-api-key') fail();
  for (const name of ['kernel', 'task']) if (isAbsolute(c[name]) || c[name].split(/[\\/]/).some(part => !part || part === '.' || part === '..')) fail();
  if (!Array.isArray(c.environmentKeys) || new Set(c.environmentKeys).size !== c.environmentKeys.length || c.environmentKeys.some(k => typeof k !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(k))) fail();
  const config = { maxTurns: 8, nativeTimeoutMs: 120000, toolTimeoutMs: 15000, outerTimeoutMs: 150000, processTimeoutMs: 165000, readinessTimeoutMs: 45000, maxCaptureBytes: 4194304, ...c };
  if (!integer(config.maxTurns, 1, 100) || !integer(config.maxCaptureBytes, 1024, 16777216) || !['nativeTimeoutMs', 'toolTimeoutMs', 'outerTimeoutMs', 'processTimeoutMs', 'readinessTimeoutMs'].every(k => integer(config[k], 1, 3600000))) fail();
  if (!(config.toolTimeoutMs <= config.nativeTimeoutMs && config.nativeTimeoutMs < config.outerTimeoutMs && config.outerTimeoutMs < config.processTimeoutMs)) fail();
  receiptTarget(config.receiptDirectory);
  const cwd = realpathSync(resolve(dirname(configPath), config.cwd));
  return { ...config, cwd, configPath, configDigest: hash(bytes), kernelPath: resolve(cwd, config.kernel), taskPath: resolve(cwd, config.task) };
}
export function validateSnapshot(c, envelope, now = Date.now()) {
  if (!exact(envelope, ['schema', 'evidence', 'attempt']) || envelope.schema !== 'openprose.weave-input/1' || typeof envelope.attempt !== 'string' || !/^[A-Za-z0-9_.-]{1,120}$/.test(envelope.attempt)) fail();
  const e = envelope.evidence;
  if (!exact(e, ['identity', 'payload', 'observedAt', 'validUntil', 'gap']) || e.gap !== false || !text(e.payload) || Buffer.byteLength(e.payload) > MAX_INPUT || !integer(e.observedAt, 0) || !integer(e.validUntil, 0) || !(e.observedAt <= now && now < e.validUntil) || hash(e.payload) !== e.identity) fail();
  const payload = parseJSON(e.payload);
  if (!exact(payload, ['version', 'policy', 'files']) || payload.version !== 1 || !text(payload.policy) || !Array.isArray(payload.files) || !payload.files.length || payload.files.length > 256) fail();
  const seen = new Set(); let total = 0;
  for (const f of payload.files) {
    if (!exact(f, ['role', 'path', 'sha256', 'content']) || !['kernel', 'contract', 'evidence'].includes(f.role) || !isAbsolute(f.path) || typeof f.content !== 'string' || !sha(f.sha256)) fail();
    const key = `${f.role}:${f.path}`; if (seen.has(key)) fail(); seen.add(key);
    const rel = relative(c.cwd, realpathSync(f.path)); if (rel === '..' || rel.startsWith('../') || isAbsolute(rel)) fail();
    const bytes = Buffer.from(f.content); total += bytes.length;
    if (total > MAX_FILE || hash(bytes) !== f.sha256 || !boundedFile(f.path).equals(bytes)) fail();
  }
  if (payload.files.filter(f => f.role === 'kernel').length !== 1 || !seen.has(`kernel:${c.kernelPath}`) || !seen.has(`contract:${c.taskPath}`) || !seen.has(`evidence:${c.configPath}`)) fail();
  if (hash(boundedFile(c.configPath)) !== c.configDigest || hash(boundedFile(c.executable, 536870912)) !== c.executableSha256) fail();
  return payload.files.find(f => f.role === 'kernel').sha256;
}
export function command(c) {
  return [c.executable, '--harness', c.harness, '--auth-profile', c.authProfile, '--model', c.model,
    '--native-max-turns', String(c.maxTurns), '--native-timeout', `${c.nativeTimeoutMs}ms`, '--native-tool-timeout', `${c.toolTimeoutMs}ms`,
    '--timeout', `${c.outerTimeoutMs}ms`, '--cwd', c.cwd, '--output-contract', 'native'];
}
/** Combined bounded stdout/stderr. POSIX process-group termination is best effort. */
export function runProcess(argv, { cwd, environment, timeoutMs, maxCaptureBytes, signal }) {
  return new Promise((done, reject) => {
    if (signal?.aborted) return reject(Error('native actor cancelled'));
    const child = spawn(argv[0], argv.slice(1), { cwd, env: environment, shell: false, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = []; let size = 0, failure = false;
    const kill = () => { failure = true; try { if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGKILL'); else child.kill('SIGKILL'); } catch {} cleanup(); child.stdout.destroy(); child.stderr.destroy(); child.unref(); reject(Error('native CLI failed, cancelled, or exceeded bounds; effects may be uncertain')); };
    const timer = setTimeout(kill, timeoutMs);
    signal?.addEventListener('abort', kill, { once: true });
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', kill); };
    for (const [stream, output] of [[child.stdout, true], [child.stderr, false]]) stream.on('data', bytes => {
      size += bytes.length; if (size > maxCaptureBytes) kill(); else if (output) chunks.push(bytes);
    });
    child.once('error', () => { cleanup(); reject(Error('native CLI process unavailable')); });
    child.once('close', (code, termination) => { cleanup(); if (failure || code !== 0 || termination) reject(Error('native CLI failed, cancelled, or exceeded bounds; effects may be uncertain')); else done(Buffer.concat(chunks)); });
  });
}
function limits(c) { return { maxTurns: c.maxTurns, timeoutSeconds: c.nativeTimeoutMs / 1000, toolTimeoutSeconds: c.toolTimeoutMs / 1000, maxOutputTokens: 12000 }; }
function sameLimits(value, c) { const expected = limits(c); return exact(value, Object.keys(expected)) && Object.entries(expected).every(([key, v]) => value[key] === v); }
function receiptTarget(path) {
  if (path === undefined || path === null) return null;
  if (typeof path !== 'string' || !isAbsolute(path) || realpathSync(path) !== path) fail();
  const info = lstatSync(path);
  if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o777) !== 0o700) fail();
  return { path, dev: info.dev, ino: info.ino };
}
function saveReceipt(target, record) {
  if (!target) return;
  const current = receiptTarget(target.path);
  if (current.dev !== target.dev || current.ino !== target.ino) throw Error('NATIVE_ACTOR_RECEIPT_FAILED');
  const bytes = Buffer.from(JSON.stringify(record) + '\n');
  if (bytes.length > 65536) throw Error('NATIVE_ACTOR_RECEIPT_FAILED');
  const fd = openSync(resolve(target.path, `native-actor-${randomUUID()}.json`), 'wx', 0o600);
  try { writeFileSync(fd, bytes); fsyncSync(fd); } finally { closeSync(fd); }
}
export async function invokeNative(configPath, envelope, { signal, processRunner = runProcess, ambient = process.env } = {}) {
  // A malformed/unreadable config cannot supply a trusted receipt destination.
  let target = null;
  const record = { schema: 'openprose.native-actor-receipt/1', startedAt: Date.now(), phase: 'config', status: 'failed', errorCode: null, attempt: typeof envelope?.attempt === 'string' && /^[A-Za-z0-9_.-]{1,120}$/.test(envelope.attempt) ? envelope.attempt : null, actionLaunched: false };
  try {
    if (!isAbsolute(configPath)) fail();
    const configBytes = boundedFile(configPath), rawConfig = parseJSON(configBytes);
    target = receiptTarget(rawConfig?.receiptDirectory);
    record.configSha256 = hash(configBytes);
    const c = loadConfig(configPath);
    if (c.configDigest !== record.configSha256) fail();
    Object.assign(record, { executableSha256: c.executableSha256, expectedImageSha256: c.expectedImageSha256, model: c.model, harness: c.harness });
    record.phase = 'input';
    const kernel = validateSnapshot(c, envelope); record.kernelSha256 = kernel;
    const environment = Object.create(null);
    for (const key of c.environmentKeys) { if (typeof ambient[key] !== 'string') fail(); environment[key] = ambient[key]; }
    const argv = command(c), options = { cwd: c.cwd, environment, maxCaptureBytes: c.maxCaptureBytes, signal };
    record.phase = 'readiness';
    const ready = parseJSON(await processRunner([...argv, '--dry-run', '--output', 'json', 'run', c.task], { ...options, timeoutMs: c.readinessTimeoutMs }));
    if (ready.readiness !== 'ready' || ready.wouldStartModel !== false || ready.selection?.harness !== c.harness || ready.selection?.model !== c.model || ready.selection?.adapterId !== 'agents-sdk/jsonl' || ready.billingOwner !== 'user-provider' || ready.prompt?.placement !== 'system-append' || ready.prompt?.strictness !== 'strict' || ready.cwd !== c.cwd || ready.languageImage?.sha256 !== c.expectedImageSha256 || !sameLimits(ready.nativeLimits, c)) fail();
    record.phase = 'revalidate';
    validateSnapshot(c, envelope);
    record.phase = 'run'; record.actionLaunched = true;
    const bytes = await processRunner([...argv, '--output', 'jsonl', 'run', c.task], { ...options, timeoutMs: c.processTimeoutMs });
    record.phase = 'completion';
    const events = new TextDecoder('utf-8', { fatal: true }).decode(bytes).split('\n').filter(line => line.trim()).map(parseJSON);
    const completed = events.filter(e => e.type === 'runner.completed');
    if (completed.length !== 1) fail();
    const result = completed[0].payload?.result;
    if (!result || result.terminal?.classification !== 'success' || result.runnerExitCode !== 0 || result.languageImage?.sha256 !== c.expectedImageSha256 || result.digests?.deliveredImageSha256 !== kernel || !sameLimits(result.nativeLimits, c)) fail();
    record.status = 'native-completed';
    return { status: 'native-completed', attempt: envelope.attempt, deliveredKernelSha256: kernel, acceptance: 'requires fresh observation and assessment' };
  } catch {
    record.errorCode = `NATIVE_ACTOR_${record.phase.toUpperCase()}_FAILED`;
    throw Error(record.errorCode);
  } finally {
    record.finishedAt = Date.now();
    try { saveReceipt(target, record); } catch { throw Error('NATIVE_ACTOR_RECEIPT_FAILED'); }
  }
}
