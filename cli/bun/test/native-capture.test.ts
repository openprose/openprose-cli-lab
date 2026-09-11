import {test,expect} from "bun:test";
import {mkdtempSync,readFileSync,statSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {NativeCapture} from "../src/adapters/native-capture";
test("capture is new private bounded JSON and redacts known strings",()=>{
 const dir=mkdtempSync(join(tmpdir(),"prose-capture-")),path=join(dir,"native.jsonl");
 const capture=new NativeCapture(path,["fixture-secret"],80);
 try{
 capture.write({type:"tool_call",value:"fixture-secret"});
 expect(JSON.parse(readFileSync(path,"utf8")).value).toBe("[REDACTED]");
 expect(statSync(path).mode&0o777).toBe(0o600);
 expect(()=>new NativeCapture(path,[])).toThrow();
 expect(()=>capture.write({payload:"x".repeat(80)})).toThrow();
 }finally{capture.close();rmSync(dir,{recursive:true});}
});
