# OpenProse CLI — Bun implementation

This directory is an independent Bun 1.3.5 workspace for the OpenProse outer
runner. It does not parse OpenProse programs. It verifies an opaque Skill
Runtime Image, preserves task argument boundaries, selects a harness adapter,
and emits the shared runner records.

This is the thin, noninteractive CLI wrapper. It never opens or controls a TUI.
An interactive Prime/OMP/Codex/Claude session runs OpenProse directly through
the installed `open-prose` skill and does not invoke this binary. Prompts and
other language-facing Markdown remain owned by that skill/image layer.

## Local development

```sh
bun install --frozen-lockfile
bun run check
./dist/prose --help
./dist/prose cli harness list
./dist/prose --harness codex --output json run fixture.prose.md
```

`bun run check` performs strict TypeScript checking, unit/integration/shared-
schema tests, standalone compilation, and standalone smoke tests. The compile
command disables Bun's runtime `.env`, `bunfig.toml`, `tsconfig.json`, and
`package.json` autoloading. A test places hostile `.env` and `bunfig.toml`
files in the invoked working directory and proves that the standalone ignores
them.

`bun run build` is the ordinary local build, matching Rust's default: it embeds
`echo-v0`, reports the `development` profile, and compiles with test seams off.
It writes `dist/prose`. `bun run build:test` is the explicit test-only build;
it creates a private temporary `sentinel-v1` bundle, enables test seams, writes
`dist/prose-test` by default, and removes the temporary bundle. The test-only
command refuses to overwrite `dist/prose`. `bun run build:release` embeds the
release-eligible `echo-v0` image with test seams off.

Ordinary builds embed the deliberately nonsemantic `echo-v0` image. They can
discover and run exactly admitted user-installed Prime, OMP, Codex, and Claude
harnesses through direct argument-array subprocesses. The adapters preserve
the complete image/task boundary, use adapter-specific credential allowlists,
and recover the image-owned terminal envelope without a shell, outer PTY, or
harness fallback. Codex and Claude can use their installed login state. Prime
and OMP additionally require a fully qualified `provider/model` plus an
explicit auth profile. `prime-harness-login` and `omp-harness-login` preserve
the respective HOME-backed harness store while stripping provider credential
environment variables; readiness and the selected provider, account, and
billing identity remain unknown, and neither name is a subscription claim.
Provider-key profiles remain separate explicit choices and receive a fresh
runner-owned mode-0700 config directory for the complete child/service
lifetime, preventing ambient harness stores from outranking the chosen key.
Prime/OMP doctor, dry-run, and run preflight never invoke model-list/help auth
probes; every profile remains unknown until actual execution, with no fallback.
Every actual Prime child receives the adapter-owned
`PRIME_AGENT_TELEMETRY=0` opt-out, overriding ambient conflict without changing
credentials; OMP, Codex, and Claude never receive that control.
Every actual OMP child receives exact `--no-tools`, exact `--no-lsp`, and a final private
mode-0600 config overlay that disables retry and the exact OMP 18.0.9 MCP discovery
providers. Its prompt is withheld until the
post-start correlated `get_state` response proves `dumpTools` is empty.
Interactive UI, control drift, a nonempty inventory, and nonterminal
`agent_end` fail closed; nonterminal settlement reports the fixed
`unsupported_nonterminal_settlement` reason.
Functional-alpha admission is an exact audited allowlist: Prime `0.7.0` or
`0.8.1`, OMP `18.0.9`, Codex `0.149.0-alpha.4.1`, and Claude `2.1.243`.
Nearby patches and prereleases are detected but refused, with the detected
identity, exact admitted set, and a copyable repair command in machine and
human doctor/run errors. The CLI build remains on its pinned Bun 1.3.5
toolchain; the separate upstream OMP package/source audit used Bun 1.3.14.

