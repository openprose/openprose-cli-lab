# Prime native operation settlement

In native-output mode, the runner asks the attached Prime RPC session for its identity before sending the task. A failed, missing or nonfresh identity response does not dispatch the task. The task is sent once; the runner closes stdin after acknowledgment and observes the native EOF/idle drain. Legacy envelope mode is unchanged.

Prime may report an agent_end for a completed model loop before queued messages finish. A toolUse segment ending is not completion. Native success requires an actual final stop/end, fully reported tools, matching segment history, drained observed queue, and process exit zero. New work or an active action after a final candidate conservatively fails; later queue emptiness alone cannot prove missing work completed.

A narrowly recognized snapshot-only custom agent-message prompt may prefix a continuation segment. Its target session ID must match the independent state response, its sender must match a previously observed child, and its body must exactly match a previously observed queued delivery. Full source-defined formatting and streamed suffix equality are checked. The custom prompt is retained as snapshot evidence, never fabricated as a streamed event. Target active-session ID and sender persisted-session ID remain snapshot-only metadata, not independently verified identities. The native producer remains trusted; these checks are correlation, not cryptographic authentication. A message may be a producer fallback on a child's behalf.

Prime aggregates child accounting into earlier parent assistant usage. Native segment comparison therefore permits numeric differences only in complete, typed, source-shaped usage/cost objects; all other message fields remain exact. Both versions remain in captured records. Accounting is not execution evidence.

Compatibility is intentionally narrow. Missing turn markers still require the established fully settled tool-turn case; arbitrary custom context, altered tool history, unknown message shapes and incomplete queues fail closed. Transport completion never establishes program fulfillment.

Source basis: Prime 0.7 installed rpc-mode get_state and EOF waitForIdle; daemon connection state and message endpoints; core agent-messages formatter; core session queue preview; child usage attribution in agent-session/session-manager. Provider-free fixtures are synthetic. A historical direct stream had no identity prelude, so replay with an added prelude does not retroactively establish its observed parent identity.
