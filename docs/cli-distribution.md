# CLI distribution work in progress (IMP-014)

This branch is independent of IMP-008 startup changes. It adds a provider-free
Actions rehearsal for the existing packager and an adapter that exports verified
package bytes to the distribution repository's reviewed-plan format. It does
not publish npm, create GitHub releases, deploy the endpoint or change kernel
startup. Dependency locks and IMP-008 startup behavior are unchanged. Rehearsal uncovered
pre-existing fake-transport diagnostic drift and a bounded reader-settlement
timing problem; the focused fixes and their shared cases are included here.

## Installation strategy and naming

The user's preferred public npm identity is `@openprose/prose`, subject to
ownership and registry checks. On September 16, 2026, public `npm view` returned
not found or inaccessible for that name; this is not proof of availability.
`@openprose/prose-cli` currently reports latest `0.14.0` without a deprecation
field for that version. Legacy packaging defaults and release authorities still use the latter name.
The packager now supports `--npm-package-name @openprose/prose` for explicit
development rehearsals: meta/platform identities, launcher root binding,
launcher self-digest and generated installation instructions change together.
The new identity installed offline and preserved child exit status in a
provider-free test; the actual Bun candidate also launched through that install.
Non-development publication under the new name remains blocked until registry
lineage and promotion authority are reviewed. Do not publish an alternative name or deprecate the old package
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

Install Rust 1.87.0, Bun 1.3.5, Node 24.20.0 and Python 3.10.20. Install the hashed
Python requirements in a private virtual environment, and the frozen Bun lock:

```sh
python3.10 -m venv .venv-distribution
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

Local export tests pass (four tests), and npm identity tests pass (three tests,
including an offline installation and failure exit propagation). Actions syntax
passes actionlint 1.7.12.
The initial rehearsal stopped because the active Python interpreter lacked the
pinned contract dependencies. Using the prepared virtual environment proceeded
through builds and then failed at packaging because root `LICENSE` is absent.
Rust metadata and npm package generation declare MIT; the kernel repository
already carries MIT with Copyright (c) 2026 OpenProse. The user subsequently approved MIT; the standard license text and existing
2026 OpenProse copyright notice are now included at repository root.
The subsequent rehearsal found a Python 3.9 `Path.write_text` incompatibility;
Python 3.11 was also incompatible with the retained wheel-hash set. Python
3.10.20 matches the existing pinned dependency set and is now the workflow pin.

The next complete rehearsal reached cross-product checks and exposed missing
fake-transport diagnostics in Bun and missing byte counts in Rust. Shared
cases now require the same closed reasons, admitted-record counts and bounded
byte counts. Those targeted changes let the three installed surfaces pass all
144 candidate/case validations in the local one-trial rehearsal. This remains
development sentinel evidence, not a kernel or release qualification.

The Rust supervisor's existing backpressure test failed twice: sleeping one
millisecond after empty polls could exhaust its 250 ms drain deadline. Waiting
for a channel message within the same deadline fixes that behavior without
extending the production timeout or reducing assertions. Retain the failures
alongside the corrected test result.

Before public release: finish the npm-name migration and ownership checks,
restore or replace missing release/promotion authorities, qualify the actual
IMP-008 artifact set, pass native installation lanes, decide macOS signing and
notarization, verify dependency notices, and configure publication credentials.
No model runs, registry changes or global Prose installations occurred in this work.
Build/test toolchains were prepared separately in temporary directories.


## Current local handoff

[Retained validation](validation/imp-014/README.md) records passing suites,
corrected failures, exact tool versions and limitations. The complete local
rehearsal passes on macOS arm64. `rehearse_npm_identity.py PACKAGE --out FRESH`
then repackages the verified Bun archive under the preferred npm identity,
installs the exact local tarball pair with scripts and registry access disabled,
and verifies the launched version. The Actions workflow includes that step.
No global CLI installation is required.

The candidates are now published in CLI PR 2 and distribution PR 1. The first
remote rehearsal exposed two fresh-runner setup defects: setup-python did not
provide Python 3.10.20 for macOS ARM, and the offline Rust build lacked downloaded
Cargo dependencies on the other three platforms. The workflow now obtains the
same pinned Python through pinned uv and explicitly fetches the locked Cargo
dependencies before retaining offline build behavior. Remote rerun results remain
a separate qualification step. The user authorized merge and publication on
September 16, 2026; actual release artifacts still require the stated gates.
