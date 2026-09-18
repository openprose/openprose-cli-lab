/** Real process boundaries with a mocked Jev transport and fake native CLI. Never uses a model. */
import assert from 'node:assert/strict';
import {kernelImageSha256} from './native-actor/actor.mjs';
import { mkdtempSync, realpathSync, readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
const seed=fileURLToPath(new URL('../',import.meta.url));
const root=realpathSync(mkdtempSync(join(tmpdir(),'weave-full-offline-')));
const sha=x=>createHash('sha256').update(x).digest('hex');
const rust=process.argv[2];
try {
 for(const [file,content] of Object.entries({'kernel.md':'Synthetic kernel for transport qualification only.','program.md':'Copy source.txt into report.txt.','source.txt':'first revision','report.txt':''}))writeFileSync(join(root,file),content);
 const kernelHash=sha(readFileSync(join(root,'kernel.md'))),imageHash=kernelImageSha256(readFileSync(join(root,'kernel.md')));
 const native=join(root,'fake-prose');
 writeFileSync(native,`#!${process.execPath}\nimport {readFileSync,writeFileSync,appendFileSync} from 'node:fs';\nconst args=process.argv.slice(2), option=n=>args[args.indexOf(n)+1];\nconst limits={maxTurns:8,timeoutSeconds:120,toolTimeoutSeconds:15,maxOutputTokens:12000};\nif(args.includes('doctor'))console.log(JSON.stringify({schema:'openprose.doctor-report/1',build:{testSeamsEnabled:false},image:{sha256:${JSON.stringify(imageHash)}}}));\nelse if(args.includes('--dry-run'))console.log(JSON.stringify({readiness:'ready',wouldStartModel:false,selection:{harness:'agents-sdk',model:'offline-test-model',adapterId:'agents-sdk/jsonl'},billingOwner:'user-provider',prompt:{placement:'system-append',strictness:'strict'},cwd:process.cwd(),languageImage:{sha256:${JSON.stringify(imageHash)}},nativeLimits:limits}));\nelse {appendFileSync('native-calls.txt','act\\n');writeFileSync('report.txt',readFileSync('source.txt'));console.log(JSON.stringify({type:'runner.completed',payload:{result:{terminal:{classification:'success'},runnerExitCode:0,languageImage:{sha256:${JSON.stringify(imageHash)}},digests:{deliveredImageSha256:${JSON.stringify(kernelHash)}},nativeLimits:limits}}}));}\n`,{mode:0o700});
 const actor={schema:1,executable:native,executableSha256:sha(readFileSync(native)),cwd:'.',kernel:'kernel.md',task:'program.md',expectedImageSha256:imageHash,harness:'agents-sdk',authProfile:'openai-api-key',model:'offline-test-model',environmentKeys:[]};
 writeFileSync(join(root,'actor.json'),JSON.stringify(actor));
 const provider=JSON.parse(readFileSync(join(seed,'providers/jev.config.example.json'),'utf8'));
 provider.endpoint='https://not-a-real-provider.invalid/v1/systemone';
 provider.questionFile='question.json';provider.receiptDirectory=join(root,'receipts');mkdirSync(provider.receiptDirectory,{mode:0o700});
 writeFileSync(join(root,'jev.json'),JSON.stringify(provider));
 writeFileSync(join(root,'question.json'),readFileSync(join(seed,'providers/jev.question.example.json')));
 const mocked=join(root,'mocked-assessor.mjs');
 writeFileSync(mocked,`import {loadConfiguration,assess} from ${JSON.stringify(pathToFileURL(join(seed,'providers/jev.mjs')).href)};\nimport {appendFileSync} from 'node:fs';\nlet raw='';for await(const part of process.stdin)raw+=part;\nconst loaded=loadConfiguration(${JSON.stringify(join(root,'jev.json'))});\nconst result=await assess(Buffer.from(raw),loaded,{environment:{TYPESAFE_API_KEY:'offline-test-fake-key'},fetchImpl:async(_url,init)=>{appendFileSync('assessment-calls.txt','assess\\n');const request=JSON.parse(init.body),files=request.state.source_evidence;const value=name=>files.find(f=>f.path.endsWith('/'+name)).content;const choice=value('source.txt')===value('report.txt')?'satisfied':'violated';return new Response(JSON.stringify({model:loaded.config.model,answers:{assessment:{type:'choice',choice,confidence:.95,probabilities:{satisfied:choice==='satisfied'?.96:.02,violated:choice==='violated'?.96:.02,unknown:.02}}},usage:{input_tokens:100,output_tokens:10}}),{headers:{'content-type':'application/json'}});}});\nconsole.log(JSON.stringify(result));\n`);
 const config={schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['source.txt','report.txt','actor.json','jev.json','question.json'],capabilityVersion:'offline-whole-loop-v1',assessor:[process.execPath,'--no-env-file',mocked],actor:[process.execPath,'--no-env-file',join(seed,'integration/native-actor/run.mjs'),'--config',join(root,'actor.json')],environmentKeys:[],checkpointDirectory:'host',maxAttempts:3,ttlMs:60000,timeoutMs:10000};
 writeFileSync(join(root,'config.json'),JSON.stringify(config));
 function step(native=false) {
  const argv=native?['step',join(root,'config.json')]:['--no-env-file',join(seed,'local/run.mjs'),'step',join(root,'config.json')];
  const result=spawnSync(native?rust:process.execPath,argv,{env:{},encoding:'utf8',timeout:15000,maxBuffer:1048576});
  assert.ifError(result.error);assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
 }
 assert.deepEqual(step(),{status:'satisfied',attempts:1,pending:null});
 assert.deepEqual(step(!!rust),{status:'reused',attempts:1,pending:null});
 assert.equal(readFileSync(join(root,'report.txt'),'utf8'),'first revision');
 writeFileSync(join(root,'source.txt'),'second revision');
 assert.deepEqual(step(!!rust),{status:'satisfied',attempts:2,pending:null});
 assert.deepEqual(step(),{status:'reused',attempts:2,pending:null});
 assert.equal(readFileSync(join(root,'native-calls.txt'),'utf8'),'act\nact\n');
 assert.equal(readFileSync(join(root,'assessment-calls.txt'),'utf8'),'assess\nassess\nassess\nassess\n');
 // Assessment policy edits are selected evidence, so they cannot reuse a previous acceptance.
 const question=JSON.parse(readFileSync(join(root,'question.json'),'utf8'));question.scope+=' (offline revision)';writeFileSync(join(root,'question.json'),JSON.stringify(question));
 assert.equal(step(!!rust).status,'satisfied');assert.equal(step().status,'reused');
 assert.equal(readFileSync(join(root,'native-calls.txt'),'utf8'),'act\nact\n');
 console.log(`PASS complete ${rust?'Bun/Rust':'Bun'} offline adapter loop: mocked Jev transport, real native adapter, repair, reuse, and bound-question invalidation; zero model calls`);
} finally {rmSync(root,{recursive:true,force:true});}
