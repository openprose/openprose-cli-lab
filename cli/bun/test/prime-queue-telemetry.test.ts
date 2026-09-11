import { test, expect } from "bun:test";
import { NativeToolLifecycle } from "../src/adapters/native-tool-lifecycle";
import fixture from "../../shared/fixtures/adapters/tool-lifecycle/prime-queue-telemetry.json";
test("Prime queue observations preserve active parent state and cannot complete it",()=>{
 const p=new NativeToolLifecycle(false);p.accept({type:"agent_start"});const phase=p.phase;
 for(const r of fixture.valid){expect(p.accept(r)).toBeNull();expect(p.phase).toBe(phase);expect(p.ended).toBe(false);}
});
test("Prime queue observations reject malformed shapes and wrong phase/harness",()=>{
 for(const r of fixture.invalid){const p=new NativeToolLifecycle(false);p.accept({type:"agent_start"});expect(()=>p.accept(r)).toThrow();}
 for(const omp of [true,false]){const p=new NativeToolLifecycle(omp);expect(()=>p.accept(fixture.valid[0])).toThrow();if(omp){p.accept({type:"agent_start"});expect(()=>p.accept(fixture.valid[0])).toThrow();}}
 const p=new NativeToolLifecycle(false);p.accept({type:"agent_start"});p.ended=true;expect(()=>p.accept(fixture.valid[0])).toThrow();
});
import { installedProtocol } from "../src/adapters/protocols";
import frames from "../../shared/fixtures/adapters/tool-lifecycle/prime.json";
test("queue status does not settle pending tools or substitute for parent terminal",()=>{
 const p=installedProtocol("prime/rpc","0.7.0","fixture-tools",new Uint8Array());
 for(const f of frames.slice(0,-1)){p.accept(f);if(f.type==="tool_execution_start")for(const r of fixture.valid)p.accept(r);}
 expect(p.terminalEventObserved).toBe(false);p.accept(frames.at(-1));expect(p.terminalEventObserved).toBe(true);
 const q=installedProtocol("prime/rpc","0.7.0","fixture-tools",new Uint8Array());const cut=frames.findIndex(f=>f.type==="tool_execution_start");for(const f of frames.slice(0,cut+1))q.accept(f);q.accept(fixture.valid[2]);expect(()=>q.accept(frames.at(-1))).toThrow();
});
