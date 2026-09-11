# Functional-alpha release readiness

This checklist governs the independent OpenProse CLI functional alpha. It does
not govern the OpenProse language, skill, or plugin release train.

The functional alpha has one narrow promise: a person or agent can install an
exact CLI package, select one admitted user-installed harness, inspect that
selection, and run the noninteractive `echo-v0` transport path. The image asks
the harness to echo an opaque task argument vector. It does not open or execute
an OpenProse program.

The interactive path remains separate. A user may start a supported harness
TUI and use its installed `open-prose` skill without this CLI.

## Readiness stages

| Stage | Meaning | Permitted external effect |
| --- | --- | --- |
| Candidate-ready | One exact source commit satisfies the local product and package contract. | None |
| Draft-ready | Protected automation reproduced, installed, and admitted the exact downloadable bytes. | Create or reconcile one unpublished GitHub draft prerelease. |
| Public-alpha-ready | Human publication authority, registry authority, legal disclosures, and supply-chain evidence are complete for those exact draft bytes. | Publish the admitted npm cohort, then promote the exact GitHub draft. |
| Post-publication verified | The public registry and release return the admitted bytes and clean public installs work. | Retain evidence, or withdraw and supersede a failed alpha. |

A later stage includes every requirement of the earlier stages. Passing one
stage never implies a later stage.

## Candidate-ready requirements

- The candidate is one clean, reviewable commit in the CLI worktree. Its source
  SHA, version, and embedded image identity agree in both implementations.
- Ordinary Rust and Bun development builds use `echo-v0` with test seams off.
  Explicit test builds use the sentinel and test seams. Release builds use
  `echo-v0` with test seams off. A release build with test seams fails closed.
- The complete provider-free local admission passes from its first gate. This
  includes shared contracts, Rust and Bun tests, differential conformance,
  process supervision, package lifecycle, release policy, and documentation
  contracts.
- Rust standalone, Bun standalone, and npm installations expose the same
  public behavior where the shared contract requires parity.
- A clean local package rehearsal installs all three surfaces from packaged
  bytes. Repair, upgrade, and removal guidance is executable, surface-owned,
  platform-specific, and hostile-input tested.
- Configuration, diagnostics, and generated guidance do not expose credentials,
  private values, raw provider output, or terminal control sequences.
- The public README, support guide, contributor guide, CLI changelog, issue
  forms, generated package guides, and known limitations agree.
- Repository-level README, release, contribution, terms, and privacy guidance
  have been reconciled with the optional CLI release without changing the
  independent interactive skill path. A public tag must not point to a commit
  whose root documentation still says that the CLI was removed or that no
  separate binary exists in any context.
- No provider call, tag, package publication, release creation, or repository
  mutation is part of candidate admission.

## Draft-ready requirements

- The candidate commit is on `main`. The exact annotated `cli-vX.Y.Z-alpha.N`
  tag exists, peels to that commit, and is protected from unreviewed creation,
  movement, or deletion.
- `main` and the `openprose-cli-alpha-release` environment have the required
  repository protection and reviewers. A workflow file cannot prove those
  external settings.
- Live npm lineage is revalidated immediately before packaging. The version is
  unused, the package owners are expected, and the stable `latest` tag remains
  on the historical stable version.
- Two physically distinct source roots produce byte-identical closed package
  inventories for every target. The retained receipts bind source, image,
  tools, dependencies, artifacts, and checksums.
- Native CI and package admission complete on every advertised target. A target
  is omitted from the public support table unless its exact package has the
  required execution authority.
- Fresh archive and offline npm installations select each platform-admitted
  harness, run `cli doctor`, and complete `echo-v0` without a per-run harness
  override or provider access.
- One bounded, cost-acknowledged live transport matrix runs against the exact
  candidate bytes and exact admitted harness closures. Every attempt and
  failure remains visible. One passing observation is not a reliability claim.
- Release notes contain exact checksum, install, sign-in, provider-charge,
  support, security-reporting, limitation, and removal guidance.
