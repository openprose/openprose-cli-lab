/** Configuration is trusted policy, but reading it must remain bounded and nonblocking. */
import { openSync, closeSync, fstatSync, readSync, constants } from 'node:fs';
export const MAX_CONFIG_BYTES = 1048576;
export function readConfigBytes(path) {
  const fd=openSync(path,constants.O_RDONLY|constants.O_NONBLOCK);
  try {
    const info=fstatSync(fd);
    if(!info.isFile() || info.size>MAX_CONFIG_BYTES)throw Error('configuration must be a regular file of at most 1 MiB');
    const bytes=Buffer.alloc(Math.min(info.size+1,MAX_CONFIG_BYTES+1));let total=0,count;
    while(total<bytes.length && (count=readSync(fd,bytes,total,bytes.length-total,null))>0)total+=count;
    if(total>info.size || total>MAX_CONFIG_BYTES)throw Error('configuration grew while being read');
    return bytes.subarray(0,total);
  } finally {closeSync(fd);}
}
export function parseConfigBytes(bytes,{stripBOM=false}={}) {
  return JSON.parse(new TextDecoder('utf-8',{fatal:true,ignoreBOM:!stripBOM}).decode(bytes));
}
