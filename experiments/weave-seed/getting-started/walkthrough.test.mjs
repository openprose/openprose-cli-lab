import {test} from 'node:test';
import assert from 'node:assert/strict';
import {realpathSync,cpSync,mkdirSync,mkdtempSync,readFileSync,writeFileSync,rmSync,existsSync,symlinkSync,statSync} from 'node:fs';
import {join,dirname,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const here=dirname(fileURLToPath(import.meta.url));
function run(args,input) {
 const result=spawnSync(process.execPath,['--no-env-file',...args],{env:{},input,encoding:'utf8',timeout:10000,maxBuffer:1048576});
 assert.ifError(result.error);return result;
}
function ok(result){assert.equal(result.status,0,result.stderr);assert.equal(result.stderr,'');return result.stdout.trim().split('\n').filter(Boolean).map(s=>JSON.parse(s));}
const calls=root=>readFileSync(join(root,'calls.log'),'utf8');
test('new developer uses real create/status/step/serve processes, repair, reuse and missing input',()=>{
 const parent=mkdtempSync(join(tmpdir(),'weave-walkthrough-'));
 try {
  const destination=join(parent,"example with 'spaces'"),[created]=ok(run([join(here,'create.mjs'),destination]));
  assert.equal(created.kind,'synthetic-no-network');assert.equal(statSync(created.root).mode&0o777,0o700);
  const cli=resolve(here,'../local/run.mjs'),invoke=(...args)=>ok(run([cli,...args]));
  const initial=invoke('status',created.config)[0];assert.equal(initial.checkpoint,null);assert.equal(initial.serviceOwned,false);assert.ok(!existsSync(join(created.root,'host')));
  assert.deepEqual(invoke('step',created.config)[0],{status:'satisfied',attempts:1,pending:null});assert.equal(readFileSync(join(created.root,'report.txt'),'utf8'),'one\n');
  const firstCalls=calls(created.root);assert.equal(firstCalls,'assess\nact\nassess\n');
  assert.deepEqual(invoke('step',created.config)[0],{status:'reused',attempts:1,pending:null});assert.equal(calls(created.root),firstCalls);
  const unchanged=invoke('serve',created.config,'--poll-ms','1','--max-steps','3');assert.equal(unchanged.length,4);assert.ok(unchanged.slice(0,3).every(v=>v.status==='reused'));assert.deepEqual(unchanged[3],{stopped:'step-limit',steps:3});assert.equal(calls(created.root),firstCalls);
  writeFileSync(join(created.root,'source.txt'),'two\n');const changed=invoke('serve',created.config,'--poll-ms','1','--max-steps','2');assert.equal(changed[0].status,'satisfied');assert.equal(changed[0].attempts,2);assert.equal(changed[1].status,'reused');assert.equal(readFileSync(join(created.root,'report.txt'),'utf8'),'two\n');
  const beforeGap=calls(created.root);rmSync(join(created.root,'source.txt'));assert.equal(invoke('step',created.config)[0].status,'evidence-gap');assert.equal(calls(created.root),beforeGap);
  const final=invoke('status',created.config)[0];assert.equal(final.checkpoint.attempts,2);assert.equal(final.serviceOwned,false);assert.equal(final.checkpointLocked,false);
  const config=JSON.parse(readFileSync(created.config));assert.deepEqual(config.environmentKeys,[]);assert.equal(config.actor[1],'--no-env-file');assert.equal(config.assessor[1],'--no-env-file');
 }finally{rmSync(parent,{recursive:true,force:true});}
});
test('setup refuses existing directory or symlink without overwriting user files',()=>{
 const parent=mkdtempSync(join(tmpdir(),'weave-walkthrough-refusal-'));
 try {
  const destination=join(parent,'new');ok(run([join(here,'create.mjs'),destination]));const before=readFileSync(join(destination,'config.json'));
  const again=run([join(here,'create.mjs'),destination]);assert.equal(again.status,1);assert.equal(again.stdout,'');assert.deepEqual(readFileSync(join(destination,'config.json')),before);
  const linked=join(parent,'linked');symlinkSync(destination,linked);assert.equal(run([join(here,'create.mjs'),linked]).status,1);
  assert.equal(run([join(here,'create.mjs'),'relative-example']).status,1);
 }finally{rmSync(parent,{recursive:true,force:true});}
});

test('copied source package creates and runs an example without resolving original checkout',()=>{
 const parent=mkdtempSync(join(tmpdir(),'weave-copied-walkthrough-'));
 try {
  const copied=join(realpathSync(parent),'copied-package');
  const files=['getting-started/create.mjs','local/fixture.mjs','local/run.mjs','local/check.mjs','local/coordinator.mjs','integration/run.mjs','integration/config.mjs','integration/binding.mjs','integration/process.mjs','bun/index.mjs','bun/host.mjs'];
  for(const file of files) {const target=join(copied,file);mkdirSync(dirname(target),{recursive:true});cpSync(resolve(here,'..',file),target);}
  const [created]=ok(run([join(copied,'getting-started/create.mjs'),join(parent,'consumer')]));
  const config=JSON.parse(readFileSync(created.config));assert.ok(config.actor[2].startsWith(copied));assert.ok(config.assessor[2].startsWith(copied));
  const cli=join(copied,'local/run.mjs');assert.equal(ok(run([cli,'step',created.config]))[0].status,'satisfied');assert.equal(ok(run([cli,'step',created.config]))[0].status,'reused');
 }finally{rmSync(parent,{recursive:true,force:true});}
});
