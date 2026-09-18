/** Generate an explicit private host binding. No discovery, execution or environment reads. */
import { constants, statSync, realpathSync, accessSync, openSync, fstatSync, readSync, writeSync, fsyncSync, closeSync } from 'node:fs';
import { dirname, basename, isAbsolute, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
const fail=()=>{throw Error('HOST_BINDING_SETUP_REJECTED');};
const MAX_EXECUTABLE_BYTES=536870912;
const required=['host','config','output','environmentKeys','timeoutMs','maxOutputBytes','prose'];
function scalar(text){
 for(let i=0;i<text.length;i++){
  const c=text.charCodeAt(i);
  if(c>=0xd800&&c<=0xdbff){const d=text.charCodeAt(++i);if(!(d>=0xdc00&&d<=0xdfff))return false;}
  else if(c>=0xdc00&&c<=0xdfff)return false;
 }
 return true;
}
function path(value){return typeof value==='string'&&scalar(value)&&value.length>0&&Buffer.byteLength(value)<=4096&&!value.includes('\0')&&isAbsolute(value);}
function integer(value,max){return Number.isSafeInteger(value)&&value>=1&&value<=max;}
function executable(selected,hash){
 if(!path(selected)||!statSync(selected).isFile())fail();
 // Check kind before realpath: Bun 1.3.5 can wait on a FIFO during realpath.
 const canonical=realpathSync(selected);if(!path(canonical))fail();accessSync(canonical,constants.X_OK);
 const fd=openSync(canonical,constants.O_RDONLY|constants.O_NONBLOCK);
 try{
  const info=fstatSync(fd);if(!info.isFile()||info.size>MAX_EXECUTABLE_BYTES)fail();
  if(!hash)return {path:canonical};
  const digest=createHash('sha256'),buffer=Buffer.alloc(65536);let total=0,count;
  while((count=readSync(fd,buffer,0,buffer.length,null))>0){total+=count;if(total>MAX_EXECUTABLE_BYTES)fail();digest.update(buffer.subarray(0,count));}
  return {path:canonical,sha256:digest.digest('hex')};
 }finally{closeSync(fd);}
}
export function createHostBinding(options){
 try{
  if(options===null||typeof options!=='object'||Array.isArray(options)||Object.keys(options).length!==required.length||required.some(k=>!Object.hasOwn(options,k)))fail();
  const {host,config,output,environmentKeys,timeoutMs,maxOutputBytes,prose}=options;
  if(!path(config)||!statSync(config).isFile()||!path(output)||!integer(timeoutMs,86400000)||!integer(maxOutputBytes,16777216))fail();
  if(!Array.isArray(environmentKeys)||environmentKeys.length>128||new Set(environmentKeys).size!==environmentKeys.length||environmentKeys.some(k=>typeof k!=='string'||!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(k)))fail();
  const selected=executable(host,true),runner=executable(prose,false);
  const parent=realpathSync(dirname(output));if(!statSync(parent).isDirectory())fail();
  const target=resolve(parent,basename(output));if(!path(target))fail();
  const binding={schema:'openprose.weave-host-binding/1',executable:selected.path,sha256:selected.sha256,environmentKeys:[...environmentKeys],timeoutMs,maxOutputBytes};
  const bytes=Buffer.from(JSON.stringify(binding,null,2)+'\n');if(bytes.length>65536)fail();
  // O_EXCL rejects every existing destination, including dangling symlinks.
  // No cleanup of a partially written file: retain it for inspection, without
  // an acceptance response, and require a fresh output path on the next attempt.
  const fd=openSync(target,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL,0o600);
  try{let offset=0;while(offset<bytes.length){const count=writeSync(fd,bytes,offset,bytes.length-offset);if(count<=0)fail();offset+=count;}fsyncSync(fd);}finally{closeSync(fd);}
  const command=(...args)=>[runner.path,'cli','weave','--host-binding',target,...args];
  return {schema:'openprose.weave-host-setup/1',status:'binding-created-not-executed',binding:target,host:selected.path,hostSha256:selected.sha256,config,hostExecuted:false,cliProbed:false,providerVerified:false,commands:{check:command('check',config),status:command('status',config),step:command('step',config),serve:command('serve',config,'--poll-ms','1000','--max-steps','1')}};
 }catch{fail();}
}
export function parseHostBindingArguments(args){
 const flags={'--host':'host','--config':'config','--output':'output','--environment-keys':'environmentKeys','--timeout-ms':'timeoutMs','--max-output-bytes':'maxOutputBytes','--prose':'prose'};
 const values=Object.create(null);
 if(args.length!==14)fail();
 for(let i=0;i<args.length;i+=2){const key=Object.hasOwn(flags,args[i])?flags[args[i]]:undefined;if(!key||Object.hasOwn(values,key)||typeof args[i+1]!=='string')fail();values[key]=args[i+1];}
 if(required.some(k=>!Object.hasOwn(values,k)))fail();
 if(Buffer.byteLength(values.environmentKeys)>32768)fail();
 try{values.environmentKeys=JSON.parse(values.environmentKeys);}catch{fail();}
 for(const key of ['timeoutMs','maxOutputBytes']){if(!/^[1-9][0-9]*$/.test(values[key]))fail();values[key]=Number(values[key]);}
 return values;
}
const usage='Usage: bun --no-env-file host-binding.mjs --host ABS --config ABS --output NEW_ABS --environment-keys JSON_ARRAY --timeout-ms N --max-output-bytes N --prose ABS';
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{const args=process.argv.slice(2);if(args.length===1&&args[0]==='--help')console.log(usage);else console.log(JSON.stringify(createHostBinding(parseHostBindingArguments(args))));}
 catch{console.error('HOST_BINDING_SETUP_REJECTED');process.exitCode=1;}
}
