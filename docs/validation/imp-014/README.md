# IMP-014 local validation

Validation applies to CLI code at `5256387f92c2f852f00032bd7ba009564518a400`;
subsequent evidence/navigation changes do not rebuild or qualify different code.
These records are provider-free engineering tests on macOS arm64. Paths in logs
are historical execution context. No credentials, installed packages or binaries
are included. Checksums authenticate retained bytes, not a release authority.

## Results

- Full Bun suite: 545 passed, two existing skips. Typecheck passed.
- Shared contracts: 25 passed. Package unit suite: 81 run, two skipped.
- Rust core: 162 passed, two ignored. Process supervisor: 26 unit and 40 integration tests passed.
- New plan adapter: four passed. npm identity: three passed, including offline installation under `@openprose/prose`, launcher integrity bindings and child failure exit propagation.
- The actual Bun candidate was separately installed under the proposed npm identity from local tarballs and returned `prose 0.1.0 (bun)`.
- Final full rehearsal passed: standalone Rust, standalone Bun and npm launcher; 144 candidate/case validations, followed by one deterministic mock timing trial per surface. See [rehearsal.json](rehearsal.json). This is not a performance comparison or semantic qualification.
- The updated Rust supervisor also passed `cargo +1.87.0 check --target x86_64-pc-windows-gnu`; this is compile-only evidence.
- Both workflow files passed actionlint 1.7.12. The generated Homebrew formula passed Ruby syntax checking; Homebrew download/install remains untested without published qualified artifacts.
- A Git three-way merge of this candidate with IMP-008 `3937f741da7635a288a2ac788d68ef81dc44b35e` completed without conflicts. No branch was merged or IMP-008 worktree changed.

Final rehearsal toolchain: Node 24.20.0 (official download checksum verified),
Python 3.10.20 with the repository's hashed wheel requirements, Bun 1.3.5 and
Rust 1.87.0. Earlier unit checks also used installed Node 24.19.0. Rehearsal
binaries intentionally report source `development`; the external code revision
above identifies this test, and those binaries cannot pass the hosting plan's
full-commit source check. They contain the sentinel and must not be published.

## Failures and corrections

Retained initial failures: missing Python dependencies (01), missing approved
LICENSE (02), Python 3.9 API incompatibility (03), and an unsuccessful Python
3.11 dependency setup (04; the existing hash set targets 3.10). The user approved
MIT and the source now includes its standard text and the existing OpenProse
copyright notice. Tooling now uses Python 3.10.20 rather than weakening hashes.

Rehearsal 05 exposed missing Bun fake-transport diagnostics. Rehearsal 06 then
exposed missing Rust byte counts. Shared cases were extended before the fixes;
no normalization or comparison was relaxed. The full rehearsal subsequently
passed.

The supervisor backpressure test failed twice because the settlement loop slept
after empty channel polls. A wait for incoming data within the same deadline
fixed the issue without changing the timeout or assertions. Both failures and
the corrected result are retained. An interim progress update incorrectly
reported a rerun as passing because a later shell command succeeded; it was
corrected when the individual test log was inspected.

The first broad Bun suite after the diagnostic fix failed two old assertions
that required the fields to be absent. Those assertions now require the shared
contract's exact field set; the final broad suite passes.

## Limits and next checks

No GitHub Actions run, model call, release, deployment, registry mutation or
Homebrew tap creation occurred. GitHub rejected both implementation pushes
because the current OAuth credential lacks `workflow` scope. Platform execution
outside this machine and kernel-backed qualification remain pending. The new
npm identity is enabled only for development packaging; registry ownership,
version lineage and public release authority must be completed before release.


The subsequent npm rehearsal driver was tested against the exact final package,
with two additional rejection tests. [Preferred identity report](preferred-npm-identity.json)
binds the new tarballs to the original Bun executable and records the successful
offline install/launch. Those tarballs remain local development artifacts.
