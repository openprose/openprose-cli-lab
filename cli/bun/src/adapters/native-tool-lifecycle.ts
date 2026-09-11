import { isDeepStrictEqual } from "node:util";
import { failure } from "../core/errors";
import type { RawTransportEvent } from "../supervision/types";

const bad = (): never => { throw failure("PROTOCOL_MALFORMED", { reason: "Native tool lifecycle is malformed or out of order." }); };
const object = (v: unknown): Record<string, any> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, any> : bad();
const same = (a: unknown, b: unknown): boolean => isDeepStrictEqual(a,b);
// OMP's schema validation may omit optional null fields before tool execution.
// This is transport compatibility, not a claim of schema equivalence.
export function nativeArgsMatch(actual: unknown, declared: unknown, omp:boolean):boolean {
 if(!omp)return same(actual,declared);
 if(Array.isArray(actual)&&Array.isArray(declared))return actual.length===declared.length&&actual.every((v,i)=>nativeArgsMatch(v,declared[i],true));
 if(actual&&declared&&typeof actual==="object"&&typeof declared==="object"&&!Array.isArray(actual)&&!Array.isArray(declared)){
   const a=actual as Record<string,unknown>,d=declared as Record<string,unknown>;
   return Object.keys(a).every(k=>Object.hasOwn(d,k)&&nativeArgsMatch(a[k],d[k],true))&&Object.keys(d).every(k=>Object.hasOwn(a,k)||d[k]===null||d[k]==="null");
 }
 return same(actual,declared);
}

/** Native transport bookkeeping only. No language meaning or synthetic settlement. */
export class NativeToolLifecycle {
  started = false;
  ended = false;
  private turn = false;
  private user = false;
  private open: Record<string, any> | null = null;
  private assistant: Record<string, any> | null = null;
  private history: Record<string, any>[] = [];
  private calls = new Map<string, { name: string; args: unknown; state: string; result?: any; isError?: boolean }>();
  private results: Record<string, any>[] = [];
  private blockTypes = new Map<number,string>();
  private lastStop: string | null = null;
  constructor(private readonly omp: boolean) {}

  get phase(): "tool-await-agent-start" | "tool-await-next-turn" | "tool-message-open" | "tool-turn-open" | "tool-await-agent-end" | "complete" {
    return this.ended ? "complete" : !this.started ? "tool-await-agent-start" : this.open ? "tool-message-open" : this.turn ? "tool-turn-open" : this.lastStop === "toolUse" ? "tool-await-next-turn" : "tool-await-agent-end";
  }

