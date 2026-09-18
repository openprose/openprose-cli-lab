// Optional cross-runtime development checks; never required by the native runtime.
import {mkdtempSync,writeFileSync,readFileSync,rmSync,mkdirSync,existsSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {stepConfig,statusConfig,acquireOwner} from '../local/coordinator.mjs';
const here=fileURLToPath(new URL('.',import.meta.url));
const binary=process.argv[2]??join(here,'target/debug/weave-rust-local');
const fixture=process.argv[3]??join(dirname(binary),'weave-local-test-fixture');
const root=mkdtempSync(join(tmpdir(),'weave-native-local-interop-'));
let checks=0;
function assertEqual(a,b,label){assert.deepEqual(a,b,label);checks++;}
function native(command,config){const r=spawnSync(binary,[command,config],{encoding:'utf8',env:{WEAVE_B:'second',WEAVE_A:'first'},timeout:10000});assert.ifError(r.error);assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);}
try {
 for(const [name,value] of Object.entries({'kernel.md':'synthetic','program.md':'synthetic','source.txt':'one','report.txt':''})) writeFileSync(join(root,name),value);
 const path=join(root,'config.json');
 let config={schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['source.txt','report.txt'],capabilityVersion:'fixture-v1',assessor:[fixture,'assess'],actor:[fixture,'act'],checkpointDirectory:'host',maxAttempts:3,ttlMs:60000,timeoutMs:2000,maxOutputBytes:1048576,environmentKeys:['WEAVE_B','WEAVE_A']};
 process.env.WEAVE_B='second';process.env.WEAVE_A='first';
 const save=()=>writeFileSync(path,JSON.stringify(config));save();
 assertEqual(native('status',path),statusConfig(path),'readonly status');assertEqual(existsSync(join(root,'host')),false,'status creates no host');
 assertEqual(native('step',path).status,'satisfied','Rust repair');let calls=readFileSync(join(root,'calls.log'),'utf8');
 assertEqual(stepConfig(path).status,'reused','Bun reuses Rust binding and checkpoint');assertEqual(readFileSync(join(root,'calls.log'),'utf8'),calls,'Bun reuse no callbacks');
 assertEqual(native('status',path),statusConfig(path),'shared full checkpoint');
 writeFileSync(join(root,'source.txt'),'two');assertEqual(stepConfig(path).status,'satisfied','Bun repair');calls=readFileSync(join(root,'calls.log'),'utf8');
 assertEqual(native('step',path).status,'reused','Rust reuses Bun binding and checkpoint');assertEqual(readFileSync(join(root,'calls.log'),'utf8'),calls,'Rust reuse no callbacks');
 assertEqual(native('status',path),statusConfig(path),'reverse shared checkpoint');
 const owner=acquireOwner(path);try{const r=spawnSync(binary,['step',path],{encoding:'utf8',env:{},timeout:5000});assert.notEqual(r.status,0);checks++;assertEqual(statusConfig(path).serviceOwned,true,'Bun owner preserved');}finally{owner.release();}
 // Changing output policy must invalidate a prior positive cache in both runtimes.
 config.maxOutputBytes=1048575;save();assertEqual(native('step',path).status,'satisfied','Rust recognizes maxOutputBytes policy change');assertEqual(stepConfig(path).status,'reused','Bun agrees updated policy');
 config.actor=[fixture,'fail'];save();writeFileSync(join(root,'source.txt'),'three');assertEqual(native('step',path).status,'action-outcome-unknown','effect then failed native actor');calls=readFileSync(join(root,'calls.log'),'utf8');
 assertEqual(stepConfig(path).status,'recovery-needed','Bun preserves Rust pending');assertEqual(readFileSync(join(root,'calls.log'),'utf8'),calls,'no pending replay');assertEqual(native('status',path),statusConfig(path),'pending checkpoint codec agreement');
 console.log(`${checks} native Rust/Bun local interoperability checks passed`);
}finally{rmSync(root,{recursive:true,force:true});delete process.env.WEAVE_B;delete process.env.WEAVE_A;}
