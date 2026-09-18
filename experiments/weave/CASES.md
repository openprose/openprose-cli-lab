# Experimental lifecycle cases (before implementation)

Single owner, one binding, bounded step. Bindings are supplied by caller; no Markdown parser.

1. Fresh satisfied evidence yields rest, zero actions.
2. Same evidence and policy before expiry reuses satisfaction, zero assessments/actions.
3. Changed evidence invalidates cached satisfaction; unknown/gap never authorizes action.
4. Unsatisfied evidence starts at most one action, records pending checkpoint before invoking it, then rereads and reassesses.
5. Actor returning normally is not acceptance. Failed repair remains unsatisfied; no hidden retry.
6. Contract/policy/selector identity change invalidates reuse.
7. Evidence changes during assessment: discard assessment; no action or acceptance on obsolete input.
8. Persisted pending attempt after interruption: report recovery-needed, never blindly replay effects.
9. Action exception: preserve pending attempt and disclose unknown outcome.
10. Budget exhaustion prevents launching work but permits assessment.

This synchronous reference requires external serialization and durable checkpoint writes. It does not claim multi-process locking, remote exactly-once effects, or atomic world updates. Its final read detects tested races but cannot guarantee the world never changes after return.
