# Scripted hosted-service oracle

This is a provider-free Phase-5 transport oracle, not the OpenProse hosted
product. It gives Rust and Bun the same closed, executable fixtures while the
execution placement, account endpoints, token storage, quota/pricing model,
canonical Skill Runtime Image, and semantic terminal schema remain gated.
The public `openprose` adapter must remain unavailable until those gates are
resolved; this fixture is never a fallback.

## What it evaluates

The same `openprose.hosted-wire/1` start-record contract can exercise either candidate
boundary without importing language behavior:

- `local-loop-gateway`: a local agent loop would use an OpenProse model/billing
  gateway.
- `hosted-agent-local-capability-bridge`: a hosted loop requests a narrowly
  scoped local read/write/direct-process capability.

`run.start` carries `runtimeImage` and `taskEnvelope` as distinct, independently
hashed byte claims. The oracle verifies exact bytes and never parses their
meaning. Auth is the closed `none-test-only` category and has no credential
field. Billing owner is recorded as `openprose`, but the evidence authority is
explicitly `test-fixture-only`.

The fake service is a stdio JSONL process. Every service record has request,
run, and sequence correlation; local capability records add a correlation ID.
Data frames consume bounded credit, while terminal/control records remain able
to settle the run. Idempotency keys bind all start fields except the retry's
request ID. The first lifecycle signal wins, with deterministic tie order
`cancel`, then `timeout`, then `disconnect`.

Usage is either `fixture-authoritative`, with explicit token/cost data, or
`unavailable`; zero is never substituted for missing evidence. Auth, quota, and
availability scenarios map to the existing `HOSTED_AUTH_REQUIRED`,
`HOSTED_QUOTA_EXCEEDED`, and `HOSTED_UNAVAILABLE` codes and always report
`fallbackSelected: false`.

The local bridge defaults to no filesystem grants and no executables. A test
must explicitly grant each relative read/write path or direct executable.
Traversal, absolute/backslash/NUL paths, symlink components, ungranted paths,
shell executables, ambient environment names, oversized I/O, and unknown
operations are refused. Direct argv is never re-rendered through a shell.

The default direct-process executor reads stdout and stderr concurrently into a
combined bounded result buffer. It kills and reaps the direct child as soon as
the declared output limit is crossed or the deadline expires. This fixture does
**not** claim a process-tree sandbox: it confines the working directory and
executable grant, but filesystem sandboxing and descendant containment are
`unsupported`. Stable hosted-agent admission therefore needs the platform
containment mechanism supplied by the eventual product implementation.

A structural `run.terminal` or `run.rejected` record is mandatory. Since the
language-owned terminal schema is unavailable, even a mechanically complete
run ends with `semantic.status: unknown` and `SEMANTIC_STATUS_UNKNOWN`. Assistant
text is never promoted to semantic evidence.

## Verify

From this directory, with the repository's test dependency installed:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s . -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 run_cases.py
```

The first command independently validates the JSON Schemas, fake service,
stdio process, bridge, content digests, case index, and all cases. The second
executes the 14 digest-pinned manifests and emits one JSON summary. Neither
command uses a provider credential or network endpoint.

Key files:

- `hosted-protocol.v1.schema.json`: closed wire records.
- `hosted-evidence.v1.schema.json`: service-side transport evidence.
- `capability-bridge-evidence.v1.schema.json`: local bridge evidence.
- `fake_hosted_service.py`: deterministic service and lifecycle state machine.
- `capability_bridge.py`: default-deny local capability implementation.
- `case_oracle.py`: independent manifest executor.
- `../cases/hosted/case-index.v1.json`: immutable file-byte digests.

See `PROPOSED-SHARED-SCHEMA-DELTA.md` for the intentionally unmerged contract
changes that product implementations will eventually need.
