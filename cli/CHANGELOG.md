# OpenProse CLI changelog

This changelog covers the independent CLI implementation under `cli/`. The
repository-level changelog covers the OpenProse language, skill, and plugin
track separately.

Before creating `cli-vX.Y.Z-alpha.N`, move that release's entries into an exact
dated section named `## [X.Y.Z-alpha.N] — YYYY-MM-DD`. A published alpha must
not remain only under `[Unreleased]`; retain `[Unreleased]` for changes after
the tagged release.

## [Unreleased] — 0.15.0-alpha.N functional-alpha train

### Added

- A transport-only functional-alpha package train for standalone Rust,
  standalone Bun, and npm installation on the explicitly admitted POSIX
  platforms.
- Exact installed-harness adapters for the admitted Prime, OMP, Codex, and
  Claude versions, with platform-specific availability and explicit
  authentication boundaries.
- Provider-free package admission and opt-in, cost-acknowledged live transport
  evidence for the nonsemantic `echo-v0` image.

### Compatibility and release boundary

- This is a new implementation that reuses the `@openprose/prose-cli` package
  lineage after the historical `@openprose/prose-cli` implementation was
  removed. It does not claim configuration, behavior, or automatic-upgrade
  compatibility with the historical package.
- The `0.15.0-alpha.N` CLI train is independent of the repository's language,
  skill, and plugin versions with the same numeric core.
- The functional alpha transports an opaque task argument vector through the
  selected harness. It does not execute OpenProse semantics or establish
  program portability, semantic conformance, strict containment, provider
  identity, billing identity, or reliability.
- This changelog entry does not claim that an artifact is published and does
  not authorize publication. Package manifests and protected release controls
  remain the authority for any future artifact.
