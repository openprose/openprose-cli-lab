/** Run with Bun and a locally built Rust host binary. No models or network. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync,writeFileSync,rmSync } from 'node:fs';
import { join,isAbsolute } from 'node:path';
import { tmpdir } from 'node:os';
import { FileHost,encodeCheckpoint,decodeCheckpoint } from '../bun/host.mjs';
const executable=process.argv[2];if(!executable||!isAbsolute(executable))throw Error('absolute Rust host binary path required');
const root=mkdtempSync(join(tmpdir(),'weave-interchange-')),host=new FileHost(join(root,'host'));
const rust=(...args)=>spawnSync(executable,args,{encoding:'utf8',timeout:3000,killSignal:'SIGKILL',maxBuffer:1048576});
try{
 for(let i=0;i<100;i++){
  const cp={binding:`contract-${i}-λ`,evidence:`record-${i}\n🧪`,disposition:['unknown','satisfied','work-needed'][i%3],validUntil:i%2?9007199254740991:0,pending:i%2?`attempt-${i}`:null,attempts:i,settlement:i%3?null:{binding:'b',attempt:'prior',outcome:i%2?'completed':'not-applied',receipt:'receipt-α'}};
  host.withLock(s=>s.save(cp));let result=rust('load',host.directory);assert.equal(result.status,0,result.stderr);assert.deepEqual(decodeCheckpoint(result.stdout),cp);
  const fixture=join(root,'fixture.json');writeFileSync(fixture,encodeCheckpoint(cp));result=rust('save-fixture',host.directory,fixture);assert.equal(result.status,0,result.stderr);assert.deepEqual(host.withLock(s=>s.load()),cp);
 }
 const base=JSON.parse(encodeCheckpoint({binding:'b',evidence:'e',disposition:'unknown',validUntil:0,pending:'pending',attempts:1,settlement:null}));
 const invalid=[JSON.stringify({...base,extra:true}),JSON.stringify({...base,schema:2}),JSON.stringify({...base,pending:''}),JSON.stringify({...base,attempts:true}),JSON.stringify({...base,validUntil:9007199254740992}),'{',JSON.stringify(base).replace('"attempts":1','"attempts":1.0'),JSON.stringify(base).replace('"attempts":1','"attempts":1e0'),JSON.stringify(base).replace('"attempts":1','"attempts":-0'),JSON.stringify(base).replace('"pending":"pending"','"pending":"pending","pending":null'),'\uFEFF'+JSON.stringify(base)];
 for(const field of Object.keys(base)){const missing={...base};delete missing[field];invalid.push(JSON.stringify(missing));}
 for(const raw of invalid){writeFileSync(host.checkpointPath,raw);assert.throws(()=>host.withLock(s=>s.load()));const result=rust('load',host.directory);assert.notEqual(result.status,0,raw);}
 console.log(`PASS 100 bidirectional checkpoint round trips; ${invalid.length} shared malformed checkpoint rejections`);
}finally{rmSync(root,{recursive:true,force:true});}
