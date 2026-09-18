import assert from 'node:assert/strict';
import { mkdtempSync,writeFileSync,readFileSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runConfig } from './run.mjs';
const root=mkdtempSync(join(tmpdir(),'weave-step-'));
try{
 for(const [name,value] of Object.entries({'kernel.md':'Synthetic test kernel, not the OpenProse kernel.','program.md':'State must be ready and each batch requires a report.','state.txt':'ready','batch.txt':'batch-1','report.txt':''}))writeFileSync(join(root,name),value);
 const child=join(root,'capability.mjs');writeFileSync(child,`import {readFileSync,writeFileSync} from 'node:fs';let text='';for await(const c of process.stdin)text+=c;const input=JSON.parse(text);const payload=JSON.parse(input.evidence.payload);const file=name=>payload.files.find(f=>f.path.endsWith('/'+name)).content;if(process.argv[2]==='assess')console.log(JSON.stringify({judgment:file('state.txt')==='ready'&&file('report.txt')===file('batch.txt')?'satisfied':'work-needed'}));else writeFileSync('report.txt',readFileSync('batch.txt'));`);
 const config=join(root,'config.json');writeFileSync(config,JSON.stringify({schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['state.txt','batch.txt','report.txt'],capabilityVersion:'synthetic-report-v1',assessor:[process.execPath,child,'assess'],actor:[process.execPath,child,'act'],maxAttempts:2,checkpointDirectory:'host'}));
 assert.equal(runConfig(config).status,'satisfied');assert.equal(runConfig(config).status,'reused');
 writeFileSync(join(root,'batch.txt'),'batch-2');assert.equal(runConfig(config).status,'satisfied');assert.equal(readFileSync(join(root,'report.txt'),'utf8'),'batch-2');
 writeFileSync(join(root,'batch.txt'),'batch-3');assert.equal(runConfig(config).status,'attempt-limit');
 console.log('PASS durable process integration: ready state does not skip required report; new batch invalidates reuse; restarted host preserves cumulative budget');
}finally{rmSync(root,{recursive:true,force:true});}
