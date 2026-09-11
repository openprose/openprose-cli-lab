# Rust substrate labs

These are isolated Phase 4 experiments, not stable `prose` adapters. They
import no OpenProse language implementation and are absent from stable help,
configuration, packaging, and compatibility claims.

`openprose-acp-transport-lab` proves that `agent-client-protocol` 2.0.0 is a
useful typed client/transport for an ACP agent. Its in-memory demonstration
covers ACP v1 negotiation, sessions, streamed updates, permission callbacks,
and cooperative session cancellation. It intentionally does **not** pretend
that the SDK supplies an agent loop, model, billing, process containment, or
OpenProse semantics.

`openprose-rig-embedded-lab` proves that Rig 0.42.0 is a true in-process agent
candidate. Its scripted model runs an embedded tool loop, streams output,
receives arbitrary instruction/task payloads unchanged, and drops pending
model work on cancellation. A real provider route, complete coding/subagent
tools, remote cancellation, and semantic conformance remain open work.

The exact admission posture, auth/billing ownership, local packaging
measurements, and source links are machine-readable in `scorecard.json`.

## Verify without providers

Install Rust 1.90 plus Clippy and rustfmt, fetch the locked crates once, then
run:

```sh
./verify.sh
```

The verifier is offline after dependency fetch and neither demonstration reads
credentials or makes a network/model call. Run the two binaries directly to
see their JSON evidence:

```sh
cargo run --locked --offline -p openprose-acp-transport-lab
cargo run --locked --offline -p openprose-rig-embedded-lab
```

The lab needs Rust 1.90 because Rig's current resolved graph includes
`ordered-float` 5.5.0, which declares that floor. ACP 2.0.0 itself declares
Rust 1.88. This cost is quarantined from the stable Rust 1.87 CLI workspace.

Primary references: [ACP Rust SDK](https://github.com/agentclientprotocol/rust-sdk),
[ACP SDK architecture](https://agentclientprotocol.github.io/rust-sdk/),
[Rig 0.42 API](https://docs.rs/rig/0.42.0/rig/), and
[Rig source](https://github.com/0xPlaygrounds/rig).
