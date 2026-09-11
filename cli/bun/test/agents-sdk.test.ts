import {test,expect} from "bun:test";
import {installedProtocol} from "../src/adapters/protocols";
import {installedAdapterDefinition} from "../src/adapters/recipes";
import {buildInstalledAdapterEnvironment} from "../src/adapters/environment";
const start={type:"start",model:"fixture-model",cwd:"/tmp"};
test("SDK validates native settlement and preserves arbitrary prose",()=>{
 const p=installedProtocol("agents-sdk/jsonl","prose-agents-sdk 0.1.0","fixture");
 p.accept(start);p.accept({type:"tool_call",name:"execute_shell"});p.accept({type:"tool_result",name:"execute_shell"});
 expect(p.terminalEventObserved).toBe(false);
 expect(p.accept({type:"final",output:"All done, no JSON."})).toMatchObject({text:"All done, no JSON."});
 expect(p.terminalEventObserved).toBe(true);
 expect(()=>p.accept({type:"final",output:"duplicate"})).toThrow();
});
test("SDK rejects missing start and explicit native errors",()=>{
 const p=installedProtocol("agents-sdk/jsonl","prose-agents-sdk 0.1.0","fixture");
 expect(()=>p.accept({type:"final",output:"done"})).toThrow();
 p.accept(start);expect(()=>p.accept({type:"error",error_type:"TimeoutError"})).toThrow();
 expect(p.terminalEventObserved).toBe(false);
});
test("SDK requires API key and strips competing credentials",()=>{
 const definition=installedAdapterDefinition("agents-sdk/jsonl");
 expect(()=>buildInstalledAdapterEnvironment({definition,ambient:{},credentialGroup:"openai-api-key"})).toThrow();
 expect(buildInstalledAdapterEnvironment({definition,ambient:{OPENAI_API_KEY:"fixture",ANTHROPIC_API_KEY:"other"},credentialGroup:"openai-api-key"})).toEqual({OPENAI_API_KEY:"fixture"});
});
