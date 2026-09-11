# npm migration and rollback runbook

This is the operator runbook for the separately authorized protected alpha
promotion workflow. Adding or testing that workflow does not authorize a
publication. The admitted candidate remains marked `publicationAuthorized:
false`; approval belongs to the protected environment and the human operator.

## Registry boundary

The public `@openprose/prose-cli` package existed before this CLI implementation.
The registry observation pinned in `npm-registry-lineage.v1.json` records stable
`0.14.0` as `latest` and the first unused successor lineage as
`0.15.0-alpha.N`, beginning with `0.15.0-alpha.1`. Four proposed platform
packages were not found when that observation was made. A recorded absence is
not a namespace reservation.

During the functional alpha:

- publish only exact `0.15.0-alpha.N` versions under the `alpha` dist-tag;
- preserve `latest` on `0.14.0`;
- never reuse an npm version, even after a failed or partial publication;
- publish platform packages before the meta package; and
- keep the legacy stable and the new alpha side by side. Do not claim config,
  behavior, or automatic-upgrade compatibility until it is separately tested.

The checked-in authority makes source and workflow decisions deterministic and
offline. It cannot establish current registry ownership, package availability,
dist-tags, or version availability. A release operator must perform the live
registry checks below immediately before every publication attempt.

## Preconditions

Do not publish unless all of these are true:

1. A designated release owner has confirmed every **Public-alpha-ready**
   requirement in [`ALPHA_READINESS.md`](ALPHA_READINESS.md). This confirmation
   includes the inherited candidate-ready and draft-ready requirements. Source
   code and workflow tests do not prove these external controls; the owner must
   record the supporting decisions and live observations with the release.
2. The root README, release, contribution, Terms, and privacy guidance has been
   reconciled with the optional, nonsemantic CLI alpha. The Terms and license
   claims cover the downloadable packages, and the privacy disclosure explains
   that a selected harness and provider receive task data even though the CLI
   does not add OpenProse-operated telemetry.
3. An authoritative dependency-license review has decided which notices or
   license texts every distribution requires. A current vulnerability review
   covers the exact Rust, Bun, npm, and Windows-helper dependency closure.
4. The project owner has accepted or replaced the disclosed macOS signing and
   notarization posture. GitHub checksums and attestations do not substitute for
   a Developer ID signature or Apple notarization.
5. The contribution-governance owner has recorded whether a CLA, DCO, or
   neither applies, and the public contributor guidance states that decision.
6. The protected promotion workflow runs one mutation-free job that verifies
   GitHub artifact attestations: exactly one verification for every exact draft
   asset, including the aggregate checksum file, before the selected mutation
   job can start. The publication and settlement path also requires npm
   registry provenance for each exact tarball; GitHub promotion cannot occur
   until that provenance has been observed and verified in the public registry.
7. The candidate is the exact commit and version admitted by the protected
   functional-alpha draft workflow. Record that workflow's numeric run ID,
   run attempt, draft release ID, retained authority artifact name, and
   authority SHA-256 from its sanitized handoff summary. The authority's
   repository, source/control SHA, version, tag, release body digest, and full
   asset inventory must identify the selected draft exactly.
8. The repository's alpha release environment, required-reviewer policy, tag
   creation/update/deletion rules, npm ownership, and trusted-publisher or OIDC
   configuration have been independently verified.
9. The exact meta and platform versions are unused in the live registry. The
   platform package names still resolve either to the intended OpenProse-owned
   packages or to `404`; a `404` alone does not establish authority to publish.
10. The live `@openprose/prose-cli` `latest` tag and published-version history
   agree with the pinned migration boundary, or a reviewed successor authority
   has replaced it. Any drift stops the release.
11. Publication credentials are short-lived and scoped. The release operator
   has a captured, access-controlled log of the public metadata queried and
   the local tarball names, sizes, SHA-256 digests, and npm integrity values.
12. The GitHub environment `openprose-cli-alpha-publish` requires a human
   reviewer. Only the protected `main` branch can use it. The repository's tag
   rules prevent the admitted `cli-v<version>` tag from moving or disappearing.
13. The mutation jobs use the workflow-pinned Node.js 24.20.0 runtime and the
   authenticated npm 11.15.0 client archive described below. Trusted
   publishing is configured for this exact repository, workflow file, and
   environment before a staged transition is selected.

The following queries are read-only templates. Use an npm configuration that
does not silently redirect the registry, and retain their JSON output with the
release incident record:

