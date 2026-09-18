/** Experimental single-step process host; configuration is trusted executable policy. */
import { readFileSync } from 'node:fs';
import { resolve,dirname } from 'node:path';
import { randomUUID,createHash } from 'node:crypto';
import { FileHost } from '../bun/host.mjs';
import { bindFiles } from './binding.mjs';
import { processCapabilities } from './process.mjs';
export function runConfig(path) {
  const configPath=resolve(path), config=JSON.parse(readFileSync(configPath,'utf8'));
  if(config.schema!==1 || typeof config.capabilityVersion!=='string' || !config.capabilityVersion.trim() || !Number.isSafeInteger(config.maxAttempts) || config.maxAttempts<0 || typeof config.checkpointDirectory!=='string')throw Error('invalid explicit configuration');
  const root=resolve(dirname(configPath),config.root??'.');
  const capabilities=processCapabilities({...config,cwd:root});
  const policy=createHash('sha256').update(JSON.stringify({version:config.capabilityVersion,assessor:config.assessor,actor:config.actor,environment:config.environment??{},timeoutMs:config.timeoutMs??30000})).digest('hex');
  const bound=bindFiles({...config,root,policy});
  const host=new FileHost(resolve(dirname(configPath),config.checkpointDirectory));
  return host.step(bound.binding,{...capabilities,observe:()=>bound.observe(Date.now()),clock:Date.now,newId:randomUUID},config.maxAttempts);
}
if(import.meta.main){
  try {
    if(process.argv.length!==3)throw Error('usage: bun integration/run.mjs CONFIG.json');
    const {checkpoint,status}=runConfig(process.argv[2]);
    console.log(JSON.stringify({status,attempts:checkpoint.attempts,pending:checkpoint.pending}));
  } catch(error){console.error(error.message);process.exitCode=1;}
}
