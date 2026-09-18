/** Generated configuration integration only: mocked Jev HTTP and a fake native CLI. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {configure} from './configure.mjs';
const here=dirname(fileURLToPath(import.meta.url)),seed=dirname(here),sha=v=>createHash('sha256').update(v).digest('hex');
const rust=process.env.WEAVE_RUST_LOCAL;
test('generated config runs real adapter/coordinator boundaries with mock transport and retains uncertain effects',()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'weave-generated-loop-')));
 try {
  for(const[name,body]of Object.entries({'kernel.md':'Synthetic kernel; transport fixture only.','program.md':'Synthetic program; fixture copies source.txt into report.txt.','source.txt':'first','report.txt':'','mode.txt':'ok'}))writeFileSync(join(root,name),body);
  const native=join(root,'fake-prose'),image='a'.repeat(64),kernel=sha(readFileSync(join(root,'kernel.md')));
  writeFileSync(native,`#!${process.execPath} --no-env-file
import {readFileSync,writeFileSync,appendFileSync} from 'node:fs';
const limits={maxTurns:8,timeoutSeconds:120,toolTimeoutSeconds:15,maxOutputTokens:12000};
if(process.argv.includes('--dry-run')) console.log(JSON.stringify({readiness:'ready',wouldStartModel:false,selection:{harness:'agents-sdk',model:'offline-test-model',adapterId:'agents-sdk/jsonl'},billingOwner:'user-provider',prompt:{placement:'system-append',strictness:'strict'},cwd:process.cwd(),languageImage:{sha256:${JSON.stringify(image)}},nativeLimits:limits}));
else {
 appendFileSync('native-calls.log','act\\n');writeFileSync('report.txt',readFileSync('source.txt'));
 if(readFileSync('mode.txt','utf8')==='fail') process.exitCode=1;
 else console.log(JSON.stringify({type:'runner.completed',payload:{result:{terminal:{classification:'success'},runnerExitCode:0,languageImage:{sha256:${JSON.stringify(image)}},digests:{deliveredImageSha256:${JSON.stringify(kernel)}},nativeLimits:limits}}}));
}
`,{mode:0o700});
  const reviewed=join(root,'reviewed-question.json');writeFileSync(reviewed,readFileSync(join(seed,'providers/jev.question.example.json')));
  const setup={schema:1,root,configDirectory:'generated',kernel:'kernel.md',task:'program.md',contracts:['program.md'],evidence:['source.txt','report.txt','mode.txt'],bun:process.execPath,nativeCli:native,nativeCliSha256:sha(readFileSync(native)),expectedImageSha256:image,actionModel:'offline-test-model',actorEnvironmentKeys:['OPENAI_API_KEY'],assessment:{endpoint:'https://not-a-provider.invalid/v1/systemone',model:'jev-1.13.0',questionFile:reviewed,decisionPolicy:{id:'offline-unvalidated-fixture',minProbability:0.9,minConfidence:0.8,minMargin:0.5},apiKeyEnv:'TYPESAFE_API_KEY'},maxAttempts:3};
  const setupPath=join(root,'setup.json');writeFileSync(setupPath,JSON.stringify(setup));const generated=configure(setupPath);
  const shim=join(root,'mock-assessor.mjs');
  writeFileSync(shim,`import {loadConfiguration,assess} from ${JSON.stringify(pathToFileURL(join(seed,'providers/jev.mjs')).href)};
import {appendFileSync} from 'node:fs';
let raw='';for await(const part of process.stdin)raw+=part;
const loaded=loadConfiguration(${JSON.stringify(join(generated.configurationDirectory,'jev.json'))});
const result=await assess(Buffer.from(raw),loaded,{fetchImpl:async(url,init)=>{
 appendFileSync('assessment-calls.log','assess\\n');
 const request=JSON.parse(init.body),files=request.state.source_evidence,value=name=>files.find(f=>f.path.endsWith('/'+name)).content;
 const choice=value('source.txt')===value('report.txt')?'satisfied':'violated';
 return new Response(JSON.stringify({model:loaded.config.model,answers:{assessment:{type:'choice',choice,confidence:.95,probabilities:{satisfied:choice==='satisfied'?.96:.02,violated:choice==='violated'?.96:.02,unknown:.02}}},usage:{input_tokens:100,output_tokens:10}}),{headers:{'content-type':'application/json'}});
}});console.log(JSON.stringify(result));
`);
  // Explicit test-only capability replacement. The outer config and shim are selected evidence.
  const config=JSON.parse(readFileSync(generated.config));config.assessor=[process.execPath,'--no-env-file',shim];config.evidence.push('mock-assessor.mjs');config.capabilityVersion+='-offline-mock-v1';writeFileSync(generated.config,JSON.stringify(config));
  // No ambient environment is inherited. These two fake values satisfy generated name selections only.
  const environment={OPENAI_API_KEY:'not-a-real-openai-key',TYPESAFE_API_KEY:'not-a-real-typesafe-key'};
  function invoke(command,options=[],nativeLoop=false) {
   const result=spawnSync(nativeLoop?rust:process.execPath,nativeLoop?[command,generated.config,...options]:['--no-env-file',join(seed,'local/run.mjs'),command,generated.config,...options],{env:environment,encoding:'utf8',timeout:15000,maxBuffer:1048576});
   assert.ifError(result.error);return result;
  }
  function success(command,options=[],nativeLoop=false){const result=invoke(command,options,nativeLoop);assert.equal(result.status,0,result.stderr);assert.equal(result.stderr,'');return result.stdout.trim().split('\n').map(line=>JSON.parse(line));}
  const count=name=>readFileSync(join(root,name),'utf8').trim().split('\n').length;
  assert.equal(success('check')[0].providerVerified,false);assert.equal(success('status')[0].checkpoint,null);
  assert.deepEqual(success('step')[0],{status:'satisfied',attempts:1,pending:null});assert.equal(count('native-calls.log'),1);assert.equal(count('assessment-calls.log'),2);
  assert.equal(success('step',[],!!rust)[0].status,'reused');assert.equal(count('assessment-calls.log'),2);
  writeFileSync(join(root,'source.txt'),'second');
  const served=success('serve',['--poll-ms','1','--max-steps','2'],!!rust);assert.equal(served[0].status,'satisfied');assert.equal(served[0].attempts,2);assert.equal(served[1].status,'reused');assert.deepEqual(served[2],{stopped:'step-limit',steps:2});
  assert.equal(readFileSync(join(root,'report.txt'),'utf8'),'second');assert.equal(count('native-calls.log'),2);assert.equal(count('assessment-calls.log'),4);
  const questionPath=join(generated.configurationDirectory,'question.json'),question=JSON.parse(readFileSync(questionPath));question.scope+=' edited offline';writeFileSync(questionPath,JSON.stringify(question));
  assert.equal(success('step')[0].status,'satisfied');assert.equal(count('assessment-calls.log'),5);assert.equal(count('native-calls.log'),2);
  const validQuestion=readFileSync(questionPath);writeFileSync(questionPath,'{}');assert.notEqual(invoke('step').status,0);assert.equal(count('assessment-calls.log'),5);assert.equal(count('native-calls.log'),2);
  writeFileSync(questionPath,validQuestion);assert.equal(success('step')[0].status,'reused');
  writeFileSync(join(root,'source.txt'),'third');writeFileSync(join(root,'mode.txt'),'fail');assert.equal(success('step')[0].status,'action-outcome-unknown');
  const pending=success('status')[0].checkpoint;assert.equal(pending.attempts,3);assert.equal(typeof pending.pending,'string');assert.equal(readFileSync(join(root,'report.txt'),'utf8'),'third');
  const before=count('native-calls.log');assert.equal(success('step',[],!!rust)[0].status,'recovery-needed');assert.equal(count('native-calls.log'),before);
 } finally {rmSync(root,{recursive:true,force:true});}
});
