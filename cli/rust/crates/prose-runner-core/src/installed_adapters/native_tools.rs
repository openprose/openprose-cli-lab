use super::*;
use std::collections::BTreeMap;

pub(super) fn rich(record: &Value) -> bool {
    record
        .pointer("/message/content")
        .and_then(Value::as_array)
        .is_some_and(|items| {
            items
                .iter()
                .any(|b| b["type"] == "toolCall" || b.get("index").is_some())
        })
        || record
            .pointer("/assistantMessageEvent/type")
            .and_then(Value::as_str)
            .is_some_and(|s| s.starts_with("toolcall_"))
        || record_type(record).is_some_and(|s| s.starts_with("tool_execution_"))
        || record
            .pointer("/data/dumpTools")
            .and_then(Value::as_array)
            .is_some_and(|t| !t.is_empty())
}
fn same_message(a: &Value, b: &Value, omp: bool) -> bool {
    if !omp {
        return a == b;
    }
    let mut a = a.clone();
    let mut b = b.clone();
    if let Some(o) = a.as_object_mut() {
        o.remove("completedAt");
    }
    if let Some(o) = b.as_object_mut() {
        o.remove("completedAt");
    }
    if a["role"].as_str()==Some("toolResult") && a["prunedAt"].as_f64().is_some_and(|n|n.is_finite()&&n>=0.0) && ["[Superseded by a newer read of this file]","[Uneventful result elided]"].iter().any(|text|a["content"]==serde_json::json!([{"type":"text","text":text}])) {
      a.as_object_mut().unwrap().remove("prunedAt");a["content"]=b["content"].clone();
    }
    a == b
}

