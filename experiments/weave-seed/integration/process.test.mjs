import assert from 'node:assert/strict';
import { mkdtempSync,writeFileSync,readFileSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { processCapabilities } from './process.mjs';
import { reconcile,emptyCheckpoint } from '../bun/index.mjs';
const root=mkdtempSync(join(tmpdir(),'weave-process-'));
try {
 const child=join(root,'child.mjs');writeFileSync(child,`import {writeFileSync} from 'node:fs';let text='';for await(const chunk of process.stdin)text+=chunk;const v=JSON.parse(text);if(process.argv[2]==='assess')console.log(JSON.stringify({judgment:'work-needed'}));else {writeFileSync('effect.txt',v.attempt);process.exit(3);}`);
 const capabilities=processCapabilities({assessor:[process.execPath,child,'assess'],actor:[process.execPath,child,'act'],cwd:root});
 const evidence={identity:'a',payload:'input data',observedAt:0,validUntil:100,gap:false};let saved;
 const host={...capabilities,observe:()=>evidence,clock:()=>1,newId:()=> 'attempt-1',save:cp=>saved=structuredClone(cp)};
 const result=reconcile('fixture',emptyCheckpoint(),host);
 assert.equal(result.status,'action-outcome-unknown');assert.equal(saved.pending,'attempt-1');assert.equal(readFileSync(join(root,'effect.txt'),'utf8'),'attempt-1');
 assert.equal(reconcile('fixture',saved,host).status,'recovery-needed');
 assert.throws(()=>processCapabilities({assessor:['relative'],actor:['relative'],cwd:root}));
 const hang=join(root,'hang.mjs');writeFileSync(hang,'setInterval(()=>{},1000)');
 const bounded=processCapabilities({assessor:[process.execPath,hang],actor:[process.execPath,hang],cwd:root,timeoutMs:50});assert.throws(()=>bounded.assess(evidence),/failed or timed out/);
 const noisy=join(root,'noisy.mjs');writeFileSync(noisy,`process.stdout.write('x'.repeat(80));process.stderr.write('y'.repeat(80));`);
 const combined=processCapabilities({assessor:[process.execPath,noisy],actor:[process.execPath,noisy],cwd:root,maxOutputBytes:100});assert.throws(()=>combined.act(evidence,'noisy-attempt'),/failed or timed out/);
 assert.throws(()=>processCapabilities({assessor:[process.execPath,'bad\0argument'],actor:[process.execPath,noisy],cwd:root}));
 const duplicate=join(root,'duplicate.mjs');writeFileSync(duplicate,`console.log('{"judgment":"unknown","judgment":"satisfied"}')`);
 assert.throws(()=>processCapabilities({assessor:[process.execPath,duplicate],actor:[process.execPath,duplicate],cwd:root}).assess(evidence),/invalid assessor/);
 writeFileSync(hang,"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)");
 const started=Date.now();assert.throws(()=>bounded.assess(evidence));assert.ok(Date.now()-started<2000);
 console.log('PASS process boundary: shell-free argv, failed actor with actual effect retains pending, restart blocks replay, timeout and invalid command rejection');
} finally {rmSync(root,{recursive:true,force:true});}
