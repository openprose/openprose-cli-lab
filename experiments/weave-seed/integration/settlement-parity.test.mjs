/** Actual sidecar recovery across ports; synthetic effects and no provider calls. */
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const seed=fileURLToPath(new URL('../',import.meta.url)), rust=process.argv[2];
if(!rust || !isAbsolute(rust))throw Error('absolute native coordinator binary required');
let assertions=0;
for(const native of [false,true])for(const outcome of ['completed','not-applied']) {
 const root=mkdtempSync(join(tmpdir(),'weave-settlement-'));
 const configPath=join(root,'config.json'), checkpointPath=join(root,'host/checkpoint.json');
 const fixture=[process.execPath,'--no-env-file',join(seed,'local/fixture.mjs')];
 const config={schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['source.txt','report.txt'],capabilityVersion:'settlement-test-v1',
  assessor:[...fixture,'assess'],actor:outcome==='completed'?[...fixture,'fail']:[process.execPath,'--no-env-file','-e','process.exit(1)'],
  environmentKeys:[],checkpointDirectory:'host',maxAttempts:3,ttlMs:60000,timeoutMs:3000};
 const save=()=>writeFileSync(configPath,JSON.stringify(config));
 const call=(port,command,args=[],expected=0)=>{
  const result=spawnSync(port?rust:process.execPath,port?[command,configPath,...args]:['--no-env-file',join(seed,'local/run.mjs'),command,configPath,...args],{env:{},encoding:'utf8',timeout:10000});
  assert.ifError(result.error);assert.equal(result.status,expected,result.stderr);assertions++;
  return expected===0?JSON.parse(result.stdout):result;
 };
 try {
  for(const [name,value] of Object.entries({'kernel.md':'Synthetic kernel','program.md':'Report equals source','source.txt':'new','report.txt':''}))writeFileSync(join(root,name),value);
  save();
  const failed=call(native,'step');assert.equal(failed.status,'action-outcome-unknown');assert.ok(failed.pending);
  const checkpoint=JSON.parse(readFileSync(checkpointPath,'utf8')), before=readFileSync(checkpointPath,'utf8');
  const effects=readFileSync(join(root,'report.txt'),'utf8'), calls=readFileSync(join(root,'calls.log'),'utf8');
  assert.equal(effects,outcome==='completed'?'new':'');
  // Recovery does not need provider credentials, readable evidence, or runnable capabilities.
  config.environmentKeys=['MISSING_FOR_RECOVERY_TEST'];save();rmSync(join(root,'kernel.md'));
  const args=['--binding',checkpoint.binding,'--attempt',checkpoint.pending,'--outcome',outcome,'--receipt','local-investigation:synthetic-effect-inspected'];
  for(const port of [native,!native]) {
   const wrong=[...args];wrong[3]='wrong-attempt';call(port,'settle',wrong,1);
   const badReceipt=[...args];badReceipt[7]=' ';call(port,'settle',badReceipt,1);
   mkdirSync(join(root,'host/service.lock'));call(port,'settle',args,1);rmSync(join(root,'host/service.lock'),{recursive:true});
   mkdirSync(join(root,'host/lock'));call(port,'settle',args,1);rmSync(join(root,'host/lock'),{recursive:true});
   assert.equal(readFileSync(checkpointPath,'utf8'),before);
  }
  assert.deepEqual(call(!native,'settle',args),{status:'settled',attempts:1,pending:null});
  const settled=JSON.parse(readFileSync(checkpointPath,'utf8'));
  assert.equal(settled.disposition,'unknown');assert.equal(settled.validUntil,0);assert.equal(settled.settlement.outcome,outcome);
  assert.equal(readFileSync(join(root,'calls.log'),'utf8'),calls);assert.equal(readFileSync(join(root,'report.txt'),'utf8'),effects);
  call(native,'settle',args,1); // No replay or duplicate settlement.
  config.environmentKeys=[];config.actor=[...fixture,'act'];save();writeFileSync(join(root,'kernel.md'),'Synthetic kernel');
  const resumed=call(native,'step');assert.equal(resumed.status,'satisfied');assert.equal(resumed.attempts,outcome==='completed'?1:2);
  assert.equal(call(!native,'step').status,'reused');
 }finally{rmSync(root,{recursive:true,force:true});}
}
console.log(`PASS ${assertions} sidecar recovery invocations: both outcomes, both directions, preserved budgets, lock exclusion and no calls during settlement`);
