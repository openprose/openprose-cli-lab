# Direct legacy-skill real-harness lane

This lane answers one deliberately narrow question: can an installed Prime
0.7.0 process, given the frozen legacy OpenProse skill explicitly, execute a
portable `prose run program.prose` request without an OpenProse CLI on `PATH`?

It is an opt-in, noninteractive proxy for the direct skill path. It does not
drive or observe an interactive TUI. It does not test either new CLI wrapper,
the current developing language package, a canonical Skill Runtime Image, or
Prose-complete semantics. Its evidence is therefore exploratory and can never
make a current compatibility, strict wrapper, semantic, or release claim.

## Frozen experiment

`policy.v1.json` and `matrix.v1.json` were written before live execution. The
matrix pins:

- Prime's observed version 0.7.0 and executable digest, independent of where
  the operator installed it;
- the explicit legacy skill's complete 74-file tree digest and relevant file
  digests, independent of where the operator installed it;
- exact provider/model route identifiers;
- two immutable programs, two trials per cell, zero runner retries, an absolute
  per-trial deadline, bounded output/workspace retention, and a $2 planned
  threshold.

Prime is invoked with one explicit skill and with ambient skills, extensions,
context files, saved sessions, prompt templates, themes, and builtin tools
disabled. Only `ipython` is re-enabled because the legacy skill uses its
`rlm.run` API for subagents. `PATH` is restricted to a frozen value that does
not resolve `prose`. Every trial uses a fresh temporary directory and creates
an exact, mode-0600 `.prose/.env` containing only
`OPENPROSE_TELEMETRY=disabled` before Prime starts.

The provider is the only permitted network boundary. For routes that declare
credential names, the operator must identify one credential file explicitly;
the runner passes only those named values and never copies, records, or prints
that file. It never discovers or reads an ambient `.env`. Prime's stored login
is used for `prime-inference`. Costs and token usage are whatever Prime reports
and are explicitly non-authoritative for billing.

Despite its historical field name, `maximumTotalCostUsd` is not an
authoritative spend cap. It is a planned ceiling plus a between-trial stop
heuristic over non-authoritative harness-reported values. Missing reported cost
counts as zero for that heuristic, and any one trial can overshoot it. Evidence
and reports never claim otherwise.

## What is retained

Evidence keeps requested and Prime-reported provider/model identifiers,
harness and skill digests, task/program digests, process limits, tool-name
counts, exact output-effect file digests, and bounded redacted diagnostics.
It keeps neither user prompt bodies, raw stdout/transcripts, tool arguments,
workspace file bodies, disposable workspaces, nor credentials. Failures are
retained. A full old-skill effect pass requires the requested route to be
reported exactly, the expected filesystem effects, direct structured
subagent execution proof, unchanged telemetry opt-out, and no observed CLI,
telemetry, extra-network, or consequential tool attempt. This lane currently
retains start observations, not authoritative completion/execution proof, so a
start alone cannot satisfy that pass condition.

The absence of an observed CLI invocation is a bounded observation of Prime's
structured tool stream under this policy, not a universal proof. Binding
effects prove only filesystem effects; they cannot substitute for a directly
observed subagent start. Transcripts are not compared. Reports conservatively
reanalyze a copy of retained metadata with the current analyzer, keep the
original collector classification separately for auditability, and never
rewrite historical observation files.

Text such as `rlm.run(...)` in an `ipython` argument is only a diagnostic
counter; dead code can contain the same text. Only structured subagent-start
events contribute to the start count, and even those do not authorize
`subagentExecutionProven` or a pass classification. Default validation
recomputes every analyzer-owned proof/classification field and rejects
contradictory retained values. Analyzer v3 enforces this; retained v2 evidence
is accepted only where its existing facts are already fail-closed.

New process observations use concurrent bounded readers and one absolute
deadline for execution and settlement. Timeout, overflow, assistant failure,
normal leader exit, and interrupted execution terminate and verify the exact
original POSIX process group, reap the leader, and close both pipes. This is not
strict containment: a descendant that deliberately calls `setsid(2)` leaves
the original group. Evidence therefore always records
`detachedDescendantsContained: false` and `strictContainmentClaimed: false`.
The retained 2026-08-27 observations predate bounded reader settlement; their
original records remain unchanged, and validation does not infer reader,
leader, pipe, or process-group settlement facts for them.

## Commands

Provider-free checks:

```sh
python3 -m unittest -v cli/conformance/direct-skill-real/test_run.py
python3 cli/conformance/direct-skill-real/run.py validate
```

Default `validate` checks the frozen declarations, public program files, closed
evidence schema, and retained evidence bindings. It does not read a skill tree
or execute an installed Prime binary. Retained observations remain bound to the
original pre-portability matrix digest; the current matrix keeps that digest as
an explicit historical identity while omitting the original machine's
locations. An operator can explicitly provide current absolute locations to
check the installed skill tree, executable digest, and version:

```sh
python3 cli/conformance/direct-skill-real/run.py validate \
  --verify-live-inputs \
  --executable /absolute/path/to/prime-agent \
  --skill-root /absolute/path/to/open-prose
```

Validation also regenerates both committed report forms in memory and requires
exact byte equality, without rewriting observations.

Live execution is intentionally guarded by two acknowledgements:

```sh
python3 cli/conformance/direct-skill-real/run.py run \
  --evidence cli/conformance/direct-skill-real/evidence/runs/run-001 \
  --executable /absolute/path/to/prime-agent \
  --skill-root /absolute/path/to/open-prose \
  --env-file /absolute/path/to/credentials.env \
  --i-understand-this-spends-money \
  --i-understand-this-uses-a-legacy-skill
```

`--env-file` is required only when at least one selected route declares a
credential name. The complete frozen matrix includes OpenRouter routes, so the
full command above requires it. The paths are runtime inputs only and are never
written to public evidence.

The run target must be a new directory. All trial evidence and derived reports
are staged privately and published together only after the full run succeeds;
an existing target is never deleted or overwritten, and a failed run publishes
no partial target.

Before a live probe or trial, the runner copies the exact frozen skill tree and
the resolved harness entry's sibling dependency tree into owned execution
custody. Script interpreter bytes (and the macOS Node launch library) are also
snapshotted and digested before the snapshots are executed. Later changes to
those source paths cannot change the run. Transitive dynamic libraries and the
rest of the runtime closure remain external and unbound, so evidence records
`dynamicDependenciesClosed: false` and `semanticAdmission: false`; this creates
no strict-wrapper, semantic, or release authority.

The retained historical observation JSON files predate bounded settlement
metadata and remain byte-for-byte unchanged. Validation does not infer cleanup
facts for those observations. Newly collected evidence includes the closed
`bounded-original-group-v1` settlement record.

Regenerate the deterministic report from retained evidence:

```sh
python3 cli/conformance/direct-skill-real/run.py report
python3 cli/conformance/direct-skill-real/run.py validate
```

Report regeneration is provider-free and does not rewrite any
`*.evidence.json` observation. Without optional live verification, the derived
report labels its file list as the matrix's relevant-file subset while
preserving the declared full-tree digest, count, and byte total.

Current sanitized observations live in `evidence/current/`. The closed JSON
schema is `evidence.schema.json`; the provider-free validator enforces its
types, required fields, constants, dynamic-map value types, and recursive
`additionalProperties: false` boundaries. The policy and matrix must not be
edited in response to live results; a changed experiment requires a new
versioned policy/matrix.
