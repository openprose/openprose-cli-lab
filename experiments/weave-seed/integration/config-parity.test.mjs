/** Read-only invalid-input matrix across real Bun and Rust command processes. */
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const seed=fileURLToPath(new URL('../',import.meta.url)),rust=process.argv[2];
if(!rust||!isAbsolute(rust))throw Error('absolute Rust local binary required');
const root=mkdtempSync(join(tmpdir(),'weave-config-matrix-')),path=join(root,'config.json');
let count=0;
try {
 for(const file of ['kernel.md','program.md','evidence.txt'])writeFileSync(join(root,file),'synthetic');
 const base={schema:1,root:'.',kernel:'kernel.md',contracts:['program.md'],evidence:['evidence.txt'],capabilityVersion:'configuration-parity',assessor:[process.execPath],actor:[process.execPath],checkpointDirectory:'host',maxAttempts:2,environmentKeys:[]};
 const baseline=readdirSync(root).sort();
 function check(value,label,raw=false) {
  writeFileSync(path,raw?value:JSON.stringify(value));
  const invoke=(executable,args)=>{
   const r=spawnSync(executable,args,{env:{},encoding:'utf8',timeout:5000,maxBuffer:1048576});assert.ifError(r.error);assert.equal(r.stderr,'',label);
   const record=JSON.parse(r.stdout);delete record.runtime;return {exit:r.status,record};
  };
  const bun=invoke(process.execPath,['--no-env-file',join(seed,'local/run.mjs'),'check',path]);
  const native=invoke(rust,['check',path]);assert.deepEqual(native,bun,label);count++;
 }
 check(base,'valid');
 const values=[null,false,true,0,-1,1,1.5,Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER+1,'',' ','x','\0',[],{},[''],['x'],['kernel.md']];
 for(const field of [...Object.keys(base),'ttlMs','limit','timeoutMs','maxOutputBytes','environment']) {
  for(const value of values)check({...base,[field]:value},`${field}=${JSON.stringify(value)}`);
  const missing={...base};delete missing[field];check(missing,`missing ${field}`);
 }
 for(const raw of ['null','[]','true','1','{}','{','{"schema":1,"schema":2}','\ufeff'+JSON.stringify(base)])check(raw,'raw configuration',true);
 assert.deepEqual(readdirSync(root).sort(),[...baseline,'config.json'].sort());
 console.log(`PASS ${count} readonly Bun/Rust configuration cases; no capability execution or state creation`);
} finally {rmSync(root,{recursive:true,force:true});}
