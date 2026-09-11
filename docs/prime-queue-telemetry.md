# Prime session queue observations

Prime 0.7.0 emits `session_action_update` as native queue snapshots change. The adapter admits its documented closed shape during an active tool-capable Prime parent, before terminal. Rust's outer event filter and both typed parsers admit the same event. This is observation only: it never settles a parent tool, modifies message history, or replaces the actual parent terminal.

The event contains actions with a nonnegative safe integer queuedCount, string arrays steering and followUps, and optional active {kind: turn|session_command, phase: preparing|committing|running, label?: string}. Array length need not equal queuedCount. Text is opaque. Unknown fields/events remain rejected. OMP and legacy text-only handling are unchanged.

The installed SessionActionSnapshot declaration and AgentSession._emitQueueUpdate emitter establish this shape. A real completed child without an explicit reply triggered fallback steering injection and the observed queue update. That original parent failed despite a completed reviewer; it remains retained. Provider-free whole-command tests replay that exact prefix with an explicitly synthetic terminal continuation and malformed/early/late/missing-terminal variants. Synthetic completion is not evidence the historical parent completed.

Other events, including ipython_sent_agent_message and its potential tool-history mutation, are outside this change. A separate Rust cleanup diagnostic can mask a more specific incomplete/invalid-stream error; the tested negative cases still fail rather than invent completion.
