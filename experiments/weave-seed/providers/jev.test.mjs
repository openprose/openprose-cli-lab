import {test} from 'node:test';
import assert from 'node:assert/strict';
import {symlinkSync,realpathSync,mkdtempSync,readFileSync,writeFileSync,rmSync,readdirSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {loadConfiguration,prepare,decide,strictJSON,assess} from './jev.mjs';
const here=fileURLToPath(new URL('.',import.meta.url));
const hash=v=>createHash('sha256').update(v).digest('hex');
const key='test-only-fake-credential-not-a-real-key';
function fixture() {
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'weave-jev-')));
 const config=JSON.parse(readFileSync(join(here,'jev.config.example.json'),'utf8'));
 config.endpoint='https://invalid.example/v1/systemone';
 writeFileSync(join(dir,'config.json'),JSON.stringify(config));
 writeFileSync(join(dir,config.questionFile),readFileSync(join(here,config.questionFile)));
 const load=()=>loadConfiguration(join(dir,'config.json'));
 function input(patch={},filesPatch=x=>x) {
  const files=[{role:'kernel',path:join(dir,'kernel.md'),content:'Kernel agreement.'},{role:'contract',path:join(dir,'program.md'),content:'Require source-backed compliance.'},{role:'evidence',path:join(dir,'state.json'),content:'{"current":true}'},...['config.json',config.questionFile].map(name=>({role:'evidence',path:join(dir,name),content:readFileSync(join(dir,name),'utf8')}))].map(v=>({...v,sha256:hash(v.content)}));
  const payload=JSON.stringify({version:1,policy:'explicit-test-policy',files:filesPatch(files)});
  return Buffer.from(JSON.stringify({schema:'openprose.weave-input/1',attempt:null,evidence:{identity:hash(payload),payload,observedAt:1000,validUntil:2000,gap:false,...patch}}));
 }
 return {dir,config,load,input,save:()=>writeFileSync(join(dir,'config.json'),JSON.stringify(config)),close:()=>rmSync(dir,{recursive:true,force:true})};
}
const response=(choice='satisfied')=>({model:'jev-1.13.0',answers:{assessment:{type:'choice',choice,confidence:0.95,probabilities:{satisfied:choice==='satisfied'?0.96:0.02,violated:choice==='violated'?0.96:0.02,unknown:choice==='unknown'?0.96:0.02}}},usage:{input_tokens:400,output_tokens:43}});
const http=v=>new Response(JSON.stringify(v),{status:200,headers:{'content-type':'application/json'}});
const options=fetchImpl=>({fetchImpl,environment:{TYPESAFE_API_KEY:key},now:()=>1500});
test('strict JSON rejects duplicate fields, invalid UTF8, BOM, nonfinite and surrogate data',()=>{
 for(const value of ['{"a":1,"a":2}','{"x":{"a":1,"\\u0061":2}}','[1e999]','{"x":"\\ud800"}','\ufeff{}']) assert.throws(()=>strictJSON(Buffer.from(value)));
 assert.throws(()=>strictJSON(Buffer.from([0xc0,0xaf])));
 assert.deepEqual(strictJSON(Buffer.from('{"x":"😀","s":" ","n":null}')),{x:'😀',s:' ',n:null});
});
test('bound request preserves agreement/evidence and rejects changed or unbound configuration',()=>{
 const f=fixture();try {
  const loaded=f.load(), raw=f.input(), request=JSON.parse(prepare(raw,loaded,1500).wire);
  assert.equal(request.state.agreement.kernel.content,'Kernel agreement.');assert.equal(request.questions.assessment.type,'choice');
  assert.throws(()=>prepare(f.input({},files=>files.filter(v=>v.path!==loaded.configPath)),loaded,1500));
  assert.throws(()=>prepare(f.input({},files=>files.map(v=>v.path===loaded.questionPath?{...v,role:'contract'}:v)),loaded,1500));
  writeFileSync(loaded.questionPath,readFileSync(loaded.questionPath,'utf8')+'\n');
  assert.throws(()=>prepare(raw,loaded,1500));assert.throws(()=>prepare(raw,f.load(),1500));
 }finally{f.close();}
});
test('gap, future and expired input return unknown without credential or HTTP',async()=>{
 const f=fixture();try {for(const patch of [{gap:true},{observedAt:1600},{validUntil:1500}]) assert.deepEqual(await assess(f.input(patch),f.load(),{fetchImpl:()=>assert.fail('network'),environment:{},now:()=>1500}),{judgment:'unknown'});}finally{f.close();}
});
test('decision policy admits strong choices, abstains on uncertainty and validates usage/model',()=>{
 const f=fixture();try {
  const c=f.load().config;
  for(const [choice,expected] of [['satisfied','satisfied'],['violated','work-needed'],['unknown','unknown']]) assert.equal(decide(response(choice),c).judgment,expected);
  for(const change of [a=>a.confidence=0.2,a=>a.probabilities={satisfied:0.6,violated:0.3,unknown:0.1},a=>{a.choice='violated';}]) {const r=response();change(r.answers.assessment);assert.equal(decide(r,c).judgment,'unknown');}
  for(const change of [r=>r.model='jev-latest',r=>delete r.usage,r=>r.usage.input_tokens=-1,r=>r.usage.output_tokens=2000,r=>r.answers.assessment.confidence=NaN,r=>r.answers.assessment.probabilities.unknown=0.9]) {const r=response();change(r);assert.throws(()=>decide(r,c));}
 }finally{f.close();}
});
test('mock HTTP uses explicit auth, no redirect or retries, bounded private receipt',async()=>{
 const f=fixture();try {
  f.config.receiptDirectory=f.dir;f.save();let calls=0;
  const result=await assess(f.input(),f.load(),options(async(url,init)=>{calls++;assert.equal(url,f.config.endpoint);assert.equal(init.headers.Authorization,'Bearer '+key);assert.equal(init.redirect,'error');assert.ok(!init.body.includes(key));return http(response());}));
  assert.deepEqual(result,{judgment:'satisfied'});assert.equal(calls,1);
  const receiptName=readdirSync(f.dir).find(n=>/^[0-9a-f-]{36}\.json$/.test(n)),bytes=readFileSync(join(f.dir,receiptName),'utf8'),record=JSON.parse(bytes);
  assert.equal(statSync(join(f.dir,receiptName)).mode&0o777,0o600);assert.ok(!bytes.includes(key));assert.equal(record.rawChoice,'satisfied');assert.equal(record.probabilities.satisfied,0.96);assert.match(record.requestSha256,/^[a-f0-9]{64}$/);assert.equal(record.questionSha256,f.load().questionSha256);
 }finally{f.close();}
});
test('HTTP errors, malformed/oversized bodies, missing key and timeouts fail closed',async()=>{
 const f=fixture();try {
  f.config.limits.timeoutMs=15;f.save();
  const fetches=[()=>new Response('sensitive error',{status:500}),()=>new Response('{}',{headers:{'content-type':'text/plain'}}),()=>new Response('{bad',{headers:{'content-type':'application/json'}}),()=>http({...response(),usage:{input_tokens:1}}),()=>new Response('x'.repeat(65537),{headers:{'content-type':'application/json'}}),()=>new Promise(()=>{}),()=>new Response(new ReadableStream({start(){}}),{headers:{'content-type':'application/json'}})];
  for(const send of fetches) await assert.rejects(assess(f.input(),f.load(),options(send)),/^Error: JEV_ASSESSMENT_FAILED$/);
  await assert.rejects(assess(f.input(),f.load(),{...options(()=>assert.fail('network')),environment:{}}));
 }finally{f.close();}
});
test('evidence expiring during response abstains and malformed source prevents HTTP',async()=>{
 const f=fixture();try {
  let tick=1500;assert.deepEqual(await assess(f.input(),f.load(),{...options(async()=>{tick=2000;return http(response());}),now:()=>tick}),{judgment:'unknown'});
  await assert.rejects(assess(f.input({},files=>files.map((s,i)=>i===0?{...s,sha256:'bad'}:s)),f.load(),options(()=>assert.fail('network'))));
 }finally{f.close();}
});
test('process stdout is exact judgment; errors never expose input or credential',()=>{
 const f=fixture();try {
  const argv=[join(here,'jev.mjs'),'--config',join(f.dir,'config.json')];
  const good=spawnSync(process.execPath,argv,{input:f.input({gap:true}),encoding:'utf8',env:{}});
  assert.equal(good.status,0);assert.equal(good.stdout,'{"judgment":"unknown"}\n');assert.equal(good.stderr,'');
  const bad=spawnSync(process.execPath,argv,{input:'sensitive malformed input',encoding:'utf8',env:{TYPESAFE_API_KEY:key}});
  assert.equal(bad.status,1);assert.equal(bad.stdout,'');assert.equal(bad.stderr,'JEV_ASSESSMENT_FAILED\n');
 }finally{f.close();}
});

test('canonical config/question paths accept filesystem aliases while binding exact bytes',()=>{
 const f=fixture();try {
  const alias=f.dir+'-alias';symlinkSync(f.dir,alias);
  try {const loaded=loadConfiguration(join(alias,'config.json'));assert.equal(loaded.configPath,join(f.dir,'config.json'));assert.ok(prepare(f.input(),loaded,1500));}
  finally {rmSync(alias);}
  writeFileSync(join(f.dir,'config.json'),' '.repeat(65537));assert.throws(()=>f.load());
 }finally{f.close();}
});
