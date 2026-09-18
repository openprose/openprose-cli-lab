import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,mkdirSync,writeFileSync,readFileSync,readdirSync,rmSync,chmodSync,existsSync,statSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {configure} from './configure.mjs';
import {loadConfiguration} from '../providers/jev.mjs';
import {loadConfig,kernelImageSha256} from '../integration/native-actor/actor.mjs';
const here=dirname(fileURLToPath(import.meta.url)),sha=v=>createHash('sha256').update(v).digest('hex');
function fixture(){
 const parent=realpathSync(mkdtempSync(join(tmpdir(),'weave-configure-'))),root=join(parent,'subject');mkdirSync(root);
 for(const [name,value]of [['kernel.md','Explicit synthetic test kernel.'],['program.md','Explicit synthetic test program.'],['source.json','{}'],['report.md','']])writeFileSync(join(root,name),value);
 const executable=join(parent,'fake-cli');writeFileSync(executable,'#!/bin/sh\nexit 99\n');chmodSync(executable,0o700);
 const questionFile=join(parent,'reviewed-question.json');writeFileSync(questionFile,readFileSync(join(here,'../providers/jev.question.example.json')));
 const manifest={schema:1,root,configDirectory:'local-config',kernel:'kernel.md',task:'program.md',contracts:['program.md'],evidence:['source.json','report.md'],bun:process.execPath,nativeCli:executable,nativeCliSha256:sha(readFileSync(executable)),expectedImageSha256:kernelImageSha256(readFileSync(join(root,'kernel.md'))),actionModel:'explicit-test-model',actorEnvironmentKeys:['PATH','OPENAI_API_KEY'],assessment:{endpoint:'https://invalid.example/v1/systemone',model:'jev-1.13.0',questionFile,decisionPolicy:{id:'offline-test',minProbability:0.9,minConfidence:0.8,minMargin:0.5},apiKeyEnv:'TYPESAFE_API_KEY'},maxAttempts:3};
 const path=join(parent,'setup.json');const save=()=>writeFileSync(path,JSON.stringify(manifest));save();
 return{parent,root,manifest,path,save,close:()=>rmSync(parent,{recursive:true,force:true})};
}
test('one reviewed manifest produces valid bound configs without commands, credentials or source changes',()=>{
 const f=fixture();try{
 const before=new Map(readdirSync(f.root).map(name=>[name,readFileSync(join(f.root,name))]));const result=configure(f.path);assert.equal(result.providerVerified,false);assert.equal(result.status,'configured-not-provider-verified');
 const c=JSON.parse(readFileSync(result.config));assert.equal(c.timeoutMs,270000);assert.equal(c.ttlMs,300000);assert.equal(c.maxAttempts,3);assert.deepEqual(c.environmentKeys,['TYPESAFE_API_KEY','PATH','OPENAI_API_KEY']);assert.equal(result.commands.serve.at(-1),'1');
 assert.ok(c.evidence.includes('local-config/config.json'));for(const name of ['actor.json','jev.json','question.json'])assert.ok(c.evidence.includes('local-config/'+name));
 assert.equal(loadConfiguration(join(result.configurationDirectory,'jev.json')).config.model,'jev-1.13.0');assert.equal(loadConfig(join(result.configurationDirectory,'actor.json')).model,'explicit-test-model');
 for(const[name,bytes]of before)assert.deepEqual(readFileSync(join(f.root,name)),bytes);assert.ok(!existsSync(join(result.configurationDirectory,'host')));assert.equal(statSync(result.configurationDirectory).mode&0o777,0o700);for(const name of readdirSync(result.configurationDirectory))assert.equal(statSync(join(result.configurationDirectory,name)).mode&0o777,0o600);
 const unchanged=readFileSync(result.config);assert.throws(()=>configure(f.path));assert.deepEqual(readFileSync(result.config),unchanged);
 }finally{f.close();}
});
test('incorrect binary digest, unknown fields, outside sources and missing explicit values fail without output',()=>{
 for(const modify of [m=>m.nativeCliSha256='b'.repeat(64),m=>m.expectedImageSha256='b'.repeat(64),m=>m.secretValue='must-not-be-accepted',m=>m.evidence=['../outside'],m=>delete m.assessment.endpoint,m=>m.maxAttempts=101,m=>m.contracts=[],m=>m.actorEnvironmentKeys=[]]) {
 const f=fixture();try{modify(f.manifest);f.save();assert.throws(()=>configure(f.path));assert.ok(!existsSync(join(f.root,'local-config')));}finally{f.close();}
 }
});
test('provider question validation failure cleans only the newly claimed configuration directory',()=>{
 const f=fixture();try{writeFileSync(f.manifest.assessment.questionFile,'{"type":"bad"}');assert.throws(()=>configure(f.path));assert.ok(!existsSync(join(f.root,'local-config')));assert.equal(readFileSync(join(f.root,'source.json'),'utf8'),'{}');}finally{f.close();}
});
test('standalone setup emits one JSON record and provider-free check reports missing names',()=>{
 const f=fixture();try{
 const created=spawnSync(process.execPath,['--no-env-file',join(here,'configure.mjs'),f.path],{env:{},encoding:'utf8',timeout:10000});assert.equal(created.status,0,created.stderr);assert.equal(created.stderr,'');assert.equal(created.stdout.trim().split('\n').length,1);
 const result=JSON.parse(created.stdout),[executable,...args]=result.commands.check,checked=spawnSync(executable,args,{env:{},encoding:'utf8',timeout:10000});assert.equal(checked.status,2,checked.stderr);const check=JSON.parse(checked.stdout);assert.equal(check.providerVerified,false);assert.deepEqual(check.environment.missing,['TYPESAFE_API_KEY','PATH','OPENAI_API_KEY']);assert.ok(!existsSync(join(result.configurationDirectory,'host')));
 }finally{f.close();}
});
test('setup errors identify safe actionable stages without leaking private input',()=>{
 for(const [expected,change] of [
  ['manifest',f=>writeFileSync(f.path,'malformed private-input-value')],
  ['native-cli',f=>{f.manifest.nativeCliSha256='b'.repeat(64);f.save();}],
  ['question',f=>writeFileSync(f.manifest.assessment.questionFile,'{"private":"private-input-value"}')],
 ]){
  const f=fixture();try{
   change(f);const result=spawnSync(process.execPath,['--no-env-file',join(here,'configure.mjs'),f.path],{env:{},encoding:'utf8',timeout:10000});
   assert.equal(result.status,1);assert.equal(result.stdout,'');const error=JSON.parse(result.stderr);assert.equal(error.error,'CONFIGURATION_SETUP_REJECTED');assert.equal(error.stage,expected);assert.ok(error.nextStep.length>20);assert.ok(!result.stderr.includes('private-input-value'));assert.ok(!result.stderr.includes(f.root));assert.ok(!existsSync(join(f.root,'local-config')));
  }finally{f.close();}
 }
});
