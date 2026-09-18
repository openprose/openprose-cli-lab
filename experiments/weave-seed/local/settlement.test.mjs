import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { encodeCheckpoint } from '../bun/host.mjs';
import { emptyCheckpoint } from '../bun/index.mjs';
import { settleConfig, acquireOwner, statusConfig } from './coordinator.mjs';
const root = mkdtempSync(join(tmpdir(), 'weave-settlement-'));
let count = 0, next = 0;
function fixture() {
  const dir = join(root, String(next++)); mkdirSync(dir); mkdirSync(join(dir, 'host'));
  const config = join(dir, 'config.json');
  // Recovery must not require usable capabilities, required sources or selected credentials.
  writeFileSync(config, JSON.stringify({schema:1,checkpointDirectory:'host',environmentKeys:['UNAVAILABLE_SETTLEMENT_KEY'],actor:['/does/not/exist'],assessor:['/does/not/exist'],root:'/missing/source'}));
  const path = join(dir, 'host/checkpoint.json');
  const checkpoint = {...emptyCheckpoint(),binding:'old-binding',evidence:'old-evidence',pending:'attempt-1',attempts:2};
  writeFileSync(path,encodeCheckpoint(checkpoint));
  return {dir,config,path,checkpoint};
}
function test(name, operation) { operation(); count++; console.log('PASS', name); }
try {
  test('completed and not-applied settlement preserve budget, invalidate satisfaction, require no environment or evidence', () => {
    for(const outcome of ['completed','not-applied']) {
      const f=fixture(); const cp=settleConfig(f.config,'old-binding','attempt-1',outcome,'private-receipt-reference');
      assert.equal(cp.attempts,2);assert.equal(cp.pending,null);assert.equal(cp.disposition,'unknown');assert.equal(cp.validUntil,0);
      assert.deepEqual(cp.settlement,{binding:'old-binding',attempt:'attempt-1',outcome,receipt:'private-receipt-reference'});
      assert.equal(statusConfig(f.config).checkpoint.attempts,2);assert(!existsSync(join(f.dir,'host/service.lock')));assert(!existsSync(join(f.dir,'host/lock')));
      assert.deepEqual(new Set(requireFiles(f.dir)),new Set(['config.json','host']));
    }
  });
  test('wrong binding, attempt, outcome and blank receipt preserve exact checkpoint', () => {
    for(const args of [['wrong','attempt-1','completed','r'],['old-binding','wrong','completed','r'],['old-binding','attempt-1','unknown','r'],['old-binding','attempt-1','completed',' '],['old-binding','attempt-1','completed','\u0085\ufeff']]) {
      const f=fixture(),before=readFileSync(f.path);assert.throws(()=>settleConfig(f.config,...args));assert(readFileSync(f.path).equals(before));
      assert(!existsSync(join(f.dir,'host/service.lock')));assert(!existsSync(join(f.dir,'host/lock')));
    }
  });
  test('no-pending settlement cannot be repeated', () => {
    const f=fixture();settleConfig(f.config,'old-binding','attempt-1','completed','r');const before=readFileSync(f.path);
    assert.throws(()=>settleConfig(f.config,'old-binding','attempt-1','completed','r'));assert(readFileSync(f.path).equals(before));
  });
  test('service owner and host lock refuse settlement without lock recovery', () => {
    let f=fixture(),before=readFileSync(f.path);const owner=acquireOwner(f.config);
    assert.throws(()=>settleConfig(f.config,'old-binding','attempt-1','completed','r'),/busy/);assert(readFileSync(f.path).equals(before));assert(existsSync(join(f.dir,'host/service.lock')));owner.release();
    f=fixture();before=readFileSync(f.path);mkdirSync(join(f.dir,'host/lock'));assert.throws(()=>settleConfig(f.config,'old-binding','attempt-1','completed','r'),/busy/);assert(readFileSync(f.path).equals(before));assert(existsSync(join(f.dir,'host/lock')));assert(!existsSync(join(f.dir,'host/service.lock')));
  });
  test('actual CLI empty environment succeeds and fixed-order/mismatch calls fail without providers', () => {
    const f=fixture(); const binary=resolve(import.meta.dir,'run.mjs');
    const run=args=>spawnSync(process.execPath,['--no-env-file',binary,'settle',f.config,...args],{env:{},encoding:'utf8',timeout:3000});
    const before=readFileSync(f.path);const bad=run(['--attempt','attempt-1','--binding','old-binding','--outcome','completed','--receipt','r']);assert.equal(bad.status,1);assert(readFileSync(f.path).equals(before));
    const good=run(['--binding','old-binding','--attempt','attempt-1','--outcome','not-applied','--receipt','reviewed-receipt']);assert.equal(good.status,0,good.stderr);assert.deepEqual(JSON.parse(good.stdout),{status:'settled',attempts:2,pending:null});assert.equal(good.stderr,'');
  });
  console.log(`PASS ${count} provider-free settlement checks`);
} finally {rmSync(root,{recursive:true,force:true});}
function requireFiles(path) {return Array.from(new Bun.Glob('*').scanSync({cwd:path,onlyFiles:false}));}
