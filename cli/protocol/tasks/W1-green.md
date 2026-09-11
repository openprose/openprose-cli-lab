# W1 walking-skeleton green evidence

Oracle commit: `ed2458c9585f64b7deed0df4c8ce07f6a926f1fd`.

The following commands passed from a cleanly addressed product workspace before
the implementation commit:

- Rust: `cargo test --workspace --all-targets --locked --offline` — 27 tests.
- Rust: `cargo clippy --workspace --all-targets --locked --offline -- -D warnings`.
- Rust: `cargo fmt --all -- --check`.
- Bun: `bun run check` — strict typecheck, 31 tests/97 assertions, and
  standalone compilation with runtime autoload disabled.
- Shared: 14 schema/image/case tests and 4 fake-harness tests.

Both runners pass exact shared help, preserve hostile opaque argument
boundaries, expose the same local diagnostics, require explicit mock selection,
and fail the OpenProse-billed default closed with `HOSTED_UNAVAILABLE` and no
fallback. The sentinel remains release-ineligible.

The later lead-owned gate at `6be7c54b923e724c28ad1eb6b8ab245b5e73630c`
first failed on real product drift, then passed the full Phase-1 corpus after
both products consumed the shared descriptors: 8 cases × 2 products,
differential-clean. Individual product tests were not treated as differential
proof.
