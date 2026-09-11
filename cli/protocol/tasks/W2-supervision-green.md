# W2 process-supervision green evidence

Shared process cases were introduced by oracle commit
`ed2458c9585f64b7deed0df4c8ce07f6a926f1fd`. The implementation preserves the
attempted-process evidence contract in decision 0003.

Lead verification on macOS arm64:

- `cargo test --workspace --all-targets --locked --offline` — 44 tests.
- `cargo clippy --workspace --all-targets --locked --offline -- -D warnings`.
- `cargo fmt --all -- --check`.
- `bun run check` — 74 tests/220 expectations, typecheck, standalone build.
- Fake-harness suite repeated ten times after hardening its pipe race — green.
- `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/runner/run.py --phase 2`
  — 13 cases × 2 products, differential-clean.

The tested Unix contract uses direct argv, no outer PTY, separate bounded
streams, owned process groups, TERM→grace→KILL, nonce/PID descendant audit,
private prompt files, recursion rejection, environment allowlisting, and exact
native exit/signal evidence. Claims cover catchable termination and the owned
group, not an unobservable escaped process.

Windows strict containment truthfully returns `TRANSPORT_UNSUPPORTED` in both
products. W2 is locally green but the specification's cross-platform Phase-2
exit remains blocked until race-free Windows Job assignment and matching CI
evidence exist or Windows is formally demoted.
