# Codex instruction-placement comparison

Status: the original IMP-008 Bun comparison is retained as historical context. The subsequent [startup parity candidate](kernel-startup.md) makes append the ordinary compiled default in Bun and Rust. Replacement/framed alternatives remain explicit-image Bun experiments. No strict-wrapper admission or release promotion follows from these changes.

The build script accepts `--codex-instructions developer` or `--codex-instructions base` alongside its existing verified image arguments. The selection is compiled into the executable; runtime environment variables do not change it. `framed` is the unchanged default.

Both candidates keep the exact native version allowlist, selected credentials, tools and sandbox controls. Both deliver only canonical opaque task JSON through stdin, closing it after writing. Neither parses a Prose command or implements kernel semantics.

- `developer` uses `-c developer_instructions=<TOML string>` inside the native `exec` option scope. It adds the verified full image text to the native developer instructions and preserves the model's built-in base instructions.
- `base` uses `-c model_instructions_file=<TOML path>` in the same scope, referring to the private image file whose lifetime is owned by the runner. It replaces the model's built-in base instructions. Native tool schemas, managed policy and selected sandbox controls remain the harness's responsibility; this is not a claim that all native behavioral guidance survives replacement.

Each configuration has a separate shared recipe and SHA-256 identity. Dry-run and result records report `developer` or `model-base` placement. The task digest remains distinct from the delivered-image digest. Placement descriptions concern the native instruction channel, not proof of model compliance.

Build with a language image and use `--output-contract native` for kernel-backed execution. The normal echo image remains nonsemantic regardless of placement. Native settlement does not establish task fulfillment; inspect artifacts and available execution evidence.

The shared placement fixture covers Unicode, quotes, backslashes and newlines, separate task delivery, exec-scoped options, unchanged version admission and preservation of the default. The user authorized one live attempt per candidate, sequentially, with a five-minute deadline and the same pinned kernel, program, requested model and existing ChatGPT login. Retain the exact build and image identities with the lab results.

**Evidence — [IMP-008](https://github.com/openprose/openprose-workspace/blob/main/work/items/IMP-008.md):** maintained task, trial limits and eventual findings.
