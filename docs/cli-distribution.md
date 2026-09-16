# CLI distribution work in progress (IMP-014)

This branch is independent of IMP-008 startup changes. It adds a provider-free
Actions rehearsal for the existing packager and an adapter that exports verified
package bytes to the distribution repository's reviewed-plan format. It does
not publish npm, create GitHub releases, deploy the endpoint or change kernel
startup. Runtime source and dependency locks are unchanged.

## Installation strategy and naming

The user's preferred public npm identity is `@openprose/prose`, subject to
ownership and registry checks. On September 16, 2026, public `npm view` returned
not found or inaccessible for that name; this is not proof of availability.
`@openprose/prose-cli` currently reports latest `0.14.0` without a deprecation
field for that version. Existing packaging and launcher integrity checks still
use the latter name. A reviewed, tested migration must update the package,
platform packages, launcher identity, registry-lineage authority and promotion
checks together. Do not publish an alternative name or deprecate the old package
without owner approval. Do not overwrite old versions.

Standalone Bun and Rust downloads remain available independently. Proposed
Homebrew setup: a public `openprose/homebrew-tap` GitHub repository, with Rust
installed by `brew install openprose/tap/prose`, and an explicit `prose-bun`
alternative that avoids executable collisions. These formula names and the Rust
default are proposals, not deployed decisions. No separate Homebrew publisher
account is required. The distribution repository can render formulae from
qualified exact artifact plans. Stable formulae require all four declared
platforms; RC/development consumers use explicit versioned artifacts until a
separate prerelease formula policy is agreed.

## Local build and installation rehearsal

Install Rust 1.87.0, Bun 1.3.5, Node 24.20.0 and Python 3.11. Install the hashed
Python requirements in a private virtual environment, and the frozen Bun lock:

```sh
python3 -m venv .venv-distribution
. .venv-distribution/bin/activate
python3 -m pip install --require-hashes --only-binary=:all: -r cli/ci/requirements-test.txt
(cd cli/bun && bun install --frozen-lockfile --ignore-scripts)
python3 -m unittest discover -s cli/ci -p test_distribution_plan.py
python3 cli/ci/rehearse_release.py --output /tmp/prose-rehearsal-NEW --trials 1
```

Use a fresh output directory every time. The existing rehearsal builds both
implementations, packages them, extracts standalone installs, installs npm
tarballs offline without lifecycle scripts, and performs deterministic mock
checks. It uses development test seams and the sentinel; these artifacts must
never be released publicly. It makes no provider calls and leaves local build
outputs plus the requested disposable evidence directory. It does not establish
kernel semantics, supported-platform qualification or release readiness.

The Actions workflow `.github/workflows/cli-distribution-check.yml` runs this
rehearsal on macOS arm64/x64 and Linux arm64/x64. These are proposed test lanes,
not claims of success before execution. No publication secrets are provided.
Windows is excluded pending its separate native containment qualification.
Existing documentation references other release workflows missing from this
repository; this focused rehearsal does not claim to restore those authorities.

## Hosting boundary

For an already-qualified package directory, `cli/ci/distribution_plan.py`
checks every artifact against its package manifest and binds the original
manifest, checksums, SBOM, dependency evidence and provenance into a hosting
plan. It never rebuilds, renames versions or grants qualification. It requires
an immutable evidence URL and writes a fresh local plan for review. Alpha
versions are rejected rather than silently renamed into the new `dev`/`rc`
channels. Reconciling the earlier alpha train is a release decision.

The distribution publisher fetches exact reviewed GitHub release assets,
checks source/tag and artifact digests, and mirrors them under
`/cli/releases/VERSION/`. Channel documents have separate `dev`, `rc`, and
`stable` pointers. Reviewed kernel qualification is required before any public
staging. The kernel's `/kernel.md` pointer is unchanged. Neither endpoint
checksums nor a pinned CLI guarantee a pinned kernel; record both selections.

## Observed blockers and validation

Local export tests pass (four tests); Actions syntax passes actionlint 1.7.12.
The initial rehearsal stopped because the active Python interpreter lacked the
pinned contract dependencies. Using the prepared virtual environment proceeded
through builds and then failed at packaging because root `LICENSE` is absent.
Rust metadata and npm package generation declare MIT; the kernel repository
already carries MIT with Copyright (c) 2026 OpenProse. License confirmation and
the actual file are pending; no placeholder legal text was substituted.

Before public release: finish the npm-name migration and ownership checks,
restore or replace missing release/promotion authorities, qualify the actual
IMP-008 artifact set, pass native installation lanes, decide macOS signing and
notarization, verify dependency notices, and configure publication credentials.
No model runs, registry changes or global installations occurred in this work.