```sh
VERSION=0.15.0-alpha.1
npm view @openprose/prose-cli name version versions dist-tags time dist --json
npm view @openprose/prose-cli maintainers --json

for PACKAGE in \
  @openprose/prose-cli-darwin-arm64 \
  @openprose/prose-cli-darwin-x64 \
  @openprose/prose-cli-linux-arm64-gnu \
  @openprose/prose-cli-linux-x64-gnu
do
  npm view "$PACKAGE@$VERSION" name version dist --json
done

npm view "@openprose/prose-cli@$VERSION" name version dist --json
```

The expected result for every exact candidate version is `404`. A package-level
`404` for a proposed platform name is also expected before the first alpha.
Any returned candidate version, unexpected owner, registry redirect, malformed
response, timeout, or authentication ambiguity is a hard stop. Do not interpret
a network failure as absence.

## Pinned npm mutation tool custody

`bootstrap`, `stage-platforms`, and `stage-meta` download the npm 11.15.0
archive from the fixed public-registry URL in a credential-free step. The step
uses a fresh runner-temporary directory, accepts HTTPS only, follows no
redirect, and enforces bounded connection, transfer-time, and response-size
limits. It never installs npm globally. `settle-and-promote` performs no npm
download, extraction, or execution.

Before extraction, the promotion helper independently verifies the archive's
exact 2,901,197-byte length, SHA-1, SHA-256, SHA-512, and registry SRI identity.
It accepts only a bounded, duplicate-free set of regular `package/` members;
absolute paths, traversal, links, devices, and oversized members fail closed.
The helper extracts into a fresh private directory and verifies the closed tree
digest plus the exact `package/bin/npm-cli.js` digest. It invokes that entry
only as an argument to the exact resolved Node.js 24.20.0 executable. It never
uses a shell, an ambient npm executable, a package shebang, or PATH to select
the mutation tool.

The helper reauthenticates the downloaded archive, extracted tree, npm entry
point, Node.js executable bytes, and both exact versions before and after every
publish or stage attempt. A post-attempt mismatch is ambiguous and burns the
version under the same rule as a lost npm response. The closed settlement's
`npmMutationToolchain` field records only public versions and cryptographic
identities; it never records a runner path, configuration path, credential, or
command output. That field is `null` for `settle-and-promote`, which has no npm
mutation authority.

## Pre-mutation GitHub attestation custody

The functional-alpha build refuses a candidate unless the requested source SHA
is the exact `main` workflow-control commit (`github.sha`). It does not accept
an older ancestor. The candidate checkouts remain physically separate from the
control checkout, but this equality makes the source and signer digest emitted
by the pinned `actions/attest` step the exact candidate SHA.

The unconditional promotion preflight first rejects every non-`main`,
malformed, incomplete, or incorrectly confirmed dispatch. It downloads the
single explicitly named
`openprose-cli-alpha-draft-authority-run-<run_id>-attempt-<attempt>` artifact
from the supplied producing run, checks the operator-supplied SHA-256, and
retains that exact file under a current-run custody artifact. A missing,
replaced, ambiguous, or differently hashed authority fails before the
attestation or protected-environment jobs can start.

One current-run attestation job then downloads and authenticates the closed
38-asset draft before any protected mutation environment is entered. It has
only `contents: read` and `attestations: read`: it has no npm token, OIDC token,
or contents-write authority. It invokes one absolute GitHub CLI executable
directly for each asset, with bounded time and output, no shell, no retry, and
an isolated configuration directory. Verification requires the exact
repository, source SHA, `refs/heads/main` source ref, functional-alpha signer
workflow, signer-workflow SHA, SLSA provenance-v1 predicate, GitHub Actions
OIDC issuer, and a non-self-hosted signer. The controller accepts exactly one
verified result whose signed statement contains the exact asset name and
SHA-256 digest. A missing, duplicate, mismatched, timed-out, or malformed
result fails the run before any npm or GitHub mutation.

The GitHub CLI is supplied by the GitHub-hosted runner image rather than by a
repository-pinned archive. The workflow resolves it to one absolute executable;
the controller authenticates its bytes before and after use and records its
observed version, byte length, and SHA-256 digest as
`externally-provisioned-github-cli`. This is an explicit external verifier
authority, not a reproducible repository-owned toolchain. The GitHub CLI
cryptographically enforces the repository, source, workflow, signer, issuer,
and runner constraints through its verification flags. The controller also
checks the signed subject and predicate in the JSON result, but does not claim
to be an independent implementation of Sigstore certificate verification.

