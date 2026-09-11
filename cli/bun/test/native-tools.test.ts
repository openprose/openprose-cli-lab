import { test, expect } from "bun:test";
import { installedProtocol } from "../src/adapters/protocols";
import prime from "../../shared/fixtures/adapters/tool-lifecycle/prime.json";
import omp from "../../shared/fixtures/adapters/tool-lifecycle/omp.json";
for (const [id, frames] of [["prime/rpc",prime],["omp/rpc",omp]] as const) {
  const parser = () => installedProtocol(id,id==="prime/rpc"?"prime-agent 0.7.0":"omp/18.0.9","fixture-tools",new Uint8Array());
  test(`${id} native tools settle only after actual terminal`,()=>{
    const p=parser();for(const f of frames.slice(0,frames.findIndex(f=>f.type==="agent_end"))) p.accept(f);
    expect(p.terminalEventObserved).toBe(false);p.accept(frames.find(f=>f.type==="agent_end"));expect(p.terminalEventObserved).toBe(true);
  });
  test(`${id} rejects unmatched tool completion`,()=>{
    const p=parser(), bad=structuredClone(frames) as any[];
    bad.find(f=>f.type==="tool_execution_end").toolCallId="wrong";
    expect(()=>bad.forEach(f=>p.accept(f))).toThrow();
  });
  test(`${id} rejects invented tool-result content`,()=>{
    const p=parser(), bad=structuredClone(frames) as any[];
    bad.find(f=>f.type==="message_end" && f.message?.role==="toolResult").message.content=[{type:"text",text:"invented"}];
    expect(()=>bad.forEach(f=>p.accept(f))).toThrow();
  });
  test(`${id} rejects terminal before tools settle`,()=>{
    const p=parser(), cut=frames.findIndex(f=>f.type==="tool_execution_start");
    expect(()=>[...frames.slice(0,cut+1),frames.find(f=>f.type==="agent_end")].forEach(f=>p.accept(f))).toThrow();
  });
}