/// Validates only native transport state. Prefix mode projects already-complete
/// messages without manufacturing terminal records or claiming settlement.
pub(super) fn normalize(
    records: &[Value],
    id: &str,
    omp: bool,
    terminal: bool,
) -> Result<TransportNormalization, RunnerError> {
    let bad = || RunnerError::catalog(ErrorCode::ProtocolMalformed);
    let fail = || RunnerError::catalog(ErrorCode::HarnessFailed);
    let mut ready = !omp;
    let mut commands = !omp;
    let mut inventory = !omp;
    let mut ack = false;
    let mut started = false;
    let mut turn = false;
    let mut user = false;
    let mut ended = false;
    let mut open: Option<Value> = None;
    let mut assistant: Option<Value> = None;
    let mut last_stop: Option<String> = None;
    let mut history = Vec::<Value>::new();
    let mut results = Vec::<Value>::new();
    let mut texts = Vec::<String>::new();
    let mut block_types = BTreeMap::<usize, String>::new();
    let mut calls = BTreeMap::<String, (String, Value, u8, Option<Value>, Option<bool>)>::new();
    for (record_index,r) in records.iter().enumerate() {
        let phase=if ended {"complete"} else if !started {"tool-await-agent-start"} else if open.is_some() {"tool-message-open"} else if turn {"tool-turn-open"} else if last_stop.as_deref()==Some("toolUse") {"tool-await-next-turn"} else {"tool-await-agent-end"};
        let bad=|| {
            let error=RunnerError::catalog(ErrorCode::ProtocolMalformed);
            if omp {error} else {error.with_detail("adapterDiagnostic",json!({"schema":"openprose.adapter-diagnostic/1","adapterId":"prime/rpc","stage":"prime-lifecycle","phase":phase,"counters":{"acceptedRecords":record_index.min(u32::MAX as usize),"thinkingDeltas":records[..record_index].iter().filter(|v|v.pointer("/assistantMessageEvent/type").and_then(Value::as_str)==Some("thinking_delta")).count().min(u32::MAX as usize),"textDeltas":records[..record_index].iter().filter(|v|v.pointer("/assistantMessageEvent/type").and_then(Value::as_str)==Some("text_delta")).count().min(u32::MAX as usize),"saturated":record_index>u32::MAX as usize}}))}
        };
        if !r.is_object() || !prime_bounded_json(r, 0) {
            return Err(bad());
        }
        let kind = record_type(r).ok_or_else(bad)?;
        if omp && kind == "extension_ui_request" {
            match omp_extension_ui_disposition(r) {
                OmpExtensionUiDisposition::Presentation => continue,
                OmpExtensionUiDisposition::Blocked => return Err(fail()),
                _ => return Err(bad()),
            }
        }
        if !ready {
            if !valid_omp_ready(r) {
                return Err(bad());
            }
            ready = true;
            continue;
        }
        if kind == "available_commands_update" && omp {
            if commands
                || started
                || !has_exact_keys(r, &["type", "commands"])
                || !r["commands"].is_array()
            {
                return Err(bad());
            }
            commands = true;
            continue;
        }
        if !commands {
            return Err(bad());
        }
        if kind == "response" {
            if omp && !inventory {
                if r["id"] != omp_rpc_id(id, "state.1")
                    || r["command"] != "get_state"
                    || !has_exact_keys(r, &["id", "type", "command", "success", "data"])
                {
                    return Err(bad());
                }
                if r["success"] != true {
                    return Err(fail());
                }
                let tools = r
                    .pointer("/data/dumpTools")
                    .and_then(Value::as_array)
                    .ok_or_else(bad)?;
                if tools
                    .iter()
                    .any(|t| t["name"].as_str().is_none_or(str::is_empty))
                {
                    return Err(bad());
                }
                inventory = true;
                continue;
            }
            let expected = if omp {
                omp_rpc_id(id, "prompt.1")
            } else {
                id.to_owned()
            };
            if ack
                || r["id"] != expected
                || r["command"] != "prompt"
                || !has_exact_keys(r, &["id", "type", "command", "success"])
            {
                return Err(bad());
            }
            if r["success"] != true {
                return Err(fail());
            }
            ack = true;
            continue;
        }
        if !inventory || (!omp && !ack) || ended {
            return Err(bad());
        }
        match kind {
            "agent_start" => {
                if started {
                    return Err(bad());
                }
                started = true;
            }
            "turn_start" => {
                if !started
                    || turn
                    || open.is_some()
                    || last_stop.as_ref().is_some_and(|s| s != "toolUse")
                {
                    return Err(bad());
                }
                turn = true;
                assistant = None;
                calls.clear();
                results.clear();
            }
            "message_start" => {
                let m = r.get("message").filter(|m| m.is_object()).ok_or_else(bad)?;
                // Observed Prime boundary omission: preserve messages and require all prior tools settled.
                if !omp && started && !turn && open.is_none() && last_stop.as_deref()==Some("toolUse")
                    && m["role"]=="assistant" && m["content"].as_array().is_some_and(Vec::is_empty)
                    && !calls.is_empty() && calls.values().all(|c|c.2==3) {
                    turn=true;assistant=None;calls.clear();results.clear();
                }
                if !turn || open.is_some() {
                    return Err(bad());
                }
                match m["role"].as_str() {
                    Some("user") if !user && assistant.is_none() => {}
                    Some("assistant") if user && assistant.is_none() => {
                        block_types.clear();
                    }
                    Some("toolResult") => {
                        let call = calls
                            .get(m["toolCallId"].as_str().ok_or_else(bad)?)
                            .ok_or_else(bad)?;
                        if call.2 != 2 || m["toolName"] != call.0 {
                            return Err(bad());
                        }
                    }
                    _ => return Err(bad()),
                }
                open = Some(m.clone());
            }
            "message_update" => {
                if open.as_ref().is_none_or(|m| m["role"] != "assistant")
                    || r.pointer("/message/role") != Some(&json!("assistant"))
                {
                    return Err(bad());
                }
                let e = &r["assistantMessageEvent"];
                let t = e["type"].as_str().ok_or_else(bad)?;
                if ![
                    "text_start",
                    "text_delta",
                    "text_end",
                    "thinking_start",
                    "thinking_delta",
                    "thinking_end",
                    "toolcall_start",
                    "toolcall_delta",
                    "toolcall_end",
                ]
                .contains(&t)
                    || e["contentIndex"].as_u64().is_none()
                    || !r["message"]["content"].is_array()
                    || (t.ends_with("_delta") && !e["delta"].is_string())
                {
                    return Err(bad());
                }
                let index = e["contentIndex"].as_u64().ok_or_else(bad)? as usize;
                let expected = if t.starts_with("toolcall_") {
                    "toolCall"
                } else {
                    t.split('_').next().ok_or_else(bad)?
                };
                if r["message"]["content"]
                    .get(index)
                    .and_then(|b| b["type"].as_str())
                    != Some(expected)
                    || block_types.get(&index).is_some_and(|t| t != expected)
                {
                    return Err(bad());
                }
                block_types.insert(index, expected.into());
            }
            "message_end" => {
                let m = r.get("message").ok_or_else(bad)?;
                let current = open.as_ref().ok_or_else(bad)?;
                if m["role"] != current["role"] || !m["content"].is_array() {
                    return Err(bad());
                }
                match m["role"].as_str() {
                    Some("user") => {
                        if m != current {
                            return Err(bad());
                        }
                        user = true;
                    }
                    Some("assistant") => {
                        if block_types.iter().any(|(i, t)| {
                            m["content"].get(*i).and_then(|b| b["type"].as_str())
                                != Some(t.as_str())
                        }) {
                            return Err(bad());
                        }
                        let stop = m["stopReason"].as_str().ok_or_else(bad)?;
                        if !["stop", "toolUse"].contains(&stop) {
                            return Err(fail());
                        }
                        let mut text = String::new();
                        for b in m["content"].as_array().ok_or_else(bad)? {
                            match b["type"].as_str() {
                                Some("text") => text.push_str(b["text"].as_str().ok_or_else(bad)?),
                                Some("thinking") => {
                                    if !b["thinking"].is_string() {
                                        return Err(bad());
                                    }
                                }
                                Some("toolCall") => {
                                    let key = b["id"]
                                        .as_str()
                                        .filter(|s| !s.is_empty())
                                        .ok_or_else(bad)?;
                                    let name = b["name"]
                                        .as_str()
                                        .filter(|s| !s.is_empty())
                                        .ok_or_else(bad)?;
                                    let mut args =
                                        b["arguments"].as_object().ok_or_else(bad)?.clone();
                                    if omp
                                        && b["intent"].is_string()
                                        && args.get("i") == b.get("intent")
                                    {
                                        args.remove("i");
                                    }
                                    if calls
                                        .insert(
                                            key.into(),
                                            (name.into(), Value::Object(args), 0, None, None),
                                        )
                                        .is_some()
                                    {
                                        return Err(bad());
                                    }
                                }
                                _ => return Err(bad()),
                            }
                        }
                        if (stop == "toolUse") != (!calls.is_empty()) {
                            return Err(bad());
                        }
                        texts.push(text);
                        assistant = Some(m.clone());
                    }
                    Some("toolResult") => {
                        let call = calls
                            .get_mut(m["toolCallId"].as_str().ok_or_else(bad)?)
                            .ok_or_else(bad)?;
                        if m != current
                            || call.2 != 2
                            || m["toolName"] != call.0
                            || !m["isError"].is_boolean()
                        {
                            return Err(bad());
                        }
                        if call.3.as_ref() != m.get("content") || call.4 != m["isError"].as_bool() {
                            return Err(bad());
                        }
                        call.2 = 3;
                        results.push(m.clone());
                    }
                    _ => return Err(bad()),
                }
                history.push(m.clone());
                open = None;
            }
            "tool_execution_start" | "tool_execution_update" | "tool_execution_end" => {
                if !turn || open.is_some() {
                    return Err(bad());
                }
                let call = calls
                    .get_mut(r["toolCallId"].as_str().ok_or_else(bad)?)
                    .ok_or_else(bad)?;
                if r["toolName"] != call.0 {
                    return Err(bad());
                }
                if kind == "tool_execution_start" {
                    if call.2 != 0 || !native_args_match(&r["args"], &call.1, omp) {
                        return Err(bad());
                    }
                    call.2 = 1;
                } else if kind == "tool_execution_update" {
                    if call.2 != 1 || !native_args_match(&r["args"], &call.1, omp) || !r["partialResult"].is_object() {
                        return Err(bad());
                    }
                } else {
                    if call.2 != 1 || !r["isError"].is_boolean() || !r["result"].is_object() {
                        return Err(bad());
                    }
                    call.3 = r["result"].get("content").cloned();
                    call.4 = r["isError"].as_bool();
                    call.2 = 2;
                }
            }
            "turn_end" => {
                if !turn
                    || open.is_some()
                    || assistant
                        .as_ref()
                        .is_none_or(|m| !same_message(&r["message"], m, omp))
                    || r["toolResults"] != json!(results)
                    || calls.values().any(|c| c.2 != 3)
                {
                    return Err(bad());
                }
                last_stop = assistant
                    .as_ref()
                    .and_then(|m| m["stopReason"].as_str().map(str::to_owned));
                turn = false;
            }
            "agent_end" => {
                let msgs = r["messages"].as_array().ok_or_else(bad)?;
                if !started
                    || turn
                    || open.is_some()
                    || last_stop.as_deref() != Some("stop")
                    || msgs.len() != history.len()
                    || msgs
                        .iter()
                        .zip(&history)
                        .any(|(a, b)| !same_message(a, b, omp))
                {
                    return Err(bad());
                }
                if omp && r["isTerminal"] != true {
                    return Err(fail());
                }
                ended = true;
            }
            _ => return Err(bad()),
        }
    }
    if terminal && (!ended || !ack) {
        return Err(bad());
    }
    Ok(TransportNormalization {
        terminal_event: "agent_end",
        assistant_messages: texts,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn frames(omp: bool) -> Vec<Value> {
        serde_json::from_str(if omp {
            include_str!("../../../../../shared/fixtures/adapters/tool-lifecycle/omp.json")
        } else {
            include_str!("../../../../../shared/fixtures/adapters/tool-lifecycle/prime.json")
        })
        .unwrap()
    }
    #[test]
    fn actual_tool_streams_and_corruptions() {
        for omp in [false, true] {
            let frames = frames(omp);
            assert!(normalize(&frames, "fixture-tools", omp, true).is_ok());
            let end = frames
                .iter()
                .position(|r| r["type"] == "agent_end")
                .unwrap();
            assert!(normalize(&frames[..end], "fixture-tools", omp, true).is_err());
            assert!(normalize(&frames[..end], "fixture-tools", omp, false).is_ok());
            let mut bad = frames.clone();
            bad.iter_mut()
                .find(|r| r["type"] == "tool_execution_end")
                .unwrap()["toolCallId"] = json!("wrong");
            assert!(normalize(&bad, "fixture-tools", omp, true).is_err());
        }
    }
}

// OMP may omit schema-optional null fields before native execution.
fn native_args_match(actual:&Value,declared:&Value,omp:bool)->bool {
 if !omp{return actual==declared;}
 match (actual,declared){
 (Value::Object(a),Value::Object(d))=>a.iter().all(|(k,v)|d.get(k).is_some_and(|other|native_args_match(v,other,true)))&&d.iter().all(|(k,v)|a.contains_key(k)||v.is_null()||v.as_str()==Some("null")),
 (Value::Array(a),Value::Array(d))=>a.len()==d.len()&&a.iter().zip(d).all(|(v,o)|native_args_match(v,o,true)),
 _=>actual==declared
 }
}
#[test]
fn omp_optional_null_omission_does_not_permit_changed_values(){
 let declared=serde_json::json!({"op":"init","optional":null});
 assert!(native_args_match(&serde_json::json!({"op":"init"}),&declared,true));
 assert!(!native_args_match(&serde_json::json!({"op":"erase"}),&declared,true));
 assert!(!native_args_match(&serde_json::json!({"op":"init"}),&declared,false));
}

#[test]
fn omp_superseded_terminal_projection_preserves_tool_identity(){
 let original=serde_json::json!({"role":"toolResult","toolCallId":"t1","toolName":"read","isError":true,"content":[{"type":"text","text":"not found"}]});
 let mut summary=original.clone();summary["prunedAt"]=serde_json::json!(12);summary["content"]=serde_json::json!([{"type":"text","text":"[Superseded by a newer read of this file]"}]);
 assert!(same_message(&summary,&original,true));assert!(!same_message(&summary,&original,false));
 summary["toolCallId"]=serde_json::json!("invented");assert!(!same_message(&summary,&original,true));
}

#[test]
fn prime_implicit_boundary_requires_settled_tools_and_native_terminal(){
 let frames:Vec<Value>=serde_json::from_str(include_str!("../../../../../shared/fixtures/adapters/tool-lifecycle/prime-implicit-turn.json")).unwrap();
 assert!(normalize(&frames,"fixture-tools",false,true).is_ok());
 assert!(normalize(&frames[..frames.len()-1],"fixture-tools",false,false).is_ok());
 assert!(normalize(&frames[..frames.len()-1],"fixture-tools",false,true).is_err());
 let boundary=(1..frames.len()).find(|&i|frames[i]["type"]=="message_start"&&frames[i-1]["type"]=="turn_end").unwrap();
 for message in [json!({"role":"user","content":[]}),json!({"role":"assistant","content":[{"type":"text","text":"unexpected"}]})] {
  let mut bad=frames.clone();bad[boundary]["message"]=message;
  let error=normalize(&bad,"fixture-tools",false,true).unwrap_err();assert_eq!(serde_json::to_value(error).unwrap()["details"]["adapterDiagnostic"]["phase"],"tool-await-next-turn");
 }
 let mut pending=frames.clone();pending.remove(boundary-1);assert!(normalize(&pending,"fixture-tools",false,true).is_err());
 let mut omp:Vec<Value>=serde_json::from_str(include_str!("../../../../../shared/fixtures/adapters/tool-lifecycle/omp.json")).unwrap();
 let boundary=omp.iter().enumerate().filter(|(_,r)|r["type"]=="turn_start").nth(1).unwrap().0;omp.remove(boundary);assert!(normalize(&omp,"fixture-tools",true,true).is_err());
}

#[test]
#[ignore = "Explicit provider-free replay path is supplied by developer"]
fn prime_recorded_prefix_replay(){
 let text=std::fs::read_to_string(std::env::var("PRIME_REPLAY_PATH").expect("replay path")).unwrap();
 let mut frames:Vec<Value>=text.lines().map(|l|serde_json::from_str(l).unwrap()).collect();let id=frames[0]["id"].as_str().unwrap().to_owned();
 assert!(normalize(&frames,&id,false,false).is_ok());assert!(normalize(&frames,&id,false,true).is_err());
 let mut message=frames.last().unwrap()["message"].clone();message["content"]=json!([{"type":"text","text":"synthetic continuation"}]);message["stopReason"]=json!("stop");
 let mut history:Vec<Value>=frames.iter().filter(|r|r["type"]=="message_end").map(|r|r["message"].clone()).collect();history.push(message.clone());
 frames.push(json!({"type":"message_end","message":message}));frames.push(json!({"type":"turn_end","message":message,"toolResults":[]}));
 assert!(normalize(&frames,&id,false,true).is_err());frames.push(json!({"type":"agent_end","messages":history}));assert!(normalize(&frames,&id,false,true).is_ok());
}
