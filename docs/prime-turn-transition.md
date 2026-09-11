# Native Prime missing turn markers

Native-output mode permits one narrowly inferred parser transition when Prime omits both turn_end and turn_start before an actual empty assistant message. The previous actual assistant must have stopped for tool use, declared at least one call, and every call must have a validated execution result and matching reported toolResult. There must be no open message or terminal candidate.

The parser closes the already observed tool turn internally and opens the next one. It emits no replacement events, preserves the complete message history, and makes no claim that hidden post-turn hooks ran. Tool errors remain ordinary reported results; they are not interpreted as program success. Unknown, pending, duplicate or mismatched results cannot trigger this transition.

The source-defined turn_end repeats the current assistant and collected tool results; turn_start precedes the next assistant. This compatibility rule tolerates loss of those redundant boundary observations only after the rest of that lifecycle is fully observed. The cause of historical record loss is not established. Legacy envelope mode and OMP remain unchanged.

An accepted prefix is still incomplete. Native completion requires the existing genuine final stop/turn_end/agent_end, matching history, fresh required queue observation and process-zero gates. No result file or child status substitutes for settlement. Tests label fabricated continuations as synthetic and retain original failed captures unchanged.
