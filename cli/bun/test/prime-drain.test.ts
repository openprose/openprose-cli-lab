import {test,expect} from "bun:test";
import {installedProtocol} from "../src/adapters/protocols";
import fixture from "../../shared/fixtures/adapters/tool-lifecycle/prime-drain.json";
const copy=<T>(v:T):T=>structuredClone(v);
const make=()=>installedProtocol("prime/rpc","0.7.0",fixture.invocationId,new TextEncoder().encode("PROMPT\n"),true);
const start=()=>{const p=make();expect(new TextDecoder().decode(p.takeStagedStdinBytes!()!)).toContain('"get_state"');p.accept(fixture.stateResponse);expect(new TextDecoder().decode(p.takeStagedStdinBytes!()!)).toBe("PROMPT\n");p.accept(fixture.promptResponse);expect(p.stdinCloseRequested).toBe(true);return p;};
test("Prime native segments require actual final stop, drained queue and process settlement",()=>{
 const p=start();for(const f of fixture.frames){p.accept(f);expect(p.terminalEventObserved).toBe(false);}expect(p.settleProcess!(0)).toEqual({type:"session.completed"});expect(p.terminalEventObserved).toBe(true);
 for(const count of [0,13,14,17,18]){const q=start();for(const f of fixture.frames.slice(0,count))q.accept(f);expect(()=>q.settleProcess!(0)).toThrow();}
 const q=start();for(const f of fixture.frames)q.accept(f);expect(()=>q.settleProcess!(1)).toThrow();
});
test("failed or missing identity never dispatches a prompt",()=>{
 for(const mutate of [(x:any)=>x.id="wrong",(x:any)=>x.command="prompt",(x:any)=>x.success=false,(x:any)=>x.data.sessionId="",(x:any)=>x.data.isStreaming=true,(x:any)=>x.data.messageCount=1]){
  const p=make();p.takeStagedStdinBytes!();const r=copy(fixture.stateResponse);mutate(r);expect(()=>p.accept(r)).toThrow();expect(p.takeStagedStdinBytes!()).toBeNull();
 }
 const p=make();p.takeStagedStdinBytes!();expect(()=>p.settleProcess!(0)).toThrow();expect(p.takeStagedStdinBytes!()).toBeNull();
});
test("snapshot-only context requires independent target, known child, exact queued body and history",()=>{
 for(const mutate of [(a:any[])=>a[17].messages[0].details.target.sessionId="other",(a:any[])=>a[17].messages[0].details.from.activeSessionId="other",(a:any[])=>a[17].messages[0].details.from.sessionName="other",(a:any[])=>a[11].actions.steering=["wrong"],(a:any[])=>a[17].messages[0].content+="changed",(a:any[])=>a[17].messages[1].content[0].text="changed",(a:any[])=>a[17].messages[0].extra=true,(a:any[])=>a[17].messages[0].timestamp="2",(a:any[])=>a[15].message.stopReason="aborted"]){
  const frames=copy(fixture.frames);mutate(frames);const p=start();expect(()=>{for(const f of frames)p.accept(f);p.settleProcess!(0);}).toThrow();
 }
});
test("active/new work, pending tools and post-terminal observations cannot manufacture completion",()=>{
 for(const suffix of [{type:"session_action_update",actions:{queuedCount:0,steering:[],followUps:[],active:{kind:"turn",phase:"running"}}},{type:"message_start",message:{role:"assistant",content:[]}},{type:"turn_start"}]){
  const p=start();for(const f of fixture.frames)p.accept(f);expect(()=>p.accept(suffix)).toThrow();
 }
 const p=start();for(const f of fixture.frames.slice(0,7))p.accept(f);expect(()=>p.accept(fixture.frames[13])).toThrow();
 const q=start();for(const f of fixture.frames)q.accept(f);q.settleProcess!(0);expect(()=>q.accept(fixture.frames.at(-1))).toThrow();
});
import {primeHistorySame,PrimeDrain} from "../src/adapters/prime-drain";
test("native history accounting projection is typed and never changes execution evidence",()=>{
 const usage={input:1,output:2,cacheRead:3,cacheWrite:0,totalTokens:6,cost:{input:0.1,output:0.2,cacheRead:0.03,cacheWrite:0,total:0.33}};
 const a={role:"assistant",content:[{type:"text",text:"same"}],stopReason:"stop",model:"fixture",usage};const b=copy(a);b.usage.input=200;expect(primeHistorySame(a,b)).toBe(true);
 for(const mutate of [(x:any)=>delete x.usage.input,(x:any)=>x.usage.extra=1,(x:any)=>x.usage.input="1",(x:any)=>x.usage.cost.total=-1,(x:any)=>x.content[0].text="different",(x:any)=>x.model="other",(x:any)=>delete x.usage]){const x=copy(b);mutate(x);expect(primeHistorySame(a,x)).toBe(false);}
 const p=new PrimeDrain(fixture.sessionId);p.resumed=true;p.child((fixture.frames[10] as any).child);p.queue((fixture.frames[11] as any).actions);const ms=(fixture.frames[17] as any).messages;expect(p.history(ms,ms.slice(1))).toBe(true);expect(p.history(ms,ms.slice(1))).toBe(false);
});
