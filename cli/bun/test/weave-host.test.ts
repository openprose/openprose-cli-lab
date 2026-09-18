import { test, expect } from "bun:test";
import { mkdtemp, writeFile, chmod, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { parseEntrypoint } from "../src/core/args";
import { runWeaveHost, strictHostJson, weaveInvocation } from "../src/core/weave-host";
import { runCli } from "../src/cli";
import corpus from "../../shared/fixtures/weave-host-v1.json";

async function fixture(source:string,patch:Record<string,unknown>={}){
 const root=await mkdtemp(join(tmpdir(),"weave-host-test-"));const host=join(root,"host"),binding=join(root,"binding.json");
 const bytes=`#!${process.execPath}\n${source}`;await writeFile(host,bytes);await chmod(host,0o700);
 const base={schema:"openprose.weave-host-binding/1",executable:host,sha256:createHash("sha256").update(bytes).digest("hex"),environmentKeys:[],timeoutMs:1000,maxOutputBytes:65536,...patch};await writeFile(binding,JSON.stringify(base));
 const out:Buffer[]=[],err:Buffer[]=[];const deps={env:{SECRET:"hidden",KEPT:"yes"},writeStdout:(s:string)=>out.push(Buffer.from(s)),writeStderr:(s:string)=>err.push(Buffer.from(s)),writeStdoutBytes:(b:Uint8Array)=>{out.push(Buffer.from(b));},writeStderrBytes:(b:Uint8Array)=>{err.push(Buffer.from(b));}};
 return {root,host,binding,base,deps,out,err,args:["--host-binding",binding,"check",join(root,"missing-config.json")],close:()=>rm(root,{recursive:true,force:true})};
}
test("shared grammar and opaque language fixtures retain routing",()=>{
 for(const c of corpus.cases){if(c.id.startsWith("invalid-grammar")||c.id.startsWith("invalid-global")){const parsed=parseEntrypoint(c.argv);expect(parsed.kind).toBe("weave");if(parsed.kind==="weave")expect(weaveInvocation(parsed.argv,parsed.global)).toBeNull();}}
 for(const id of ["language-routing-47","language-routing-48"]){const c=corpus.cases.find(c=>c.id===id)!;const parsed=parseEntrypoint(c.argv);expect(parsed.kind).toBe("language");if(parsed.kind==="language")expect(parsed.argv).toEqual(["prose",...c.argv.filter((_,i)=>!(i===0&&c.argv[0]==="--"))]);}
 expect(parseEntrypoint(["init","--model","literal"])).toEqual({kind:"language",global:{},argv:["prose","init","--model","literal"]});
 expect(weaveInvocation(["--help"],{})).toEqual({help:true});
 for(const n of ["0","01","1e2","+1","3600001"]){expect(weaveInvocation(["--host-binding","/x","serve","/c","--poll-ms",n,"--max-steps","1"],{})).toBeNull();}
});
test("strict JSON rejects duplicate escapes, noncanonical integers and invalid scalar strings",()=>{
 for(const raw of ['{"x":1,"\\u0078":2}','{"x":1.0}','{"x":1e0}','{"x":-0}','{"x":NaN}','{"x":"\\ud800"}','{"x":"\\udfff"}','\ufeff{}','{"x":01}','{"x":1,}'])expect(()=>strictHostJson(raw)).toThrow();
 expect(strictHostJson('{"x":"😀","n":123}')).toEqual({x:"😀",n:123});
});
test("raw bytes, argv, isolated selected environment and normal exit are preserved",async()=>{
 const f=await fixture('process.stdout.write(Buffer.from([255,0,128]));process.stderr.write(JSON.stringify({argv:process.argv.slice(2),cwd:process.cwd(),kept:process.env.KEPT,secret:process.env.SECRET}));process.exitCode=42;',{environmentKeys:["KEPT"]});
 try{expect(await runWeaveHost(f.args,{},f.deps)).toBe(42);expect(Buffer.concat(f.out)).toEqual(Buffer.from([255,0,128]));expect(JSON.parse(Buffer.concat(f.err).toString())).toEqual({argv:f.args.slice(2),cwd:await import("node:fs/promises").then(m=>m.realpath(f.root)),kept:"yes"});}finally{await f.close();}
});
test("bridge ignores runner config, Unix rejection and grammar precede binding",async()=>{
 const f=await fixture('process.stdout.write("ok")');try{
 expect(await runCli(["cli","weave",...f.args],{...f.deps,processCwd:"/does-not-exist",userConfigPath:"/does-not-exist",clock:{now:()=>"",monotonicMs:()=>0},ids:{invocationId:()=>"unused"}})).toBe(0);
 expect(await runWeaveHost(f.args,{}, {...f.deps,platform:"win32"})).toBe(2);
 expect(Buffer.concat(f.err).toString()).toContain("WEAVE_HOST_UNSUPPORTED_PLATFORM\n");
 expect(await runWeaveHost(["--host-binding","/missing","bogus","/config"],{},f.deps)).toBe(2);
 expect(Buffer.concat(f.err).toString()).toContain("WEAVE_HOST_INVOCATION_INVALID\n");
 }finally{await f.close();}
});
test("binding failures never spawn and diagnostics are fixed",async()=>{
 const f=await fixture('throw Error("must not run")');try{
 for(const raw of [JSON.stringify({...f.base,sha256:"a".repeat(64)}),JSON.stringify({...f.base,extra:"secret"}),JSON.stringify({...f.base,environmentKeys:["A","A"]}),'{"schema":"secret","schema":"duplicate"}',"{}".repeat(40000)]){
 f.err.length=0;await writeFile(f.binding,raw);expect(await runWeaveHost(f.args,{},f.deps)).toBe(2);expect(Buffer.concat(f.err).toString()).toBe("WEAVE_HOST_BINDING_INVALID\n");}
 }finally{await f.close();}
});
test("combined output limit forwards at most exact remaining bytes",async()=>{
 const f=await fixture('process.stdout.write("abcdef");process.stderr.write("ghijkl");setInterval(()=>{},1000)',{maxOutputBytes:7});try{expect(await runWeaveHost(f.args,{},f.deps)).toBe(125);expect(Buffer.concat(f.out).length+Buffer.concat(f.err).length-"WEAVE_HOST_OUTPUT_LIMIT\n".length).toBe(7);}finally{await f.close();}
});
test("timeout and explicit cancellation are bounded and reap host",async()=>{
 for(const signal of [null,"SIGINT","SIGTERM"]){const f=await fixture('process.on("SIGTERM",()=>{});setInterval(()=>{},1000)',{timeoutMs:100});const controller=new AbortController();const started=performance.now();const timer=signal?setTimeout(()=>controller.abort(signal),50):undefined;
 try{expect(await runWeaveHost(f.args,{}, {...f.deps,cancellationSignal:controller.signal})).toBe(signal==="SIGINT"?130:signal==="SIGTERM"?143:124);expect(performance.now()-started).toBeLessThan(2000);}finally{if(timer)clearTimeout(timer);await f.close();}}
},8000);
test("inherited pipe deadline applies after direct child exits",async()=>{
 const f=await fixture('Bun.spawn({cmd:[process.execPath,"-e","setTimeout(()=>{},600)"],stdin:"ignore",stdout:"inherit",stderr:"inherit"});process.exit(0)',{timeoutMs:100});try{const start=performance.now();expect(await runWeaveHost(f.args,{},f.deps)).toBe(124);expect(performance.now()-start).toBeLessThan(500);}finally{await f.close();}
});
test("forwarding failure is fixed IO_FAILED",async()=>{
 const f=await fixture('process.stdout.write("bytes");setInterval(()=>{},1000)');try{expect(await runWeaveHost(f.args,{}, {...f.deps,writeStdoutBytes:()=>{throw Error("private path");}})).toBe(125);expect(Buffer.concat(f.err).toString()).toBe("WEAVE_HOST_IO_FAILED\n");}finally{await f.close();}
});
test("SIGTERM-resistant ready host is killed after bounded grace",async()=>{
 const f=await fixture('process.on("SIGTERM",()=>{});process.stdout.write("ready");setInterval(()=>{},1000)',{timeoutMs:4000});const controller=new AbortController();let cancelledAt=0;
 try{const result=await runWeaveHost(f.args,{}, {...f.deps,cancellationSignal:controller.signal,writeStdoutBytes:(bytes)=>{f.out.push(Buffer.from(bytes));cancelledAt=performance.now();controller.abort("SIGTERM");}});expect(result).toBe(143);expect(performance.now()-cancelledAt).toBeGreaterThan(900);expect(performance.now()-cancelledAt).toBeLessThan(1800);expect(Buffer.concat(f.err).toString()).toBe("WEAVE_HOST_CANCELLED\n");}finally{await f.close();}
},6000);
test("normal child signal termination returns 128 plus signal without wrapper text",async()=>{
 const f=await fixture('process.kill(process.pid,"SIGTERM")');try{expect(await runWeaveHost(f.args,{},f.deps)).toBe(143);expect(Buffer.concat(f.err).length).toBe(0);}finally{await f.close();}
});
test("actual process rejects FIFO and bounds unread or closed wrapper pipes",async()=>{
 const f=await fixture('while(true)require("node:fs").writeSync(Number(process.env.FD),Buffer.alloc(65536,120));',{timeoutMs:300,maxOutputBytes:16777216,environmentKeys:["FD"]});
 const main=join(import.meta.dir,"../src/main.ts");
 const script=`import subprocess,sys,os,json
root,binding,bun,main=sys.argv[1:]
fifo=root+'/fifo'
os.mkfifo(fifo)
p=subprocess.run([bun,'--no-env-file',main,'cli','weave','--host-binding',fifo,'step','/missing'],capture_output=True,env={},timeout=3)
assert p.returncode==2 and p.stderr==b'WEAVE_HOST_BINDING_INVALID\\n'
for fd in ['1','2']:
 p=subprocess.Popen([bun,'--no-env-file',main,'cli','weave','--host-binding',binding,'step','/missing'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'FD':fd})
 try:
  result=p.wait(timeout=3)
  assert result in (124,125), (fd,result)
 finally:
  if p.poll() is None: p.kill();p.wait()
  p.stdout.close();p.stderr.close()
host=json.load(open(binding))['executable']
source='#!'+bun+'\\nprocess.stdout.write("broken pipe probe");\\n'
open(host,'w').write(source)
v=json.load(open(binding));import hashlib
v['sha256']=hashlib.sha256(source.encode()).hexdigest()
open(binding,'w').write(json.dumps(v))
r,w=os.pipe();os.close(r)
p=subprocess.Popen([bun,'--no-env-file',main,'cli','weave','--host-binding',binding,'step','/missing'],stdout=w,stderr=subprocess.PIPE,env={'FD':'1'})
os.close(w)
_,err=p.communicate(timeout=3)
assert p.returncode==125 and err==b'WEAVE_HOST_IO_FAILED\\n', (p.returncode,err)
print('passed')
`;
 try{const child=Bun.spawn({cmd:["/usr/bin/python3","-c",script,f.root,f.binding,process.execPath,main],env:{},stdin:"ignore",stdout:"pipe",stderr:"pipe"});const [code,out,err]=await Promise.all([child.exited,new Response(child.stdout).text(),new Response(child.stderr).text()]);expect(err).toBe("");expect(code).toBe(0);expect(out).toBe("passed\n");}finally{await f.close();}
},12000);

test("observed excess wins over a synchronously throwing prefix writer",async()=>{
 const f=await fixture('process.stdout.write("12345")',{maxOutputBytes:4});
 try{expect(await runWeaveHost(f.args,{}, {...f.deps,writeStdoutBytes:()=>{throw Error("broken");}})).toBe(125);expect(Buffer.concat(f.err).toString()).toBe("WEAVE_HOST_OUTPUT_LIMIT\n");}finally{await f.close();}
});
