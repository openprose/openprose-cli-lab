// Read-only diagnostics compared on the same filesystem; runtime identity is intentionally different.
import {checkConfig} from '../local/check.mjs';
import {mkdtempSync,writeFileSync,readFileSync,rmSync,mkdirSync,chmodSync,existsSync,readdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {emptyCheckpoint} from '../bun/index.mjs';
import {encodeCheckpoint} from '../bun/host.mjs';
const here=fileURLToPath(new URL('.',import.meta.url)),binary=process.argv[2]??join(here,'target/debug/weave-rust-local');
const fixture=process.argv[3]??join(dirname(binary),'weave-local-test-fixture');
const root=mkdtempSync(join(tmpdir(),'weave-native-check-parity-')),path=join(root,'config.json');
let config={schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['source.txt','report.txt'],capabilityVersion:'synthetic',assessor:[fixture,'assess'],actor:[fixture,'act'],checkpointDirectory:'host',maxAttempts:2,environmentKeys:[]};
let checks=0;
function check(label,selectedPath=path,environment={}) {
 const expected=checkConfig(selectedPath);
 const child=spawnSync(binary,['check',selectedPath],{encoding:'utf8',env:environment,timeout:5000});assert.ifError(child.error);assert.equal(child.stderr,'',label);assert.equal(child.status,expected.status==='configured'?0:2,label);
 const actual=JSON.parse(child.stdout);assert.deepEqual(actual.runtime,{name:'rust',version:'0.0.0'});delete actual.runtime;delete expected.runtime;assert.deepEqual(actual,expected,label);checks++;
}
const save=()=>writeFileSync(path,JSON.stringify(config));
try {
 for(const name of ['kernel.md','program.md','source.txt','report.txt'])writeFileSync(join(root,name),'opaque');save();
 const before=readdirSync(root);check('configured');assert.deepEqual(readdirSync(root),before);assert.equal(existsSync(join(root,'host')),false);
 check('missing config',join(root,'absent'));
 config.environmentKeys=['WEAVE_CHECK_PARITY_SECRET'];save();delete process.env.WEAVE_CHECK_PARITY_SECRET;check('missing environment');
 process.env.WEAVE_CHECK_PARITY_SECRET='private-value';check('present environment',path,{WEAVE_CHECK_PARITY_SECRET:'private-value'});delete process.env.WEAVE_CHECK_PARITY_SECRET;
 config.environmentKeys=[];save();rmSync(join(root,'source.txt'));check('source gap');writeFileSync(join(root,'source.txt'),'opaque');
 const nonexe=join(root,'nonexe');writeFileSync(nonexe,'not launched');chmodSync(nonexe,0o600);const actor=config.actor;config.actor=[nonexe];save();check('nonexecutable');config.actor=[root];save();check('directory executable');config.actor=actor;save();
 mkdirSync(join(root,'host'));const cp=emptyCheckpoint();writeFileSync(join(root,'host/checkpoint.json'),encodeCheckpoint(cp));check('present checkpoint');cp.pending='unsettled';cp.attempts=1;writeFileSync(join(root,'host/checkpoint.json'),encodeCheckpoint(cp));check('pending checkpoint');
 mkdirSync(join(root,'host/service.lock'));mkdirSync(join(root,'host/lock'));check('both locks and pending');
 writeFileSync(join(root,'host/checkpoint.json'),'corrupt');check('corrupt checkpoint');
 rmSync(join(root,'host/checkpoint.json'));rmSync(join(root,'host/service.lock'),{recursive:true});rmSync(join(root,'host/lock'),{recursive:true});
 for(const [field,value] of [['timeoutMs',0],['maxAttempts',-1],['maxOutputBytes',null],['environment',{}],['checkpointDirectory',''],['environmentKeys',['bad-key']],['contracts',[]]]){const original=config[field];config[field]=value;save();check(`invalid ${field}`);if(original===undefined)delete config[field];else config[field]=original;}
 save();assert.equal(existsSync(join(root,'calls.log')),false);console.log(`${checks} native Rust/Bun readonly check comparisons passed`);
}finally{rmSync(root,{recursive:true,force:true});delete process.env.WEAVE_CHECK_PARITY_SECRET;}
