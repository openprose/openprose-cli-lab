import {test,expect} from "bun:test";
import fixture from "../../shared/fixtures/adapters/native-output.v1.json";
import {nativeOutputText} from "../src/adapters/terminal";
import {installedProtocol} from "../src/adapters/protocols";
test("native mode preserves arbitrary final prose",()=>{expect(nativeOutputText(fixture.messages)).toBe(fixture.visibleText)});
test("text without native completion does not settle",()=>{
 const protocol=installedProtocol("codex/exec-json","0.149.0-alpha.4.1","fixture");
 protocol.accept({type:"thread.started",thread_id:"fixture"});
 protocol.accept({type:"turn.started"});
 protocol.accept({type:"item.completed",item:{type:"agent_message",text:fixture.visibleText}});
 expect(protocol.terminalEventObserved).toBe(false);
 protocol.accept({type:"turn.completed",usage:{input_tokens:1,output_tokens:1,cached_input_tokens:0}});
 expect(protocol.terminalEventObserved).toBe(true);
});
