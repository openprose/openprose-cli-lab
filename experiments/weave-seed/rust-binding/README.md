# Native Rust file binding experiment

This unpublished Unix crate implements `integration/binding.mjs` v1 in Rust. It reads explicitly selected file bytes without interpreting Markdown or Prose. The library and observer executable do not invoke Bun, a shell, a hash executable, a provider, or a model. `conformance.mjs` is an optional development comparison with the reference Bun binding.

```sh
cargo build --offline --locked --manifest-path experiments/weave-seed/rust-binding/Cargo.toml
cargo test --offline --locked --manifest-path experiments/weave-seed/rust-binding/Cargo.toml
bun --no-env-file experiments/weave-seed/rust-binding/conformance.mjs
```

On a cold dependency cache, first explicitly fetch the locked dependencies with `cargo fetch --locked --manifest-path experiments/weave-seed/rust-binding/Cargo.toml`, then use the offline commands. No network was used to implement or validate this crate. The lockfile pins serde/serde_json, sha2 and libc dependencies; no shared manifest changes are needed.

The binary `rust-binding/target/debug/weave-file-binding-experiment` consumes one JSON object on stdin and returns one object on stdout:

```json
{"root":"/absolute/caller-selected/root","kernel":"kernel.md","contracts":["contract.md"],"evidence":["state.json"],"policy":"caller-policy/v1","ttlMs":60000,"limit":262144,"now":10}
```

```text
{"binding":"<sha256>","evidence":{"identity":"<sha256>","payload":"<JSON string>","observedAt":10,"validUntil":60010,"gap":false}}
```

`ttlMs` and `limit` are optional with the defaults shown; omitted `now` uses Unix epoch milliseconds. Explicit null is not an omitted value. Invalid declarations, numeric bounds, roots or times exit nonzero, with diagnostics only on stderr. Missing, unreadable, oversized, nonregular, outside-root or invalid-UTF-8 required sources produce a stable generic evidence gap and exit zero. Consumers must inspect `gap`; process success does not mean evidence was available or an agreement was satisfied. Stdin has an independent 1 MiB transport limit. The CLI does not accept credentials or execute capabilities.

## Library interface

Use a path dependency on this crate and `weave_file_binding_experiment::{Config, FileBinding, Evidence}`. `FileBinding::new(config)` validates explicit sources and creates the stable declaration binding. `binding()` returns that hex SHA-256 string; `observe(now_ms)` returns `Result<Evidence, String>`. `Evidence` has public `identity`, `payload`, `observed_at`, `valid_until`, and `gap` fields and camelCase serde serialization. A caller can map these directly into its runtime's evidence type; this crate does not depend on a particular loop implementation. Calls are synchronous; a host requiring cancellation or a wall deadline should isolate observation in its bounded process adapter.

The v1 binding serializes ordered keys `version,sources,policy,ttlMs,limit`. Sources preserve kernel/contract/evidence order and duplicates and contain `role,path` with lexically resolved absolute paths rooted at the canonical root. Successful payload keys are `version,policy,files`; each file has `role,path,sha256,content`. Gap keys are `version,error,policy`, with `required-source-unavailable`. Identity hashes the UTF-8 payload string. File hashes include all raw bytes; content is strict UTF-8 with a single leading BOM removed, matching JavaScript TextDecoder. Timestamps must be nonnegative safe integers, with a safe `now+ttlMs`; bounds are positive safe integers. JSON numeric spellings such as `1e0` and `1.0` follow JavaScript numeric value semantics. Policy blank detection matches ECMAScript trim, including BOM and excluding NEL.

## Scope and limits

Reads form a sequential snapshot of a trusted local filesystem, not an atomic snapshot or a sandbox. Canonical-path containment matches the Bun implementation: internal symlinks and hardlinks are accepted; outside-root symlinks are gaps. An adversarial rename/symlink race can invalidate the canonical-path check before open. No stronger confinement or cross-file atomicity is claimed. Nonblocking open prevents FIFO-open hangs and metadata rejects nonregular files; an ordinary file read on a stalled filesystem can still block. Byte limits do not establish a time deadline.

The configured aggregate limit is caller-selected. Chunked Rust reads avoid preallocating that whole limit, but large allowed content still consumes memory and may exhaust resources. Configure a practical limit in the enclosing host. UTF-8 source names and root paths are required; invalid filesystem names and JavaScript-only lone-surrogate strings are outside the shared input domain. Paths are Unix paths; Windows is explicitly unsupported. Absolute declaration paths mean relocating the root changes the binding, even with identical bytes. This is v1 parity, not a portable publication identity; a future relocation-neutral identity requires a separately versioned contract.

`fixture-v1.json` provides nine shared source/observation cases. Native tests additionally cover changing content, missing/restored sources, exact aggregate bounds, malformed UTF-8, BOM hashes, symlinks, hardlinks, FIFO/nonfiles, policy whitespace, invalid declarations, timestamp overflow, numeric JSON spellings, CLI failures and request limits. The optional differential runner compares full outputs on actual shared temporary files, including 100 changing observations. It never calls a model.

Validated locally with rustc 1.98.1, cargo 1.98.1 and Bun 1.3.5 on macOS arm64: nine native tests and 134 Bun/Rust comparisons. Rustfmt was unavailable in the installed toolchain; formatting validation is not claimed. These observed versions are test evidence, not a platform support guarantee.
