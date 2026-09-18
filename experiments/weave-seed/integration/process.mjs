/** Bounded, shell-free synchronous process capabilities. No implicit provider. */
import { spawnSync } from 'node:child_process';
import { isAbsolute } from 'node:path';
export function processCapabilities({ assessor, actor, cwd, timeoutMs = 30000, maxOutputBytes = 1048576, environment = {} }) {
  for (const command of [assessor,actor]) {
    if (!Array.isArray(command) || !command.length || !command.every(s => typeof s === 'string') || !isAbsolute(command[0])) throw Error('explicit absolute executable and argument array required');
  }
  if (!isAbsolute(cwd) || ![timeoutMs,maxOutputBytes].every(n=>Number.isSafeInteger(n)&&n>0)) throw Error('invalid execution bounds');
  if (Object.values(environment).some(v=>typeof v!=='string')) throw Error('environment values must be strings');
  function run(command,evidence,attempt) {
    const input=JSON.stringify({schema:'openprose.weave-input/1',evidence,attempt:attempt??null})+'\n';
    const result=spawnSync(command[0],command.slice(1),{cwd,env:{...environment},input,encoding:'utf8',timeout:timeoutMs,killSignal:'SIGKILL',maxBuffer:maxOutputBytes,shell:false,stdio:['pipe','pipe','pipe']});
    // A timeout/error may follow side effects. The core preserves pending for actors.
    if(result.error || result.status!==0 || result.signal) throw Error('capability process failed or timed out');
    return result.stdout;
  }
  return {
    assess(evidence) {
      const text=run(assessor,evidence);
      if (!/^\s*\{\s*"judgment"\s*:\s*"(satisfied|work-needed|unknown)"\s*\}\s*$/.test(text)) throw Error('invalid assessor result');
      const value=JSON.parse(text);
      if(!value || typeof value!=='object' || Array.isArray(value) || Object.keys(value).join(',')!=='judgment' || !['satisfied','work-needed','unknown'].includes(value.judgment)) throw Error('invalid assessor result');
      return value.judgment;
    },
    act(evidence,attempt) {run(actor,evidence,attempt);},
  };
}
