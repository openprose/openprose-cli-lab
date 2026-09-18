// Optional development-only differential test. Rust library/CLI do not use Bun.
import { bindFiles } from '../integration/binding.mjs';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync, symlinkSync, linkSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import assert from 'node:assert/strict';
const here=fileURLToPath(new URL('.',import.meta.url));
const binary=process.argv[2] ?? join(here,'target/debug/weave-file-binding-experiment');
const root=mkdtempSync(join(tmpdir(),'weave-binding-parity-'));
const outside=mkdtempSync(join(tmpdir(),'weave-binding-outside-'));
let checks=0;
const fixture=JSON.parse(readFileSync(join(here,'fixture-v1.json'),'utf8'));
function check(config, now, label) {
  let expected, error;
  try { const b=bindFiles(config); expected={binding:b.binding,evidence:b.observe(now)}; } catch(e) { error=e; }
  const request=JSON.stringify({...config,now});
  const child=spawnSync(binary,[],{input:request,encoding:'utf8',env:{},timeout:5000,maxBuffer:4*1024*1024});
  assert.ifError(child.error);
  if(error) assert.notEqual(child.status,0,label);
  else { assert.equal(child.status,0,`${label}: ${child.stderr}`);assert.deepEqual(JSON.parse(child.stdout),expected,label); }
  checks++;
}
try {
 for(const [name,content] of Object.entries(fixture.files)) writeFileSync(join(root,name),content);
 for(const row of fixture.cases) {const {name,now,gap,...config}=row;check({root,...config},now,name);}
 const config={root,kernel:'kernel.md',contracts:['contract.md'],evidence:['evidence.json'],policy:'v1'};
 writeFileSync(join(root,'invalid'),Buffer.from([0xff]));check({...config,evidence:['invalid']},1,'invalid UTF8');
 writeFileSync(join(outside,'secret'),'outside');symlinkSync(join(outside,'secret'),join(root,'escape'));check({...config,evidence:['escape']},1,'symlink escape');
 symlinkSync(join(root,'evidence.json'),join(root,'inside'));check({...config,evidence:['inside']},1,'internal symlink');
 linkSync(join(outside,'secret'),join(root,'hardlink'));check({...config,evidence:['hardlink']},1,'hardlink matches trusted semantics');
 check({...config,evidence:[join(outside,'secret')]},1,'absolute escape');
 check({...config,kernel:resolve(root,'kernel.md')},1,'absolute inside');
 check({...config,evidence:['nul\u0000path']},1,'NUL path gap');
 for(const now of [-1,1.5,Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER+1,null]) check(config,now,`time ${now}`);
 for(const limit of [0,-1,1.5,Number.MAX_SAFE_INTEGER+1,null]) check({...config,limit},1,`limit ${limit}`);
 for(const policy of ['', '\ufeff','\u0085']) check({...config,policy},1,'policy whitespace');
 for(const contracts of [[],[''],[null]]) check({...config,contracts},1,'bad contract');
 check({...config,root:join(root,'absent')},1,'missing root');
 check({...config,root:join(root,'kernel.md')},1,'root regular file');
 // Shared source files change over successive observations; equality includes hashes and serialized payload.
 for(let i=0;i<100;i++){writeFileSync(join(root,'evidence.json'),JSON.stringify({i,text:i%2?'😺':'line\nquote"'}));check(config,i,'varying content');}
 console.log(`${checks} Bun/Rust binding comparisons passed`);
} finally {rmSync(root,{recursive:true,force:true});rmSync(outside,{recursive:true,force:true});}
