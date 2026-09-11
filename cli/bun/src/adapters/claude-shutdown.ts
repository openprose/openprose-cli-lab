/** Classifies native routing/cleanup evidence only; never examines task text. */
export function hasFreshClaudeResult(records: readonly Record<string, any>[]): boolean {
  let candidateIndex=-1;for(let i=records.length-1;i>=0;i--){if(records[i]?.type==="result"){candidateIndex=i;break;}}
  if(candidateIndex<0)return false;
  const candidate=records[candidateIndex]!,session=records[0]?.session_id;
  if(candidate.session_id!==session||candidate.subtype!=="success"||candidate.is_error!==false)return false;
  if(candidateIndex===records.length-1)return true;
  const bashCalls=new Set<string>(),known=new Map<string,string>();
  for(const record of records.slice(0,candidateIndex)){
    for(const block of record.message?.content??[])if(block?.type==="tool_use"&&block.name==="Bash"&&typeof block.id==="string")bashCalls.add(block.id);
    if(record.type!=="system"||record.session_id!==session)continue;
    if(record.subtype==="task_started"&&record.task_type==="local_bash"&&record.is_backgrounded===true&&bashCalls.has(record.tool_use_id))known.set(record.task_id,record.tool_use_id);
    if(record.subtype==="task_notification"&&["completed","failed","stopped"].includes(record.status))known.delete(record.task_id);
  }
  const pending=new Set<string>(),closed=new Set<string>();let inventory=false;
  for(const record of records.slice(candidateIndex+1)){
    if(record.type!=="system"||record.session_id!==session||typeof record.uuid!=="string"||!record.uuid)return false;
    if(record.subtype==="background_tasks_changed"){
      if(!Array.isArray(record.tasks)||record.tasks.length!==0)return false;inventory=true;
    }else if(record.subtype==="task_updated"){
      const patch=record.patch;
      if(!inventory||!known.has(record.task_id)||pending.has(record.task_id)||closed.has(record.task_id)||!patch||typeof patch!=="object"||Array.isArray(patch)||Object.keys(patch).sort().join(",")!=="end_time,status"||patch.status!=="killed"||!Number.isSafeInteger(patch.end_time)||patch.end_time<0)return false;
      pending.add(record.task_id);
    }else if(record.subtype==="task_notification"){
      if(!pending.has(record.task_id)||record.status!=="stopped"||record.tool_use_id!==known.get(record.task_id))return false;
      pending.delete(record.task_id);closed.add(record.task_id);
    }else return false;
  }
  return pending.size===0&&closed.size>0;
}
