# Rust local host experiment

This unpublished POSIX host implements the shared [local host contract](../HOST.md) around the existing [bounded Rust core](../rust/lib.rs). The standalone crate keeps its dependencies and lockfile separate from the CLI and preserves the dependency-free `rustc` core test path. It is not a distributed transaction or a published SDK.

Run from the repository root:

```sh
cargo test --offline --manifest-path experiments/weave-seed/rust-host/Cargo.toml
cargo run --offline --manifest-path experiments/weave-seed/rust-host/Cargo.toml -- load /tmp/my-weave-host
cargo run --offline --manifest-path experiments/weave-seed/rust-host/Cargo.toml -- save-fixture /tmp/my-weave-host experiments/weave-seed/fixtures/checkpoint-v1.json
```

On a cold dependency cache, first fetch the committed lockfile's dependencies:

```sh
cargo fetch --locked --manifest-path experiments/weave-seed/rust-host/Cargo.toml
```

That fetch requires network access. The subsequent test and example commands can run offline. `--offline` requires the pinned Cargo dependencies to be present locally. The two binary commands support inspection and explicit fixture import for cross-runtime conformance. They are experimental tools and are not installed CLI commands. Use a fresh trusted directory for fixture import.

`LocalHost::new(directory).step(binding, capabilities, max_attempts)` acquires the directory lock, loads a checkpoint, and delegates to the existing core. Implement `Capabilities` for observation, assessment, action, clock and attempt identities; persistence is supplied by the host. `settle(binding, attempt, outcome, receipt)` saves an explicit settlement without replenishing attempts. `with_lock` exposes scoped `LockedStore::load` and `save` for host tooling.

A save validates and encodes before touching the old checkpoint, creates a unique temporary file, writes and syncs it, renames it over the checkpoint, then syncs the parent directory. If rename or subsequent directory sync fails, the session stays poisoned and retains its lock, including when the caller catches the error. Further load/save calls fail. Reopening reports busy until a trusted operator reconciles the uncertain publication and removes the lock. Abrupt process exit also leaves a conservative lock. Never automatically remove a lock merely because it appears old.

Ordinary success and failures before the rename attempt release only the acquired lock directory, checked by POSIX device/inode identity. The host assumes a trusted local directory. Identity checking is not protection against hostile filesystem races. Network filesystems, distributed exclusion, hard power-loss behavior and receipt authenticity are not established. The host does not retry effects. Unsupported non-POSIX systems return an error.

Validation on macOS Darwin 25.6.0 arm64, Rust/Cargo 1.98.1: shared seed cases and sequential stress; separate-process contention; abrupt process exit after pending persistence; restart/reuse; actor uncertainty and explicit settlement; corrupt JSON, UTF-8 and exact schema rejection; invalid encoding preserving existing bytes; real pre-effect rename failure; replaced-lock preservation; injected post-rename directory-sync failure with sticky poison. The sync fault is injected and is not a claim of power-loss testing. The ignored child helper is invoked explicitly by the process test.

## Persistent file example

Choose a new directory whose parent already exists. The example refuses any existing target before writing files:

```sh
cargo run --offline --locked --manifest-path experiments/weave-seed/rust-host/Cargo.toml --example persistent -- /tmp/my-new-weave-fixture
```

This is an explicitly deterministic sample contract: `actual.txt` must match `desired.txt`, with both files restricted to three fixture day values. It initializes Monday/Tuesday, repairs the actual file, reopens the host and reuses satisfaction, changes the desired value to Wednesday and repairs again, then deliberately corrupts and removes the desired source to demonstrate evidence gaps. It emits five JSON events and makes exactly two permitted actions. There are no model calls or kernel interpretation.

The selected directory retains `actual.txt` containing Wednesday and the checkpoint under `checkpoint/checkpoint.json`. The final gap demonstration leaves `desired.txt` absent. Inspect or remove this disposable fixture directory yourself; the example does not hide its state in an automatically deleted directory. Its maximum of two attempts is cumulative, and every event constructs a new host over the same persisted checkpoint. Filesystem writes in this sample are simple local effects, not transactions with checkpoint persistence.