Prime runs use one private per-run daemon socket. If its owned service cannot
be settled, OpenProse recursively removes prompt, image, task, credential, and
cache artifacts within bounded entry, byte, and depth limits. Symlinks are
unlinked without being followed. It then reports an opaque cleanup handle and
the fixed `cli cleanup prime <handle>` argument suffix. Set `PROSE` to the exact
downloaded executable that reported the failure and run `"$PROSE" cli cleanup
prime <handle>`. Recovery accepts
only the original mode-0700, current-user directory and mode-0600 marker under
the process's same canonical temporary root, reauthenticates Prime's exact
socket/version handshake, and refuses symlinks, unexpected entries, changed
markers, or alternate roots. A private retry ticket keeps the same handle valid
if final directory removal is interrupted. Use `"$PROSE" --output json cli
cleanup prime <handle>` for the single closed machine report. If `TMPDIR` or the platform
temporary root changes before recovery, restore that environment first; the
command fails safely rather than searching other directories.

Standalone compilation selects one exact native target. Every x64 release
binary uses Bun's `baseline` runtime variant rather than the AVX2-oriented
standard target; ARM64 binaries use their exact native target. Unsupported
build hosts fail before producing a mislabeled artifact.

The exact same user journey is available in release-profile functional-alpha
builds. A completed `echo-v0` invocation proves transport completion only: it
does not parse OpenProse, claim semantic success, or earn a strict-wrapper
claim. The default `openprose` identity still fails closed with
`HOSTED_UNAVAILABLE` until OpenProse-billed execution exists.

The deterministic `mock` and `fake-process` transports remain test-only. They
are admitted only by the explicit `bun run build:test` command, which carries
the release-ineligible sentinel image and enables test seams. Ordinary
`bun run build` and release-profile builds carry `echo-v0` with test seams off
and refuse these transports before execution. Internal provider-free controls
cannot be enabled in a release build and never become a harness, language, or
billing fallback.

Human mode streams only adapter-parser-accepted assistant prose. It withholds
control-shaped object/array lines and any prefix that reproduces credential,
task, model, working-directory, invocation, or runtime-image values; after a
successful withheld response it prints a fixed safety notice. JSON and JSONL
output paths remain governed by their settled machine schemas.

Unix process supervision uses a best-effort original process group with
graceful-then-hard group termination. This is sufficient for the controlled
fixture whose descendants remain in that group, but it is not strict or
race-free descendant containment: a child can escape with `setsid()`, so Unix
results never set `cleanupVerified` to true. The Windows product now uses an
authenticated, exact-sibling native process-host sidecar with bounded streaming
and Job Object supervision. The integration and package shape are provider-free
tested, but the compiled release admission remains off until the exact packaged pair runs
successfully on native Windows and that evidence is retained.

## Packaging boundary

The current build produces `dist/prose`, a local Bun standalone carrying
`echo-v0`. Functional-alpha packaging creates platform-specific npm packages
and a small Node-compatible launcher in `@openprose/prose-cli`. Both products
embed the same exact prerelease SemVer and source revision through
`OPENPROSE_BUILD_VERSION` and `OPENPROSE_BUILD_COMMIT`. The launcher selects an
exact-version optional platform package, verifies the executable's byte length
and SHA-256, performs no download or postinstall work, forwards signals, and
preserves the binary exit status. Isolated install, integrity, and foreground
signal tests exercise the packed tarballs without a registry.

The functional alpha is explicitly transport-only. Its package evidence keeps
publication and full-release admission false; a semantic release remains
blocked on the canonical language-owned image and its external authorities.

The Node interpreter used by installed-package conformance is deliberately not
copied away from its dynamic libraries. Its resolved installed executable is
byte-checked before and after each invocation, while the JavaScript launcher is
executed from a private verified snapshot with its original CommonJS package
context. The interpreter's dynamic/resource closure is not captured, and a
same-user mutation between the last check and process start remains possible;
that lane cannot provide release or strict-custody authority.
