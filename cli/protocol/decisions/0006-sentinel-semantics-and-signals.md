# 0006: Transport fixtures make no semantic claim; OS cancellation is observable

Status: accepted for Phase 6 hardening

## Context

The deterministic mock and fake-process sentinel exercise byte delivery,
framing, supervision, and terminal settlement. They do not load a canonical
language image or execute OpenProse semantics. Their initial fixtures reported
`semantic.status: success`, which could escape the benchmark's stricter claim
override and be mistaken for semantic or Prose Complete evidence.

The product binaries also have injected cancellation tests, but an injected
token is not evidence that an installed command handles a real operating-system
interrupt. Direct artifact probes found that POSIX `SIGINT` was not normalized
to the existing `CANCELLED` result contract.

## Decision

- A sentinel/mock run that completes its transport contract reports
  `semantic.status: not-applicable`. It may exit zero because the requested
  transport-fixture operation completed and has no semantic operation to fail.
- `not-applicable` is valid only for an explicitly test-only adapter and a
  release-ineligible image. Stable language-bearing adapters never use it.
- The sentinel terminal fixture carries the same `not-applicable` value. A
  product must not reinterpret a language terminal; this fixture change is the
  test authority itself.
- Benchmarks validate and retain this status and keep semantic scorecards
  unevaluated. They may classify a trial as a transport success only after
  expected exit, mechanical output validation, explicit containment, and
  explicit process settlement all pass.
- Missing containment or settlement evidence is ineligible for success; absence
  is not equivalent to an affirmative settled result.
- On platforms where catchable process cancellation is advertised, `SIGINT`
  and the platform-equivalent control event request cancellation of the owned
  run. The wrapper emits one terminal result/event with `CANCELLED`, exits 24,
  and verifies descendant cleanup. It must not die with the native signal or
  later misclassify the run as a startup timeout.
- The npm launcher preserves the installed Bun artifact's signal and exit
  semantics. Launcher transparency does not substitute for testing the two
  product binaries directly.

## Consequences

The shared result and sentinel-terminal schemas, sentinel manifest/digests,
embedded bundle, black-box cases, both product implementations, checked
benchmark evidence, package evidence, and documentation must be regenerated in
that order. Existing evidence is stale until its frozen artifact and image
digests are updated from the corrected candidates.

Windows signal and descendant behavior remains blocked until the native Job
Object host is integrated and exercised on Windows; POSIX evidence cannot be
used to infer that result.
