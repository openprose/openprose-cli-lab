import {openSync,writeSync,closeSync} from "node:fs";
import {isAbsolute} from "node:path";
import {failure} from "../core/errors";
export class NativeCapture {
 private fd:number|undefined;
 private bytes=0;
 constructor(path:string|undefined,private readonly secrets:readonly string[],private readonly limit=64*1024*1024){
  if(path===undefined)return;
  if(!isAbsolute(path))throw failure("CONFIG_INVALID",{reason:"Native log path must be absolute"});
  this.fd=openSync(path,"wx",0o600);
 }
 write(record:unknown):void{
  if(this.fd===undefined)return;
  const scrub=(value:unknown):unknown=>typeof value === "string" ? this.secrets.filter(Boolean).reduce((text,secret)=>text.split(secret).join("[REDACTED]"),value):Array.isArray(value)?value.map(scrub):value&&typeof value==="object"?Object.fromEntries(Object.entries(value).map(([key,item])=>[key,scrub(item)])):value;
  const bytes=Buffer.from(JSON.stringify(scrub(record))+"\n");
  if(this.bytes+bytes.length>this.limit)throw failure("HARNESS_FAILED",{reason:"Native capture limit exceeded"});
  let offset=0;while(offset<bytes.length)offset+=writeSync(this.fd,bytes,offset);
  this.bytes+=bytes.length;
 }
 close():void{if(this.fd!==undefined){closeSync(this.fd);this.fd=undefined;}}
}
