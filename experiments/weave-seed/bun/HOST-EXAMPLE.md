# Persistent Bun host walkthrough

This unpublished example exercises the [local host](../HOST.md) with real synthetic source and output files. Assessment compares the two strings; action writes the selected desired string to the actual file. No credentials, provider calls, kernel loader, or automatic prose binding are involved.

From the repository root, with Bun 1.3.5 on `PATH`:

```sh
WEAVE_EXAMPLE_PARENT=$(mktemp -d)
bun experiments/weave-seed/bun/persistent-example.mjs "$WEAVE_EXAMPLE_PARENT/bun"
```

The argument must be a new directory whose parent already exists. The example refuses an existing directory before writing anything. Use a new leaf for another run; do not point it at a real project or a directory containing customer data. The files remain available for manual inspection after the command returns.

| Event | Status | Attempts | Actions | Actual file |
|---|---|---:|---:|---|
| `initial-repair` | `satisfied` | 1 | 1 | Tuesday |
| `reopened-reuse` | `reused` | 1 | 1 | Tuesday |
| `desired-changed` | `satisfied` | 2 | 2 | Wednesday |
| `source-corrupt` | `evidence-gap` | 2 | 2 | Wednesday |
| `source-unavailable` | `evidence-gap` | 2 | 2 | Wednesday |

Each event constructs a new `FileHost` and loads `host/checkpoint.json`. The action asserts that its pending attempt is already present on disk before it writes `actual.txt`. The source change is detected because evidence identity covers both file values and their source directory. The corrupt UTF-8 source and then the missing source produce gaps and no further action. The observer uses the current clock with a 60-second validity interval; the comparison and repair callbacks are deterministic. The attempt limit is two for the persisted checkpoint.

At completion, `actual.txt` contains Wednesday, `desired.txt` is deliberately absent, and the checkpoint has two attempts and an unknown disposition after the evidence gaps. The checkpoint does not attest that a report was written or that a real external service changed. The example reopens storage within one process; separate-process contention, abrupt termination, and interrupted-action recovery are exercised by the host tests:

```sh
bun experiments/weave-seed/bun/host.test.mjs
```

The host serializes its checkpoint and saves through a synced temporary file and atomic replacement. It does not make the action's file write transactional with the checkpoint. A lock left after interruption or uncertain persistence needs trusted reconciliation; the example does not remove stale locks or settle uncertain effects. This remains a trusted local-directory experiment, with no network-filesystem, hostile-writer, power-loss, distributed-transaction, or v1-readiness claim.

For real OpenProse work, the selected kernel and adopted contract must first define the complete obligation. A host must then explicitly bind that agreement, evidence selection, policy, and permissions. The demonstration's string comparison is only its synthetic rule. See the separate [kernel-backed prose example](../examples/brief.prose.md) and [entry-route explanation](../examples/README.md#a-kernel-backed-prose-example).
