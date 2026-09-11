# 0005: One machine-operation contract across products

Status: accepted for Phase 6 implementation

## Context

The Rust and Bun candidates implemented the runner-owned operations before a
shared black-box contract existed. Their `doctor`, `harness list`, and
`config explain` JSON consequently drifted in schema names, nesting, fields,
default color behavior, and exit status. That makes agent automation depend on
which distribution was installed and invalidates a fair developer-experience
bake-off.

The help surface also advertised `auth` and `benchmark` operations that were
not implemented. An advertised operation must either work or fail with its
own honest, actionable boundary; it must not be parsed as an unknown command.

## Decision

- `doctor`, `harness list`, and `config explain` use the closed shared schemas
  in `cli/shared/schemas/` and are covered by the same black-box differential
  corpus as language forwarding.
- `doctor` returns the selected problem's stable exit code when the selected
  route is not ready. It exits zero only when that route is ready.
- `doctor` includes the complete harness inventory and the complete effective
  configuration. It never includes secrets or prompt/image bodies.
- `harness list` includes the selected harness and the same ordered descriptor
  objects used by `doctor`.
- `config explain` uses explicit value/source pairs. Candidate config paths are
  reported even when the files do not exist. Machine/non-TTY operation has no
  color by default.
- Runtime, availability, prompt placement, isolation, authentication, billing,
  and admission states use closed values. An implementation-specific variant
  must be explicitly namespaced and cannot silently replace a stable ID.
- `auth status` is a separately versioned account report. Until the hosted
  account service and OS credential-storage design are admitted, it reports
  `availability: unavailable`, unknown authentication state, an OpenProse
  billing owner, and `HOSTED_UNAVAILABLE` with exit 10. Login/logout fail at the
  same boundary; third-party harness authentication remains harness-owned.
- `prose cli benchmark` is not retained in the v1 runner surface. Official
  benchmark runs invoke ordinary installed artifacts externally and cannot be
  detected or special-cased by a candidate. A future non-authoritative local
  facade requires a new shared contract before it can be advertised.

Product-specific runner identity may appear only in contracts that explicitly
declare it. No normalization may hide other machine-surface drift.

## Consequences

Both implementations will initially fail the Phase 6 operation corpus. Each
product is repaired independently against the oracle. The release gate
requires schema validation, identical normalized results, identical exit
semantics, and no harness start for these local operations.
