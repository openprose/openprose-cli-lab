# CLI publication setup

Status (September 17, 2026 UTC): implementation in progress. No CLI RC has been
published. The user approved an explicitly unsigned RC under the existing npm
name and requires normal latest-kernel startup. Apple enrollment and signed
macOS releases are a separate workspace task, IMP-015. Unsigned means there is
no Developer ID signature or Apple notarization; npm provenance and Sigstore
artifact signatures do not remove that macOS limitation.

## npm trusted publisher

Configure the existing `@openprose/prose-cli` package with this exact identity:

| Field | Value |
| --- | --- |
| Provider | GitHub Actions |
| Organization/user | `openprose` |
| Repository | `prose-cli` |
| Workflow filename | `cli-publish.yml` |
| Environment | `publication` |
| Allowed action | Direct `npm publish` |

The file is `.github/workflows/cli-publish.yml`; npm receives only its filename.
Do not authorize the distribution rehearsal workflow. Create the GitHub
`publication` environment with main-only deployment. This environment is configured; no required reviewer is currently enabled. The owner configured the root package trusted publisher on September 17, 2026. The four platform packages still require first-publication bootstrap and their own trusted publishers.
The publishing job uses GitHub-hosted runners and `id-token: write`; it does
not require `NPM_TOKEN`. The repository is public, as required for npm
provenance. Every generated package must declare this repository URL.

The four binary packages require separate identical trusted-publisher entries:

- `@openprose/prose-cli-darwin-arm64`
- `@openprose/prose-cli-darwin-x64`
- `@openprose/prose-cli-linux-arm64-gnu`
- `@openprose/prose-cli-linux-x64-gnu`

On the date above, the root package's `latest` was `0.14.0`; all four binary
package lookups returned 404. That does not prove ownership or availability.
Trusted publishing requires an existing package. A first-publication bootstrap
and subsequent per-package trust setup are therefore still required. Do not
publish placeholders or use a broad permanent token to bypass that prerequisite.
The root package's existing trusted publisher alone cannot create the children.

References checked September 17, 2026:
[npm trusted publishers](https://docs.npmjs.com/trusted-publishers/),
[npm trust prerequisites](https://docs.npmjs.com/cli/v11/commands/npm-trust/),
[npm provenance](https://docs.npmjs.com/generating-provenance-statements/).
Recheck these before changing workflow identity or credential policy.

## Publication boundary

The manual workflow consumes a reviewed plan from `cli/release/plans/` on main
and immutable draft artifacts. It does not build, execute or rewrite downloaded
binaries. The validator binds the version, source, protected preflight, kernel
qualification and every artifact hash. It checks the complete platform inventory
before download and again locally. npm and standalone Bun bytes must agree.
Packages are published platform-first and root-last; an existing version is
accepted only if its integrity matches the exact reviewed tarball. Failed
registry requests are not treated as proof that a version is absent.

Only an explicit `X.Y.Z-rc.N` plan may select `signing: unsigned-rc`. That is the
owner-approved exception. Stable publication still requires the Apple signing
path. All artifacts receive detached Sigstore signatures in the publishing job;
those and the npm result are retained as workflow evidence. They must also be
retained with the public release before claiming complete download verification.
Do not promote an incomplete release or replace npm's `latest` tag with an RC.

The existing fixed-image full-release preflight is not proof of moving-kernel
startup. The separate `kernel-rc` build/package path verifies release profile,
disabled test seams, latest-kernel policy and fresh offline installations on all
four platforms. `cli-kernel-rc.yml` runs on PRs for validation and manually from
main for actual candidates. `assemble_kernel_rc.py` verifies the native reports
and package bytes; without exact-binary live smoke evidence, it emits an
unqualified development plan that publication refuses. Do not satisfy the gate by embedding a fixed kernel, relabeling
an echo/sentinel fixture, or fabricating a protected passing report. No qualified
plan has been committed. Existing alpha workflows and their independent
requirements are not silently replaced by this new path.

## Deferred macOS signing

`cli/ci/sign_macos.py` signs copied Bun/Rust binaries in a fresh directory,
checks Developer ID, team, hardened runtime, timestamp and entitlements, and
submits a ZIP for notarization. It accepts a receipt only when Apple's response
binds the exact archive hash. `--verify-existing` rechecks the same evidence
without signing or submitting again. Its tests use fake native commands; live
Apple qualification has not occurred.

The caller creates, unlocks and removes a temporary signing keychain. Required
future environment secrets are `APPLE_DEVELOPER_ID_P12_BASE64`,
`APPLE_DEVELOPER_ID_P12_PASSWORD`, `APPLE_NOTARY_KEY_P8`,
`APPLE_NOTARY_KEY_ID`, and `APPLE_NOTARY_ISSUER_ID`; non-secret variables are
`APPLE_TEAM_ID` and `APPLE_SIGNING_IDENTITY`. Do not store these in the model
API-key `.env`. Sign before packaging and publish a new version after signing;
never replace an unsigned version. See the workspace
[Apple signing task](https://github.com/openprose/openprose-workspace/blob/main/work/items/IMP-015.md).

## Local validation

Run the publication and signing unit tests without credentials:

```sh
python3 -m unittest discover -s cli/ci -p test_publication.py
python3 -m unittest discover -s cli/ci -p test_sign_macos.py
```

The fixtures are synthetic and must never become publication plans. Native
installation qualification, a real reviewed plan, registry bootstrap/OIDC,
public byte verification and receipt retention are separate acceptance steps.
