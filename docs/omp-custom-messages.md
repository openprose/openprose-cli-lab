# OMP streamed custom messages

Decision029 is native transport compatibility only. OMP 18.0.9 can stream a custom input between turn_start and the next assistant response. The runner accepts a typed message_start/message_end pair, retains its exact position and value in terminal history, and emits no assistant result or completion for it. The customType and content are never interpreted as instructions by the adapter.

The installed producer defines CustomMessage in pi-coding-agent/src/session/messages.ts:961, CustomMessageContent at321, and TextContent/ImageContent/ProviderFileReference in pi-ai/src/types.ts. Required fields are role=custom, string customType, string or typed text/image content, boolean display and finite nonnegative timestamp. Optional attribution is user|agent; details are opaque JSON. Image metadata follows the declared fields, without fetching URLs or provider references. Unknown structural fields or content block types fail closed.

pi-coding-agent/src/session/todo-tracker.ts:290–320 emits mid-run-todo-nudge as one such message. pi-agent-core/src/agent-loop.ts:967–971 emits input start/end pairs, and1089–1094 appends pending inputs to history;1184–1185 emits them after turn_start. Neither that customType nor its text is special-cased.

Admission requires an active turn, no open message, and no current assistant response. Matching end and terminal history are exact, including details; the tool-result pruning compatibility does not weaken custom-message equality. Pending tool states, turn completion and actual agent terminal requirements remain unchanged. Prime and snapshot-only context insertion are outside this decision.

The original OMP review capture ends at custom message_start2045. An explicitly synthetic paired end and assistant/terminal suffix exercises the former rejection through both compiled products; it does not establish a historical terminal or successful live invocation. Full-command negative cases preserve incomplete, altered, unmatched and premature completion failures. Known conservative Rust cleanup-code differences are reported separately.

Implementation was authorized in isolated worktree omp-custom / branch codex/decision-029-omp-custom by the root owner, superseding the legacy shared-worktree prohibition on Git operations for this bounded candidate. Only native lifecycle helpers, shared synthetic fixture, focused tests and this documentation change. No live provider calls or language edits.