- The assembled release asset inventory is closed and hash-bound. Candidate
  manifests remain `publicationAuthorized: false`; candidate bytes cannot
  authorize their own publication.
- Protected automation creates or resumes only an unpublished GitHub draft
  prerelease. It does not publish npm or make the release public.

## Public-alpha-ready requirements

- A designated human owner approves the functional-alpha scope and the exact
  draft. The approval is recorded by the protected publication environment.
- The project owner resolves whether the repository Terms apply to downloadable
  MIT packages and makes the Terms, license, and alpha claims consistent.
- The project owner updates the privacy disclosure so it distinguishes absent
  OpenProse-operated telemetry from task data sent to a user-selected harness
  and provider.
- The project owner explicitly accepts or replaces the disclosed macOS alpha
  posture: ad-hoc code signatures, no Developer ID distribution identity, and
  no Apple notarization. Checksum and GitHub attestation verification do not
  substitute for those Apple controls.
- An authoritative dependency-license review decides whether third-party
  notices or license texts are required and verifies their inclusion in every
  distribution surface.
- Vulnerability review is current for the exact dependency closure. Public and
  private security-reporting routes are available without promising an
  unapproved response service level.
- GitHub environments, branch and tag protections, npm ownership, and the npm
  authentication method have been checked from their live control planes.
- The contribution-governance owner has decided whether a contributor license
  agreement, Developer Certificate of Origin, or neither applies. The public
  contributor guide states that decision before accepting external code.
- Downloadable archives and checksum evidence have GitHub artifact
  attestations for the exact draft bytes. Public npm packages carry registry
  provenance for the exact tarballs.
- The first publication of a new platform package name uses an explicitly
  approved one-time bootstrap credential. Later releases use stage-only npm
  trusted publishing and separate maintainer 2FA approval. Automation never
  approves a staged package or handles a one-time password.
- npm platform packages settle and verify before the meta package. The registry
  name, version, metadata, SHA-1, SHA-512 integrity, downloaded bytes, `alpha`
  tag, and preserved `latest` tag all match the admitted plan.
- Every attempted registry mutation produces retained, sanitized settlement
  evidence. An ambiguous or partial publication burns that alpha version; the
  published subset is deprecated and a new version is prepared. A published
  name and version is never reused.
- The GitHub draft is promoted to a public prerelease only after the complete
  npm cohort settles. Promotion revalidates the tag, draft, assets, registry,
  provenance, and human authority and never rebuilds or repacks the candidate.

## Post-publication verification

- Redownload every GitHub asset and verify it against the published aggregate
  checksum and its artifact attestation.
- Query and download every npm package at the exact version. Verify its
  registry integrity, provenance, dependency edges, platform identity, and
  equality with the admitted tarball.
- Confirm that every platform package and the meta package have `alpha` on the
  new version and that `latest` did not move.
- Perform a fresh public installation on each advertised platform. Run
  `--version`, harness selection, and `cli doctor`, then run the
  [provider-free functional-alpha package admission procedure](README.md#functional-alpha-package)
  before any optional live provider check.
- Retain the publication settlement, public-install results, failures, and
  rollback decision with the exact source and artifact identities.
- If public verification fails, stop new installation guidance, deprecate the
  affected npm versions, keep the failed evidence, and supersede with a new
  alpha. Do not silently replace release assets or reuse the version.

## Claims the functional alpha does not make

Even after public verification, `echo-v0` does not establish OpenProse
execution, semantic conformance, program portability, Prose Completeness,
strict descendant containment, provider or billing identity, comparative
quality, cost efficiency, or reliability. The OpenProse-hosted billing route
may remain unavailable and must fail closed. Benchmark admission and the
canonical language-owned Skill Runtime Image remain separate release tracks.

Current evidence and blockers are recorded in
[`../protocol/STATUS.md`](../protocol/STATUS.md). Construction and rehearsal
commands are documented in [`README.md`](README.md); withdrawal and
supersession are documented in
[`MIGRATION_AND_ROLLBACK.md`](MIGRATION_AND_ROLLBACK.md).
