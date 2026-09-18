import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, chmodSync, rmSync, existsSync, realpathSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { PassThrough } from 'node:stream';
import { spawnSync } from 'node:child_process';
import { bindFiles } from '../binding.mjs';
import { invokeNative, loadConfig, parseJSON, runProcess, kernelImageSha256 } from './actor.mjs';
import { readInput } from './run.mjs';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const root = realpathSync(mkdtempSync(join(tmpdir(), 'native-actor-')));
const binary = join(root, 'fake-cli.mjs'), configPath = join(root, 'actor.json');
const image = kernelImageSha256(Buffer.from('Synthetic kernel'));
writeFileSync(binary, `#!${process.execPath}\nimport {readFileSync,writeFileSync,appendFileSync} from 'node:fs';import {createHash} from 'node:crypto';
const a=process.argv.slice(2), get=k=>a[a.indexOf(k)+1];
if(process.env.UNSELECTED_SECRET)throw Error('ambient leak');
appendFileSync('argv.log',JSON.stringify(a)+'\\n');
const nativeLimits={maxTurns:Number(get('--native-max-turns')),timeoutSeconds:parseFloat(get('--native-timeout'))/1000,toolTimeoutSeconds:parseFloat(get('--native-tool-timeout'))/1000,maxOutputTokens:12000};
const languageImage={sha256:'${image}'},mode=process.env.FAKE_MODE;
if(a.includes('doctor')){const report={schema:mode==='bad-doctor'?'unknown':'openprose.doctor-report/1',build:{testSeamsEnabled:mode==='test-seams'},image:{sha256:mode==='doctor-mismatch'?'f'.repeat(64):'${image}'}};if(mode==='moving')report.imageSource='published-on-run';if(mode==='unknown-image')report.imageSource=null;console.log(JSON.stringify(report));}
else if(a.includes('--dry-run')){if(mode==='mutate')writeFileSync('program.md','changed during readiness');console.log(JSON.stringify({readiness:mode==='notready'?'blocked':'ready',wouldStartModel:false,selection:{harness:'agents-sdk',adapterId:'agents-sdk/jsonl',model:get('--model')},prompt:{placement:'system-append',strictness:'strict'},cwd:process.cwd(),languageImage,nativeLimits,billingOwner:'user-provider'}));}
else {if(mode==='fail'){writeFileSync('effect.txt','effect');console.error('PRIVATE-ERROR-CONTENT');process.exit(1);}if(mode==='flood'){process.stdout.write('x'.repeat(50000));process.stderr.write('PRIVATE-ERROR-CONTENT');process.exit(0);}if(mode==='slow')await new Promise(r=>setTimeout(r,2000));const kernel=createHash('sha256').update(readFileSync('kernel.md')).digest('hex');const result={terminal:{classification:'success'},runnerExitCode:0,languageImage,nativeLimits,digests:{deliveredImageSha256:mode==='wrongkernel'?'f'.repeat(64):kernel}};console.log(JSON.stringify({type:'runner.completed',payload:{result}}));if(mode==='duplicate')console.log(JSON.stringify({type:'runner.completed',payload:{result}}));}
`); chmodSync(binary, 0o700);
writeFileSync(join(root, 'kernel.md'), 'Synthetic kernel'); writeFileSync(join(root, 'program.md'), 'Opaque task obligations');
const base = { schema: 1, executable: binary, executableSha256: digest(readFileSync(binary)), cwd: '.', kernel: 'kernel.md', task: 'program.md', expectedImageSha256: image, harness: 'agents-sdk', authProfile: 'openai-api-key', model: 'fake-model', environmentKeys: ['FAKE_MODE'], nativeTimeoutMs: 100, toolTimeoutMs: 10, outerTimeoutMs: 200, processTimeoutMs: 500, readinessTimeoutMs: 1000, maxCaptureBytes: 4096 };
function setup(changes = {}) { writeFileSync(configPath, JSON.stringify({ ...base, ...changes })); return { schema: 'openprose.weave-input/1', attempt: 'attempt-1', evidence: bindFiles({root,kernel:'kernel.md',contracts:['program.md'],evidence:['actor.json'],policy:'test-v1',ttlMs:10000}).observe() }; }
let count = 0;
async function test(name, fn) { await fn(); count++; console.log('PASS', name); }
try {
  await test('real fake CLI exact argv and selected environment, no fulfillment claim', async () => {
    const receipt = await invokeNative(configPath, setup(), { ambient: { FAKE_MODE: 'ok', UNSELECTED_SECRET: 'must-not-leak' } });
    assert.equal(receipt.status, 'native-completed'); assert.match(receipt.acceptance, /fresh observation/);
    const lines = readFileSync(join(root,'argv.log'),'utf8').trim().split('\n').map(JSON.parse); assert.equal(lines.length, 3);
    const common = ['--harness','agents-sdk','--auth-profile','openai-api-key','--model','fake-model','--native-max-turns','8','--native-timeout','100ms','--native-tool-timeout','10ms','--timeout','200ms','--cwd',root,'--output-contract','native'];
    assert.deepEqual(lines[0],[...common,'cli','doctor','--json']); assert.deepEqual(lines[1],[...common,'--dry-run','--output','json','run','program.md']); assert.deepEqual(lines[2],[...common,'--output','jsonl','run','program.md']);
  });
  await test('selected __proto__ environment name is an own value, not prototype mutation', async () => {
    const envelope=setup({environmentKeys:['FAKE_MODE','__proto__']});
    const ambient=JSON.parse('{"FAKE_MODE":"ok","__proto__":"selected-literal"}');
    await invokeNative(configPath,envelope,{ambient,processRunner(argv,options){
      assert.equal(Object.getPrototypeOf(options.environment),null);assert(Object.hasOwn(options.environment,'__proto__'));assert.equal(options.environment.__proto__,'selected-literal');
      return runProcess(argv,options);
    }});
  });
  await test('unbound config, changed source, stale evidence and wrong identity rejected', async () => {
    let envelope=setup(); envelope.evidence.identity='0'.repeat(64); await assert.rejects(invokeNative(configPath,envelope));
    envelope=setup(); envelope.evidence.validUntil=Date.now()-1; await assert.rejects(invokeNative(configPath,envelope));
    envelope=setup();writeFileSync(join(root,'program.md'),'changed');await assert.rejects(invokeNative(configPath,envelope));
    envelope=setup();const p=JSON.parse(envelope.evidence.payload);p.files=p.files.filter(f=>f.path!==configPath);envelope.evidence.payload=JSON.stringify(p);envelope.evidence.identity=digest(envelope.evidence.payload);await assert.rejects(invokeNative(configPath,envelope));
  });
  await test('selected kernel bytes bind the canonical one-payload aggregate before any process', async () => {
    const bytes=Buffer.from('é\n');
    assert.equal(kernelImageSha256(bytes),digest(Buffer.concat([Buffer.from('payload/kernel.md\0'+bytes.length+'\0'),bytes,Buffer.from([0])])));
    let launches=0;
    await assert.rejects(invokeNative(configPath,setup({expectedImageSha256:'a'.repeat(64)}),{ambient:{FAKE_MODE:'ok'},processRunner(){launches++;throw Error('must not launch');}}),/NATIVE_ACTOR_INPUT_FAILED/);
    assert.equal(launches,0);
    const original=readFileSync(join(root,'kernel.md'));writeFileSync(join(root,'kernel.md'),'Different selected kernel');
    try {await assert.rejects(invokeNative(configPath,setup(),{ambient:{FAKE_MODE:'ok'},processRunner(){launches++;throw Error('must not launch');}}),/NATIVE_ACTOR_INPUT_FAILED/);assert.equal(launches,0);}
    finally {writeFileSync(join(root,'kernel.md'),original);}
  });
  await test('doctor rejects mutable unknown mismatched and test-seam images before readiness or execution', async () => {
    for(const mode of ['moving','unknown-image','doctor-mismatch','test-seams','bad-doctor']) {
      const before=readFileSync(join(root,'argv.log'),'utf8').trim().split('\n').length;
      await assert.rejects(invokeNative(configPath,setup(),{ambient:{FAKE_MODE:mode}}),/NATIVE_ACTOR_IMAGE_POLICY_FAILED/);
      const added=readFileSync(join(root,'argv.log'),'utf8').trim().split('\n').slice(before).map(JSON.parse);
      assert.equal(added.length,1);assert(added[0].includes('doctor'));assert(!added[0].includes('--dry-run'));
    }
  });
  await test('readiness cannot retarget task; blocked readiness never launches action', async () => {
    for(const mode of ['mutate','notready']) { const envelope=setup(); await assert.rejects(invokeNative(configPath,envelope,{ambient:{FAKE_MODE:mode}})); }
  });
  await test('effect then failure, output overflow, deadline and completion ambiguity fail closed', async () => {
    for (const mode of ['fail','flood','slow','wrongkernel','duplicate']) {
      await assert.rejects(invokeNative(configPath,setup(),{ambient:{FAKE_MODE:mode}}), error => !error.message.includes('PRIVATE-ERROR-CONTENT'));
    }
    assert(existsSync(join(root,'effect.txt')));
  });
  await test('strict config and evidence parser rejects ambiguous and unsupported inputs', () => {
    for(const text of ['{"a":1,"a":2}', '{"a":1e9999}', '{"a":"\\ud800"}'])assert.throws(()=>parseJSON(text));
    setup({harness:'codex'});assert.throws(()=>loadConfig(configPath));setup({environment:{TOKEN:'secret'}});assert.throws(()=>loadConfig(configPath));
    setup({task:'../program.md'});assert.throws(()=>loadConfig(configPath));setup({processTimeoutMs:200});assert.throws(()=>loadConfig(configPath));
  });
  await test('bounded stdin enforces size, UTF8, and timeout without provider', async () => {
    let stream=new PassThrough();let promise=readInput(stream,{limit:2,timeoutMs:100});stream.end('123');await assert.rejects(promise);
    stream=new PassThrough();promise=readInput(stream,{timeoutMs:100});stream.end(Buffer.from([255]));await assert.rejects(promise);
    stream=new PassThrough();await assert.rejects(readInput(stream,{timeoutMs:10}));
  });
  await test('private phase receipts retain failures without credentials, prompts or child output', async () => {
    const receipts=join(root,'receipts');mkdirSync(receipts,{mode:0o700});
    for(const [phase,mode,changes] of [['config','ok',{schema:2}],['input','ok',{}],['image-policy','moving',{}],['readiness','notready',{}],['revalidate','mutate',{}],['run','fail',{}],['completion','wrongkernel',{}]]) {
      writeFileSync(join(root,'program.md'),'Opaque task obligations before receipt '+phase);
      const envelope=setup({receiptDirectory:receipts,...changes});if(phase==='input')envelope.evidence.identity='0'.repeat(64);
      const before=new Set(readdirSync(receipts));
      await assert.rejects(invokeNative(configPath,envelope,{ambient:{FAKE_MODE:mode,OPENAI_API_KEY:'PRIVATE-API-KEY'}}), new RegExp('NATIVE_ACTOR_'+phase.toUpperCase().replaceAll('-', '_')+'_FAILED'));
      const path=join(receipts,readdirSync(receipts).find(name=>!before.has(name)));const bytes=readFileSync(path,'utf8'),record=JSON.parse(bytes);
      assert.equal(record.phase,phase);assert.equal(record.errorCode,'NATIVE_ACTOR_'+phase.toUpperCase().replaceAll('-', '_')+'_FAILED');assert.equal(record.attempt,'attempt-1');assert.equal(record.actionLaunched,['run','completion'].includes(phase));
      assert.equal(statSync(path).mode&0o777,0o600);assert(bytes.length<65536);assert(!bytes.includes('PRIVATE'));assert(!bytes.includes('Opaque task'));assert(!bytes.includes('environment'));assert(record.finishedAt>=record.startedAt);assert.equal(record.configSha256,digest(readFileSync(configPath)));
    }
    await invokeNative(configPath,setup({receiptDirectory:receipts}),{ambient:{FAKE_MODE:'ok'}});
    const success=readdirSync(receipts).map(name=>JSON.parse(readFileSync(join(receipts,name)))).find(r=>r.status==='native-completed');assert(success);assert.equal(success.errorCode,null);assert.equal(success.executableSha256,base.executableSha256);
    const before=readdirSync(receipts).length;await invokeNative(configPath,setup(),{ambient:{FAKE_MODE:'ok'}});assert.equal(readdirSync(receipts).length,before);
    const missing=join(root,'not-created');await assert.rejects(invokeNative(configPath,setup({receiptDirectory:missing}),{ambient:{FAKE_MODE:'ok'}}));assert(!existsSync(missing));
    await assert.rejects(invokeNative(configPath,setup({receiptDirectory:receipts}),{ambient:{FAKE_MODE:'ok'},async processRunner(argv,options){const out=await runProcess(argv,options);if(!argv.includes('--dry-run')&&!argv.includes('doctor'))chmodSync(receipts,0o755);return out;}}),/NATIVE_ACTOR_RECEIPT_FAILED/);chmodSync(receipts,0o700);
  });
  await test('actual adapter CLI emits generic error without selected secret values', () => {
    const envelope=setup();const child=spawnSync(process.execPath,['--no-env-file',resolve(import.meta.dir,'run.mjs'),'--config',configPath],{env:{FAKE_MODE:'fail',UNSELECTED_SECRET:'PRIVATE-ERROR-CONTENT'},input:JSON.stringify(envelope),encoding:'utf8',timeout:3000});
    assert.equal(child.status,1);assert(!child.stdout.includes('PRIVATE'));assert(!child.stderr.includes('PRIVATE'));assert.match(child.stderr,/uncertain/);
  });
  console.log(`PASS ${count} native actor checks; no provider calls`);
} finally { rmSync(root,{recursive:true,force:true}); }
