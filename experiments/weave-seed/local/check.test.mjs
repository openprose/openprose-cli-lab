import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync,rmSync,readdirSync,mkdirSync,chmodSync,existsSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createExample} from '../getting-started/create.mjs';
import {emptyCheckpoint} from '../bun/index.mjs';
import {encodeCheckpoint} from '../bun/host.mjs';
import {prepareConfig,runConfig} from '../integration/run.mjs';
import {checkConfig} from './check.mjs';
const here=dirname(fileURLToPath(import.meta.url));
function fixture(){const parent=mkdtempSync(join(tmpdir(),'weave-check-'));const created=createExample(join(parent,'example'));return {...created,parent,close:()=>rmSync(parent,{recursive:true,force:true})};}
function change(f,fn){const c=JSON.parse(readFileSync(f.config));fn(c);writeFileSync(f.config,JSON.stringify(c));}
const cli=(...args)=>spawnSync(process.execPath,['--no-env-file',join(here,'run.mjs'),...args],{encoding:'utf8',env:{},timeout:10000});
test('check reports configured without creating a checkpoint or invoking capabilities',()=>{
 const f=fixture();try{
 const before=readdirSync(f.root);const result=checkConfig(f.config);assert.equal(result.status,'configured');assert.equal(result.providerVerified,false);assert.equal(result.semanticAssessment,false);assert.equal(result.sources.status,'available');assert.equal(result.sources.selectedCount,4);assert.equal(result.checkpoint.status,'absent');assert.deepEqual(result.errors,[]);assert.deepEqual(readdirSync(f.root),before);assert.ok(!existsSync(join(f.root,'calls.log')));assert.ok(!existsSync(join(f.root,'host')));
 }finally{f.close();}
});
test('missing environment names are reported without values; execution remains strict',()=>{
 const f=fixture();const key='WEAVE_CHECK_TEST_SECRET';const old=process.env[key];try{
 change(f,c=>c.environmentKeys=[key]);delete process.env[key];const missing=checkConfig(f.config);assert.equal(missing.status,'blocked');assert.deepEqual(missing.environment.missing,[key]);assert.throws(()=>runConfig(f.config));
 process.env[key]='private-test-value';const present=checkConfig(f.config);assert.equal(present.status,'configured');assert.ok(!JSON.stringify(present).includes('private-test-value'));assert.deepEqual(present.environment.missing,[]);
 }finally{if(old===undefined)delete process.env[key];else process.env[key]=old;f.close();}
});
test('shared preparation preserves prototype-like variable names without invoking commands',()=>{
 const f=fixture();try{
 change(f,c=>c.environmentKeys=['__proto__']);
 const env=Object.create(null);env.__proto__='ordinary-value';
 const result=spawnSync(process.execPath,['--no-env-file',join(here,'run.mjs'),'check',f.config],{encoding:'utf8',env,timeout:10000});
 assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout).environment.missing,[]);
 }finally{f.close();}
});
test('source, executable, configuration, pending, corruption and lock blockers are distinct',()=>{
 const f=fixture();try{
 rmSync(join(f.root,'source.txt'));assert.ok(checkConfig(f.config).errors.includes('SELECTED_SOURCE_GAP'));writeFileSync(join(f.root,'source.txt'),'one');
 const nonexe=join(f.root,'not-executable');writeFileSync(nonexe,'must never run');chmodSync(nonexe,0o600);change(f,c=>c.actor=[nonexe]);assert.ok(checkConfig(f.config).errors.includes('ACTOR_EXECUTABLE_UNAVAILABLE'));
 change(f,c=>c.actor=[process.execPath]);mkdirSync(join(f.root,'host'));const cp=emptyCheckpoint();cp.attempts=1;cp.pending='unsettled';writeFileSync(join(f.root,'host/checkpoint.json'),encodeCheckpoint(cp));assert.ok(checkConfig(f.config).errors.includes('RECOVERY_REQUIRED'));
 writeFileSync(join(f.root,'host/checkpoint.json'),'invalid');assert.ok(checkConfig(f.config).errors.includes('CHECKPOINT_INVALID_OR_UNREADABLE'));rmSync(join(f.root,'host/checkpoint.json'));mkdirSync(join(f.root,'host/service.lock'));assert.ok(checkConfig(f.config).errors.includes('EXISTING_OWNER_OR_HOST_LOCK'));
 change(f,c=>c.timeoutMs=-1);assert.equal(checkConfig(f.config).configuration,'invalid');
 }finally{f.close();}
});
test('CLI help succeeds, configured check exits0 and blocked check exits2 with JSON only',()=>{
 const f=fixture();try{
 const help=cli('--help');assert.equal(help.status,0);assert.equal(help.stderr,'');assert.match(help.stdout,/check CONFIG/);
 const good=cli('check',f.config);assert.equal(good.status,0);assert.equal(good.stderr,'');assert.equal(JSON.parse(good.stdout).status,'configured');
 const bad=cli('check',join(f.root,'missing.json'));assert.equal(bad.status,2);assert.equal(bad.stderr,'');assert.equal(JSON.parse(bad.stdout).status,'blocked');assert.ok(!existsSync(join(f.root,'host')));
 }finally{f.close();}
});
