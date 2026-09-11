# Prime child-status compatibility

Prime 0.7.0 emits `rlm_child_update` during ordinary recursive delegation. The adapter admits the documented bounded child snapshot during an active tool-capable Prime parent. Child status is observation only: queued, running, done, error or cancelled does not settle parent tools, establish an accepted result, or substitute for the actual parent terminal. Records before parent start or after terminal remain invalid. OMP and legacy text-only admission are unchanged.

The typed record has `type` and `child`. The child requires string id, label and sessionDir (id/path nonempty), and the closed status above. Optional identity/preview/error strings, nonnegative finite duration, safe nonnegative integer counts, boolean repliedSinceTask and a typed waiting/writing/executing activity follow the installed producer declaration. Unknown fields remain rejected pending evidence; no open-ended telemetry whitelist is added.

Evidence is the installed Prime AgentSession RlmChildAgentSnapshot declaration and emitter, plus a real recorded queued child update rejected by the previous adapter. The original parent failure is retained; a later child session records an aborted attempt. Neither that trace nor this compatibility repair demonstrates successful independent review or verified private-daemon cleanup.

The related `ipython_sent_agent_message` event is not admitted by this change. Its declared payload is toolCallId and message {id, message, deliveryStatus: delivered|queued, receiverRole?: parent|sibling|child, target: {activeSessionId, sessionId, sessionName?}}. The installed late-delivery emitter also updates tool-result history; a future observed case needs correlated history tests, not merely an ignored-event exception.

## Whole-runner qualification correction

The first implementation changed the detailed native parser but omitted Rust's outer event-name filter. A fresh real run therefore rejected the same queued event before typed admission. That failure remains part of the evidence; parser-only replay was insufficient. The correction adds this exact event to the Prime outer filter and tests the compiled commands with the recorded prefix, an explicitly synthetic valid continuation, and invalid ordering/unknown events. An incomplete prefix remains a failure, never historical success.