  private sameMessage(a: any,b: any): boolean {
    if (!this.omp) return same(a,b);
    const x={...a},y={...b};delete x.completedAt;delete y.completedAt;
    if(x.role==="toolResult" && typeof x.prunedAt==="number" && Number.isFinite(x.prunedAt) && x.prunedAt>=0 && ["[Superseded by a newer read of this file]","[Uneventful result elided]"].some(text=>same(x.content,[{type:"text",text}]))) {delete x.prunedAt;x.content=y.content;}
    return same(x,y);
  }
  accept(value: unknown): RawTransportEvent | null {
    const r = object(value);
    if (this.ended) bad();
    switch (r.type) {
      case "agent_start":
        if (this.started) bad();
        this.started = true;
        return { type: "session.started" };
      case "turn_start":
        if (!this.started || this.turn || this.open || (this.lastStop !== null && this.lastStop !== "toolUse")) bad();
        this.turn = true; this.assistant = null; this.calls.clear(); this.results = [];
        return null;
      case "message_start": {
        const m = object(r.message);
        // Prime 0.7 has emitted this boundary without its turn_start marker.
        // Only a fully settled tool turn permits this empty assistant start.
        if (!this.omp && this.started && !this.turn && !this.open && this.lastStop === "toolUse"
          && m.role === "assistant" && Array.isArray(m.content) && m.content.length === 0
          && this.calls.size > 0 && [...this.calls.values()].every(c=>c.state==="reported")) {
          this.turn=true; this.assistant=null; this.calls.clear(); this.results=[];
        }
        if (!this.turn || this.open) bad();
        if (m.role === "user") { if (this.user || this.assistant) bad(); }
        else if (m.role === "assistant") { if (!this.user || this.assistant) bad(); this.blockTypes.clear(); }
        else if (m.role === "toolResult") {
          const c = this.calls.get(m.toolCallId) ?? bad();
          if (!c || c.state !== "ended" || c.name !== m.toolName) bad();
        } else bad();
        this.open = m; return null;
      }
      case "message_update": {
        const m = object(r.message), e = object(r.assistantMessageEvent);
        if (!this.open || this.open.role !== "assistant" || m.role !== "assistant") bad();
        if (!["text_start","text_delta","text_end","thinking_start","thinking_delta","thinking_end","toolcall_start","toolcall_delta","toolcall_end"].includes(e.type)) bad();
        if (!Number.isInteger(e.contentIndex) || e.contentIndex < 0 || !Array.isArray(m.content)) bad();
        const block = m.content[e.contentIndex];
        const expected = e.type.startsWith("toolcall_") ? "toolCall" : e.type.split("_")[0];
        if (!block || block.type !== expected || (this.blockTypes.has(e.contentIndex) && this.blockTypes.get(e.contentIndex)!==expected)) bad();
        this.blockTypes.set(e.contentIndex,expected);
        if (e.type.endsWith("_delta") && typeof e.delta !== "string") bad();
        return null;
      }
      case "message_end": {
        const m = object(r.message);
        if (!this.open || m.role !== this.open.role || !Array.isArray(m.content)) bad();
        if (m.role === "user") { if (!same(m,this.open)) bad(); this.user = true; }
        else if (m.role === "assistant") {
          if ([...this.blockTypes].some(([i,t])=>m.content[i]?.type!==t)) bad();
          if (!["stop","toolUse"].includes(m.stopReason)) throw failure("HARNESS_FAILED", { reason: "Native assistant did not finish normally." });
          for (const b0 of m.content) {
            const b = object(b0);
            if (b.type === "toolCall") {
              if (typeof b.id !== "string" || !b.id || typeof b.name !== "string" || !b.name || this.calls.has(b.id)) bad();
              object(b.arguments);
              const args = {...b.arguments};
              if (this.omp && typeof b.intent === "string" && args.i === b.intent) delete args.i;
              this.calls.set(b.id,{name:b.name,args,state:"declared"});
            } else if (b.type === "text") { if (typeof b.text !== "string") bad(); }
            else if (b.type === "thinking") { if (typeof b.thinking !== "string") bad(); }
            else bad();
          }
          if ((m.stopReason === "toolUse") !== (this.calls.size > 0)) bad();
          this.assistant = m;
        } else {
          const c = this.calls.get(m.toolCallId) ?? bad();
          if (!same(m,this.open) || !c || c.state !== "ended" || c.name !== m.toolName || typeof m.isError !== "boolean") bad();
          if (!same(m.content,c.result?.content) || m.isError!==c.isError) bad();
          c.state = "reported"; this.results.push(m);
        }
        this.history.push(m); this.open = null;
        return m.role === "assistant" ? {type:"assistant.message",text:m.content.filter((b:any)=>b.type==="text").map((b:any)=>b.text).join("")} : null;
      }
      case "tool_execution_start": case "tool_execution_update": case "tool_execution_end": {
        const c = this.calls.get(r.toolCallId) ?? bad();
        if (!this.turn || this.open || !c || r.toolName !== c.name) bad();
        if (r.type === "tool_execution_start") {
          if (c.state !== "declared" || !nativeArgsMatch(r.args,c.args,this.omp)) bad(); c.state = "started";
        } else if (r.type === "tool_execution_update") {
          if (c.state !== "started" || !nativeArgsMatch(r.args,c.args,this.omp)) bad(); object(r.partialResult);
        } else {
          if (c.state !== "started" || typeof r.isError !== "boolean") bad(); object(r.result); c.result=r.result; c.isError=r.isError; c.state = "ended";
        }
        return null;
      }
      case "turn_end":
        if (!this.turn || this.open || !this.assistant || !this.sameMessage(r.message,this.assistant) || !same(r.toolResults,this.results) || [...this.calls.values()].some(c=>c.state!=="reported")) bad();
        this.lastStop = this.assistant!.stopReason; this.turn = false; return null;
      case "agent_end":
        if (!this.started || this.turn || this.open || this.lastStop !== "stop" || (!Array.isArray(r.messages) || r.messages.length!==this.history.length || r.messages.some((m:any,i:number)=>!this.sameMessage(m,this.history[i])))) bad();
        if (this.omp && r.isTerminal !== true) throw failure("HARNESS_FAILED", {reason:"unsupported_nonterminal_settlement"});
        this.ended = true; return {type:"session.completed"};
      default: return bad();
    }
  }
}

export function hasNativeTools(value: unknown): boolean {
  const r = value as any;
  return Array.isArray(r?.message?.content) && r.message.content.some((b:any)=>b?.type==="toolCall" || Number.isInteger(b?.index))
    || typeof r?.assistantMessageEvent?.type === "string" && r.assistantMessageEvent.type.startsWith("toolcall_")
    || typeof r?.type === "string" && r.type.startsWith("tool_execution_");
}
