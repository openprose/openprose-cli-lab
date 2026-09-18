import assert from 'node:assert/strict';
import { mkdtempSync,writeFileSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { bindFiles } from './binding.mjs';
const root=mkdtempSync(join(tmpdir(),'weave-binding-'));
try {
  for(const [file,text] of Object.entries({'kernel.md':'kernel rules','program.md':'require a report','state.json':'{"ok":true}','report.md':'absent'}))writeFileSync(join(root,file),text);
  const options={root,kernel:'kernel.md',contracts:['program.md'],evidence:['state.json','report.md'],policy:'fixture-v1'};
  const bound=bindFiles(options);const first=bound.observe(0);assert.equal(first.gap,false);
  assert.equal(bound.observe(1).identity,first.identity);
  for(const path of ['kernel.md','program.md','state.json','report.md']) {
    const before=bound.observe(2);writeFileSync(join(root,path),'changed '+path);assert.notEqual(bound.observe(3).identity,before.identity);
  }
  assert.notEqual(bindFiles({...options,policy:'fixture-v2'}).binding,bound.binding);
  assert.equal(bindFiles({...options,limit:1}).observe(4).gap,true);
  rmSync(join(root,'report.md'));assert.equal(bound.observe(5).gap,true);
  writeFileSync(join(root,'report.md'),Buffer.from([255]));assert.equal(bound.observe(6).gap,true);
  console.log('PASS source binding: kernel, contract, state and report changes; policy invalidation; aggregate limit; missing/invalid UTF-8 evidence');
} finally {rmSync(root,{recursive:true,force:true});}
