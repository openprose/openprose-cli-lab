# Windows native-first resolution oracle

This directory freezes a provider-free contract for discovering a Windows
agent installation without executing a package-manager shim. It is an
executable design oracle, not product code and not an admission decision.

All profiles are non-admitting. The Codex and Claude records are frozen
research facts showing feasible launch-profile shapes for particular official
artifacts. Prime and OMP remain explicitly blocked because the researched
versions did not expose a complete, authenticated Windows launch closure.
Nothing here establishes wrapper admission, semantic conformance, native
Windows readiness, or release eligibility.

## Contract

Resolution has only two shapes:

- `native-executable`: resolve an exact `.exe`, either directly or within a
  named package layout, then authenticate every listed artifact and the exact
  package-manifest object before any version probe.
- `runtime-entrypoint`: authenticate the runtime `.exe`, entrypoint, manifest,
  and every load-bearing package artifact; invoke the entrypoint as a literal
  argv prefix. The synthetic fixture proves this shape only. A real profile is
  incomplete until its entire load-bearing closure is frozen.

The artifact list binds role, safe relative path, byte length, SHA-256, and
format. The manifest path must identify the same artifact as the manifest
binding. Environment inheritance, stripping, and fixed values are closed and
disjoint; secrets are represented only by variable names, never values.
Discovery fails on zero or multiple authenticated candidates.

`.cmd`, `.bat`, and `.ps1` files are neither parsed nor opened, even if hostile
files occupy the expected command name. There is no shell, PTY, subprocess,
provider, model, credential, session, or language-program dependency in this
lane. A package-root directory link (the shape used by some package stores) may
be canonicalized once, but every intermediate and final artifact descendant
link or reparse point is rejected. The oracle schemas are resolved and applied
entirely from local files, with network retrieval disabled. A production Windows
resolver must additionally hold verified file handles through spawn, use
Windows reparse-point-safe traversal, and authenticate native publisher or
release-manifest provenance. Those are external product gates.

The Codex profile intentionally invokes its platform executable directly and
does not reproduce the meta-package launcher's managed-package environment.
The Claude research profile freezes the platform package's documented wrapper
marker. In both cases, existing subscription authentication can only survive
through the explicitly inherited user-profile/config environment; this oracle
does not access or prove an authenticated account.

## Files and tests

- `oracle.v1.json`: frozen profiles, blockers, invariants, and source facts.
- `oracle.schema.json` and `launch-profile.schema.json`: closed machine-readable
  schemas. The Python model also checks cross-field constraints JSON Schema
  cannot express succinctly.
- `resolver_model.py`: standard-library, no-process reference resolver.
- `fixtures/hostile-bin/`: inert hostile shim fixtures used to prove they are
  ignored.
- `test_windows_resolution.py`: provider-free positive and adversarial tests.

Run from this directory:

```sh
python3 -m unittest -v test_windows_resolution.py
python3 -m json.tool oracle.v1.json >/dev/null
python3 -m json.tool oracle.schema.json >/dev/null
python3 -m json.tool launch-profile.schema.json >/dev/null
```

No command above starts an agent or consumes provider credentials.