After the 38 attestations settle, the read-only controller reauthenticates all
downloaded asset bytes and atomically writes one canonical
`github-attestation-evidence.json`. That record binds the exact draft authority
digest and producer run/attempt, current promotion run/attempt, release body and
asset-inventory digests, candidate identity, per-asset result, and observed
verifier identity. It is uploaded once under a current-run/attempt artifact
name. Every mutation job downloads that same artifact and the same current-run
copy of the draft authority, verifies the evidence digest passed directly from
the attestation job, and rejects evidence from another run or attempt.

Mutation jobs have no `attestations: read`, GitHub CLI path, or verifier
invocation. The controller independently redownloads the draft, validates its
body and inventory against the immutable authority instead of regenerating
release notes from the current `main`, and repeats a local byte check
immediately before every npm mutation attempt and before GitHub draft
promotion. If evidence validation or byte reauthentication fails, the atomic
settlement retains the sanitized authority, evidence, tool, per-asset outcome,
verified count, and failure state; it never retains a token, local path,
command output, release body, or certificate body.

## Protected promotion operations

Run `.github/workflows/openprose-cli-alpha-promote.yml` manually from `main`.
For every operation, supply these exact operator inputs from the one producing
draft workflow and its retained summary:

- `operation`: one of `bootstrap`, `stage-platforms`, `stage-meta`, or
  `settle-and-promote`;
- `version`: numbered alpha SemVer without `v`;
- `source_sha`: full lowercase 40-character source SHA;
- `release_id`: positive numeric draft release ID;
- `draft_authority_run_id`: positive numeric producing workflow run ID;
- `draft_authority_run_attempt`: positive numeric producing run attempt;
- `draft_authority_sha256`: full lowercase 64-character SHA-256 of the retained
  `alpha-draft-authority.json`; and
- `confirmation`: the exact text below, substituting all seven preceding
  identity values.

```text
PROMOTE <operation> <version> <source_sha> <release_id> AUTHORITY <draft_authority_run_id>/<draft_authority_run_attempt> <draft_authority_sha256>
```

Every operation downloads the existing draft's closed 38-asset inventory,
reauthenticates it against the source, tag, manifests, admissions, aggregate
checksums, and the pre-mutation attestation policy above. It uses the downloaded
npm tarballs directly. It does not build, pack, or modify the candidate. It
computes SHA-256, npm SHA-1 shasum, and npm SHA-512 integrity locally and
verifies the public registry's downloaded bytes against the same tarballs. A
public package settles only when its registry metadata also declares the exact
SLSA provenance predicate
`https://slsa.dev/provenance/v1`.

Use exactly one of these paths.

### One-time bootstrap for the first alpha

The four platform package names did not exist at the pinned registry
observation. npm staged publishing cannot create a package. For the first
publication only:

1. Configure the protected environment's one-time
   `OPENPROSE_NPM_BOOTSTRAP_TOKEN` secret. The token must be scoped to the
   intended OpenProse organization and must be authorized to create the four
   package names.
2. Select `bootstrap`. The workflow verifies that all four platform package
   names are still absent and that the meta version is unused.
3. The workflow directly publishes the four platform packages, in closed
   order, with scripts disabled, public access, the exact `alpha` tag, and
   provenance. It verifies each package's registry integrity and downloaded
   tarball before continuing.
4. Only after all four platform packages settle does it publish and verify the
   meta package. The `latest` tag must remain on the pinned stable version.
5. Revoke and delete the granular bootstrap token, then remove the
   `OPENPROSE_NPM_BOOTSTRAP_TOKEN` GitHub environment secret immediately after
   the first cohort settles. Configure stage-only trusted publishers for all
   five package names. Disallow traditional npm tokens for later releases and
   retain human two-factor-authenticated approval for every stage. Do not keep
   the bootstrap credential as a fallback.
6. Select `settle-and-promote` in a new protected run. This operation verifies
   the complete public cohort and only then changes the existing GitHub draft
   to `draft: false, prerelease: true`.

Any bootstrap mutation attempt that does not settle exactly burns the version.
Do not rerun the operation with that version, even when npm output suggests
that the failure occurred before publication. Registry visibility can lag and
the client response can be lost. Revoke and delete the granular bootstrap token
and remove its GitHub environment secret after a failed or partial attempt too.

### Later alphas through npm staged publishing

After all five package names exist and trusted publishers are configured:

1. Select `stage-platforms`. The workflow uses OIDC, without an npm token, and
   stages the four platform tarballs. It does not approve them.
2. A human reviews and approves those staged packages in npm with two-factor
   authentication. The workflow never handles an OTP and never approves a
   staged package.
3. Select `stage-meta`. The workflow first verifies all four public platform
   packages, their exact bytes, and their `alpha` tags. It then stages only the
   meta tarball.
