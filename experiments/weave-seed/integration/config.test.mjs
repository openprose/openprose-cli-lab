import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { readConfigBytes, parseConfigBytes, MAX_CONFIG_BYTES } from './config.mjs';
const root=mkdtempSync(join(tmpdir(),'weave-config-bounds-'));
try {
 const file=join(root,'config.json');writeFileSync(file,'{"schema":1}');
 assert.deepEqual(parseConfigBytes(readConfigBytes(file)),{schema:1});
 symlinkSync(file,join(root,'alias'));assert.deepEqual(parseConfigBytes(readConfigBytes(join(root,'alias'))),{schema:1});
 assert.throws(()=>readConfigBytes(root));
 writeFileSync(file,Buffer.alloc(MAX_CONFIG_BYTES+1,32));assert.throws(()=>readConfigBytes(file),/1 MiB/);
 writeFileSync(file,Buffer.from([123,34,120,34,58,34,255,34,125]));assert.throws(()=>parseConfigBytes(readConfigBytes(file)));
 writeFileSync(file,'\ufeff{"schema":1}');assert.throws(()=>parseConfigBytes(readConfigBytes(file)));assert.deepEqual(parseConfigBytes(readConfigBytes(file),{stripBOM:true}),{schema:1});
 if(process.platform!=='win32') {
  const fifo=join(root,'fifo');const made=spawnSync('/usr/bin/mkfifo',[fifo],{timeout:1000});assert.equal(made.status,0,made.stderr?.toString());
  const start=Date.now();assert.throws(()=>readConfigBytes(fifo),/regular file/);assert.ok(Date.now()-start<1000);
 }
 console.log('PASS bounded regular configuration, aliases, oversized and invalid UTF-8 input, explicit BOM modes, nonblocking FIFO rejection');
} finally {rmSync(root,{recursive:true,force:true});}
