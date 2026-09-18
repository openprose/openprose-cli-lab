import { checkConfig } from './check.mjs';
import { stepConfig, serveConfig, statusConfig, settleConfig } from './coordinator.mjs';
const project = result => ({ status: result.status, attempts: result.checkpoint.attempts, pending: result.checkpoint.pending });
export async function main(argv = process.argv.slice(2)) {
  if(argv.length===1 && ['--help','-h','help'].includes(argv[0])) {
    console.log('Experimental local weave (Bun):\n  check CONFIG  Offline file/executable/environment/checkpoint inspection; no provider verification\n  status CONFIG  Read-only checkpoint and lock diagnostics\n  step CONFIG  One bounded reconciliation (may invoke configured providers/actions)\n  serve CONFIG --poll-ms N --max-steps N  Bounded sequential polling\n  settle CONFIG --binding VALUE --attempt VALUE --outcome completed|not-applied --receipt VALUE  Explicit trusted pending-effect settlement\nOutput: JSON records except this help. Check exits 0 when configured, 2 when blocked.\nFor Bun source and adapter invocations, use --no-env-file. Pending effects require trusted reconciliation.');
    return;
  }
  const [command, config, ...options] = argv;
  if(command==='check' && config && !options.length) {
    const result=checkConfig(config);console.log(JSON.stringify(result));if(result.status==='blocked')process.exitCode=2;return;
  }
  if (!config) throw Error('usage: check CONFIG | step CONFIG | status CONFIG | serve CONFIG --poll-ms N --max-steps N (or --help)');
  if (command === 'status' && !options.length) return console.log(JSON.stringify(statusConfig(config)));
  if (command === 'step' && !options.length) return console.log(JSON.stringify(project(stepConfig(config))));
  if (command === 'settle') {
    if (options.length !== 8 || options[0] !== '--binding' || options[2] !== '--attempt' || options[4] !== '--outcome' || options[6] !== '--receipt' || !['completed', 'not-applied'].includes(options[5])) throw Error('explicit settle --binding VALUE --attempt VALUE --outcome completed|not-applied --receipt VALUE required');
    const checkpoint = settleConfig(config, options[1], options[3], options[5], options[7]);
    return console.log(JSON.stringify(project({status: 'settled', checkpoint})));
  }
  if (command !== 'serve' || options.length !== 4 || options[0] !== '--poll-ms' || options[2] !== '--max-steps' || ![options[1], options[3]].every(v => /^[1-9][0-9]*$/.test(v))) throw Error('explicit serve --poll-ms N --max-steps N required');
  const cancellation = new AbortController();
  const stop = () => cancellation.abort();
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
  try {
    const result = await serveConfig(config, { pollMs: Number(options[1]), maxSteps: Number(options[3]), signal: cancellation.signal, onStep: result => console.log(JSON.stringify(project(result))) });
    console.log(JSON.stringify({ stopped: result.stopped, steps: result.steps }));
  } finally { process.off('SIGINT', stop); process.off('SIGTERM', stop); }
}
if (import.meta.main) main().catch(error => { console.error(error.message); process.exitCode = 1; });
