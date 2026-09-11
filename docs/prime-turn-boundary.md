# Prime omitted turn marker compatibility

A recorded Prime0.7 stream ended a fully validated tool-use turn, then emitted an empty assistant message_start without the usual intervening turn_start. The adapter formerly rejected that record. This is native transport evidence, not evidence about the task's semantics.

Both adapters now allow that empty assistant start to open the next turn only in Prime tool mode, after a validated toolUse turn_end with every tool result reported and no open message. No message, tool result, output text, or completion event is fabricated. The actual native agent_end and complete history remain required. Initial turns, pending tools, wrong roles, nonempty content, ordinary no-tool parsing, and OMP do not gain this tolerance.

The installed Prime producer normally emits turn_start at each subsequent turn: dist/bundle/chunk-ALQBG3TN.js around14249–50. We have not established why the recorded RPC stream omitted it. This is an explicitly documented compatibility allowance for that observed sequence, not a claim that the native protocol guarantees such omissions. Raw evidence remains unchanged. The original failed task is not retroactively counted as successful.

Provider-free tests cover a generic shared fixture, negative sequences, and an opt-in exact-recorded-prefix replay through PRIME_REPLAY_PATH. The captured prefix is incomplete and must remain incomplete; a separate synthetic continuation tests acceptance only once an actual test terminal record is supplied. No new live call is necessary to validate this observed parsing defect.

Prime diagnostics now report the active native tool mode and its current state. They no longer show a stale phase from the earlier no-tool parser after switching modes.

## Known Prime 0.7.0 event-loss case

A separate observed native prefix omits both the current `turn_end` and the next `turn_start` before an empty assistant `message_start`. This exceeds the single missing-start compatibility rule above and remains rejected. No turn marker, tool result, or terminal completion is fabricated.

A provider-free fixture using extracted installed Prime methods demonstrates that an extension-event exception can be swallowed by its session queue: the affected event is not forwarded, while later events continue. This is a possible native loss mechanism, not proof of the historical failure's cause. The installed harness has not been patched and the adapter remains strict for this discontinuity.

Development evidence from the private expedition is retained separately in `lab/runs/strong-prime-diagnosis-v0` and `lab/runs/prime-queue-loss-mechanism-v0`, with source hashes and the controlled reproduction. These are diagnostic artifacts, not runner or language dependencies. No external issue report has been sent.
