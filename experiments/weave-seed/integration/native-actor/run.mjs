import { invokeNative, parseJSON, MAX_INPUT } from './actor.mjs';
export async function readInput(stream, { timeoutMs = 5000, limit = MAX_INPUT } = {}) {
  return new Promise((done, reject) => {
    let size = 0; const chunks = [];
    const cleanup = () => { clearTimeout(timer); stream.off('data', data); stream.off('end', end); stream.off('error', error); stream.pause(); };
    const error = () => { cleanup(); reject(Error('bounded actor input unavailable')); };
    const data = bytes => { size += bytes.length; if (size > limit) error(); else chunks.push(bytes); };
    const end = () => { cleanup(); try { done(parseJSON(Buffer.concat(chunks))); } catch { reject(Error('invalid actor input')); } };
    const timer = setTimeout(error, timeoutMs);
    stream.on('data', data); stream.on('end', end); stream.on('error', error);
  });
}
export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 2 || argv[0] !== '--config') throw Error('usage: native-actor/run.mjs --config ABSOLUTE_CONFIG');
  const cancellation = new AbortController(), stop = () => cancellation.abort();
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
  try {
    let input;
    try { input = await readInput(process.stdin); }
    catch { await invokeNative(argv[1], null, { signal: cancellation.signal }); throw Error('invalid actor input'); }
    console.log(JSON.stringify(await invokeNative(argv[1], input, { signal: cancellation.signal })));
  } finally { process.off('SIGINT', stop); process.off('SIGTERM', stop); }
}
if (import.meta.main) main().catch(() => { console.error('Native actor failed; no fulfillment claimed and effects may remain uncertain.'); process.exitCode = 1; });
