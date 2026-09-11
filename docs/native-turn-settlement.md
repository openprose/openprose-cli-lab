# Claude native turn outcomes and process settlement

With `--output-contract native`, Claude's `result` records are candidate turn outcomes. Background-task notifications can produce more than one result within a single process/session. The runner preserves those observations and waits for natural process settlement; it does not close stdin or emit completion because an interim result arrived.

Transport completion requires process exit 0 and a fresh successful native result. Later non-result activity makes a candidate stale until another actual result arrives, except the narrowly correlated shutdown bookkeeping described below. Consecutive same-session results replace the candidate. Error results, changed session identity, malformed/unsupported records, missing fresh result, nonzero exit, timeout, cancellation, or cleanup failure do not become success. No terminal is fabricated from assistant text or process exit alone.

This conservative initial rule can reject harmless telemetry after a result; no speculative metadata whitelist is applied. Existing event/session checks remain, but Claude's adapter does not comprehensively pair every tool call/result. Native completion does not prove program fulfillment, source correctness, or tool-history completeness. Full turn records remain available through opt-in native capture. Rust native-Claude human output is buffered until settlement so its older incremental envelope projection cannot reject valid continuations; machine evidence remains complete.

Legacy image-envelope mode retains its single-result behavior. Other harness adapters are unchanged. The historical failed pool runs remain failed observations even when their captured records pass later provider-free replay.

Native mode also accepts repeated initialization records after initial start when every field equals the first initialization except a nonempty UUID. Native background resumptions do not guarantee adjacency to task-notification events. Changed session, tools, model, authentication source, or other metadata is rejected. Repeated initialization is activity and invalidates a prior candidate. Legacy adjacency checks remain unchanged.

The sole mutable routing exception is `messaging_socket_path`: it may be absent or a nonempty string, and may appear, disappear, or change on a later init. It is excluded only from equality comparison; actual records remain unchanged. This does not weaken identity/auth/tool/model/cwd checks or permit unknown metadata changes.

## Correlated shutdown bookkeeping

A final native success candidate may remain usable across a closed shutdown suffix: empty background inventory, a killed/end-time update for an already-known background `local_bash` task correlated to an earlier observed Bash tool call, and its matching stopped notification. Each update must close, identity/session/UUID checks remain, and process exit0 is still required. Native records are retained exactly; a stopped helper is not recast as a successful task. New assistant/tool activity, new/unknown tasks, mismatched identities, nonempty inventories, incomplete closures, or unclassified trailing events still invalidate completion. This does not inspect command text or program fulfillment. Legacy mode is unchanged.

The Rust process supervisor retains actual candidate evidence through the stream; native adapter validation determines freshness at settlement. Seeing a candidate is distinct from transport completion.
