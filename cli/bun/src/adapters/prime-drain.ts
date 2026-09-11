import {isDeepStrictEqual as same} from "node:util";
export function primeHistorySame(a:any,b:any):boolean {
  if(a?.role!=="assistant"||b?.role!=="assistant"||(!("usage" in a)&&!("usage" in b)))return same(a,b);
  const nums=(v:any,keys:string[])=>v&&typeof v==="object"&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>typeof v[k]==="number"&&Number.isFinite(v[k])&&v[k]>=0);
  const valid=(u:any)=>u&&typeof u==="object"&&!Array.isArray(u)&&Object.keys(u).length===6&&nums(Object.fromEntries(Object.entries(u).filter(([k])=>k!=="cost")),["input","output","cacheRead","cacheWrite","totalTokens"])&&nums(u.cost,["input","output","cacheRead","cacheWrite","total"]);
  if(!valid(a.usage)||!valid(b.usage))return false;
  const x={...a},y={...b};delete x.usage;delete y.usage;return same(x,y);
}

/** Native producer observations only; this is not authenticated agent identity. */
export class PrimeDrain {
  segmentClosed=false; resumed=false; candidate=false; queueEmpty=false; queueObserved=false;
  private children=new Map<string,string>(); private previews=new Set<string>(); private used=new Set<string>(); private usedPreviews=new Set<string>();
  constructor(readonly sessionId:string){}
  child(c:any){if(typeof c.activeSessionId==="string"&&typeof c.sessionName==="string")this.children.set(c.activeSessionId,c.sessionName);}
  queue(a:any):boolean {
    this.queueObserved=true;
    if(this.candidate&&a.active)return false;
    this.queueEmpty=a.queuedCount===0&&a.steering.length===0&&a.followUps.length===0&&!a.active;
    for(const s of [...a.steering,...a.followUps])if(!this.usedPreviews.has(s))this.previews.add(s);
    return true;
  }
  history(messages:any[],observed:any[]):boolean {
    const equal=(a:any[],b:any[])=>a.length===b.length&&a.every((m,i)=>primeHistorySame(m,b[i]));
    if(equal(messages,observed))return true;
    if(!this.resumed||messages.length!==observed.length+1||!equal(messages.slice(1),observed))return false;
    const m=messages[0],d=m?.details,f=d?.from,t=d?.target;
    const exact=(v:any,keys:string[])=>v&&typeof v==="object"&&!Array.isArray(v)&&Object.keys(v).every(k=>keys.includes(k));
    const strings=(v:any,keys:string[])=>keys.every(k=>typeof v?.[k]==="string"&&v[k].length>0);
    if(!exact(m,["role","customType","content","display","details","timestamp"])||m.role!=="custom"||m.customType!=="agent_message"||m.display!==true||!Number.isFinite(m.timestamp)||m.timestamp<0
      ||!exact(d,["id","message","from","fromRelationship","target"])||!strings(d,["id","message"])||d.fromRelationship!=="child"
      ||!exact(f,["activeSessionId","sessionId","sessionName","runtimeKind","clientId"])||!strings(f,["activeSessionId","sessionId","sessionName","clientId"])||f.runtimeKind!=="subagent"
      ||!exact(t,["activeSessionId","sessionId","sessionName","runtimeKind"])||!strings(t,["activeSessionId","sessionId"])||t.runtimeKind!=="top-level"||("sessionName" in t&&typeof t.sessionName!=="string")
      ||t.sessionId!==this.sessionId||this.children.get(f.activeSessionId)!==f.sessionName||this.used.has(d.id))return false;
    const preview=`Agent message received: ${d.message}`;
    if(!this.previews.has(preview))return false;
    const fmt=(s:string)=>s.replace(/[\s,[\]]+/g," ").trim();
    const sender=[fmt(f.sessionName),`active ${fmt(f.activeSessionId)}`,`session ${fmt(f.sessionId)}`,`client ${fmt(f.clientId)}`].filter(Boolean).join(", ");
    const endpoint=`${t.sessionName?fmt(t.sessionName)+", ":""}active ${fmt(t.activeSessionId)}, session ${fmt(t.sessionId)}`;
    const content=`[from child:${fmt(f.sessionName)}]\nAgent-to-agent message received.\nSource: agent_message\nFrom: ${sender}\nTo: ${endpoint}\nMessage id: ${d.id}\n\n${d.message}`;
    if(m.content!==content)return false;
    this.used.add(d.id);this.usedPreviews.add(preview);this.previews.delete(preview);return true;
  }
}
