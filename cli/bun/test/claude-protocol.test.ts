import { expect, test } from "bun:test";
import { installedProtocol } from "../src/adapters/protocols";
import telemetry from "../../shared/fixtures/adapters/claude-thinking-tokens.json";
function ready() {
  const p = installedProtocol("claude/print-stream-json", "2.1.243", "fixture");
  p.accept({type: "system", subtype: "init", session_id: "fixture-session"});
  return p;
}
test("Claude telemetry is information, not completion", () => {
  const p = ready();
  expect(p.accept(telemetry)).toBeNull();
  expect(p.accept({type:"result", subtype:"success", is_error:false, session_id:"fixture-session"})).not.toBeNull();
});
test("Claude telemetry requires the active session and bounded valid fields", () => {
  for (const mutation of [{session_id:"other"}, {estimated_tokens:-1}, {estimated_tokens_delta:0.5}, {uuid:""}, {unexpected:true}]) {
    expect(() => ready().accept({...telemetry,...mutation})).toThrow();
  }
  const fresh = installedProtocol("claude/print-stream-json", "2.1.243", "fixture");
  expect(() => fresh.accept(telemetry)).toThrow();
});