4. A human separately reviews and approves the staged meta package in npm with
   two-factor authentication.
5. Select `settle-and-promote`. The workflow verifies the five public packages,
   exact integrity and dependency edges, exact `alpha` tags, and the unchanged
   stable `latest` tag. Only then does it publish the existing GitHub draft as a
   prerelease.

Trusted OIDC can stage packages but cannot list, approve, or reject staged
packages. An operator must not substitute a direct publish for either approval
step. If a stage result is absent or ambiguous, do not retry it automatically;
inspect npm with an authorized human account and use a new alpha version when
the outcome cannot be proved.

Every run retains a closed `npm-publication-settlement.json` workflow artifact.
The helper writes it atomically before verification or mutation and after each
observed outcome. It contains package names, public digests, the closed
per-asset GitHub attestation result, exact immutable draft authority identity,
the externally provisioned verifier's safe observed identity, and the
sanitized pinned mutation-tool identity when applicable, but no credential,
private path, command output, release body, certificate body, or registry
response body. Retain all authority, attestation, and settlement artifacts for
incident review.
A settlement is evidence, not permission to resume a version after an
attempted mutation.

Never publish or stage the meta package first: consumers could receive a
launcher whose platform dependency does not yet exist. Never use an
unqualified publish that could move `latest` during the alpha.

## Consumer migration

Treat the alpha as an explicit opt-in, not an upgrade to the existing stable.
Install it into a versioned prefix so both CLIs remain available:

```sh
ALPHA_VERSION=0.15.0-alpha.1
ALPHA_PREFIX="$HOME/.local/openprose-cli-$ALPHA_VERSION"
npm install --global --ignore-scripts --prefix "$ALPHA_PREFIX" \
  "@openprose/prose-cli@$ALPHA_VERSION"
"$ALPHA_PREFIX/bin/prose" cli doctor
```

Pin the exact version in automation. Do not install the alpha via an unqualified
package name or treat the `alpha` tag as reproducible. To return to the legacy
stable, invoke the pre-existing `0.14.0` installation or reinstall it into a
different explicit prefix. Removing the versioned alpha prefix must not modify
the stable prefix or shared user configuration.

## Rollback and incident response

### Before any registry publication

Leave the GitHub release as a draft, stop the run, and diagnose the evidence.
Do not replace assets in place, move or recreate the tag, or reinterpret a
failed admission. Correct the source and produce the next unused alpha version.

If a stage was created but has not been approved, reject it manually in npm
with an authorized two-factor-authenticated account. The workflow intentionally
does not automate stage rejection.

### After partial npm publication

Npm versions are immutable release identities. Record exactly which packages
and integrities became public. Deprecate every affected published version with
an incident message, but never reuse or silently replace that version. Keep
`latest` on `0.14.0`. Do not publish the meta package if any platform package is
missing or mismatched. Repair with a new `0.15.0-alpha.N` sequence and repeat
the full cohort admission and live verification.

If only some platform packages were published, they remain an incomplete
cohort and must not be selected by the meta package. If the meta package was
published, immediately deprecate it as well as every affected platform version
and remove or move the `alpha` dist-tag to the last known complete alpha. Do not
delete the GitHub tag, draft, manifest, or incident evidence merely to hide the
failed attempt.

Do not rerun `bootstrap`, `stage-platforms`, or `stage-meta` after any ambiguous
mutation attempt. Preserve its settlement evidence and choose the next unused
alpha version unless an authorized human can prove and safely reject a pending
npm stage.

### After a complete public alpha

Deprecate the faulty exact cohort. Move `alpha` back to the most recent verified
complete alpha, or remove the tag when none exists; confirm again that `latest`
remains `0.14.0`. Edit the GitHub prerelease description to put an `Affected`
notice first, naming the exact version, failure, deprecation state, and retained
incident-evidence location; do not rewrite or remove its assets. Retain the
release-edit audit event with the incident evidence. Ship the correction as the
next unused alpha sequence.

### Future stable release

A stable promotion needs a separately reviewed migration authority. If a future
stable must be rolled back, point `latest` to the last verified stable, deprecate
the faulty exact version, preserve its evidence, and release a new version for
the correction. Do not unpublish and reuse a stable version.

For every incident, preserve the workflow run, source/tag peel, environment
approval, registry responses, tarball digests, integrity values, SBOM,
dependency inventory, provenance, and release manifest. Record the start and
end time, affected package/version cohort, consumer impact, tag changes,
operator actions, and the criteria used to restore a dist-tag.
