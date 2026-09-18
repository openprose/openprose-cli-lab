/** Black-box alternating-runtime lifecycle: no providers, genuine files and subprocesses. */
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const seed=fileURLToPath(new URL('../', import.meta.url)), rust=process.argv[2];
if(!rust || !isAbsolute(rust)) throw Error('absolute native weave-rust-local binary required');
const root=mkdtempSync(join(tmpdir(),'weave-native-alternation-'));
let steps=0;
function call(native, command='step', expected=0) {
 const argv=native?[command,join(root,'config.json')]:['--no-env-file',join(seed,'local/run.mjs'),command,join(root,'config.json')];
 const result=spawnSync(native?rust:process.execPath,argv,{env:{},encoding:'utf8',timeout:10000,maxBuffer:1048576});
 assert.ifError(result.error);assert.equal(result.status,expected,result.stderr);
 if(command==='step')steps++;
 return expected===0?JSON.parse(result.stdout):result;
}
const config={schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['source.txt','report.txt'],
 capabilityVersion:'cross-runtime-offline-v1',assessor:[process.execPath,'--no-env-file',join(seed,'local/fixture.mjs'),'assess'],
 actor:[process.execPath,'--no-env-file',join(seed,'local/fixture.mjs'),'act'],environmentKeys:[],
 checkpointDirectory:'host',maxAttempts:102,ttlMs:60000,timeoutMs:3000,maxOutputBytes:65536};
const save=()=>writeFileSync(join(root,'config.json'),JSON.stringify(config));
try {
 for(const [name,value] of Object.entries({'kernel.md':'Synthetic lifecycle test','program.md':'Report equals source','source.txt':'initial','report.txt':''}))writeFileSync(join(root,name),value);
 save();
 assert.equal(call(false,'status').checkpoint,null);assert.equal(call(true,'status').checkpoint,null);assert.equal(existsSync(join(root,'host')),false);
 for(let i=0;i<100;i++) {
  writeFileSync(join(root,'source.txt'),`revision ${i} — 🧪\n`);
  const repaired=call(i%2===0);assert.deepEqual(repaired,{status:'satisfied',attempts:i+1,pending:null});
  const reused=call(i%2!==0);assert.deepEqual(reused,{status:'reused',attempts:i+1,pending:null});
  assert.equal(readFileSync(join(root,'report.txt'),'utf8'),`revision ${i} — 🧪\n`);
 }
 const calls=readFileSync(join(root,'calls.log'),'utf8').trim().split('\n');
 assert.equal(calls.filter(x=>x==='act').length,100);assert.equal(calls.filter(x=>x==='assess').length,200);
 config.maxOutputBytes=65537;save();assert.equal(call(false).status,'satisfied');assert.equal(call(true).status,'reused');
 config.maxOutputBytes=65538;save();assert.equal(call(true).status,'satisfied');assert.equal(call(false).status,'reused');
 rmSync(join(root,'source.txt'));assert.equal(call(true).status,'evidence-gap');assert.equal(call(false).status,'evidence-gap');
 writeFileSync(join(root,'source.txt'),'effect before failure');config.actor[config.actor.length-1]='fail';save();
 const failed=call(true);assert.equal(failed.status,'action-outcome-unknown');assert.equal(failed.attempts,101);assert.ok(failed.pending);
 assert.equal(readFileSync(join(root,'report.txt'),'utf8'),'effect before failure');
 const before=readFileSync(join(root,'calls.log'),'utf8');
 assert.equal(call(false).status,'recovery-needed');assert.equal(call(true).status,'recovery-needed');assert.equal(readFileSync(join(root,'calls.log'),'utf8'),before);
 assert.deepEqual(call(false,'status'),call(true,'status'));
 mkdirSync(join(root,'host/service.lock'));call(false,'step',1);call(true,'step',1);
 assert.ok(existsSync(join(root,'host/service.lock')));rmSync(join(root,'host/service.lock'),{recursive:true});
 console.log(`PASS ${steps} alternating-runtime steps: 100 actual repairs, 100 cross-runtime reuses, policy invalidation, gaps, pending effects and mutual owner exclusion`);
} finally {rmSync(root,{recursive:true,force:true});}
