# OpenProse CLI implementation rules

These instructions apply only beneath `cli/`.

The CLI is an outer runner, not an OpenProse interpreter. Treat the Skill
Runtime Image and the model-facing task envelope as opaque, versioned inputs.
Do not parse OpenProse source, infer command semantics, edit `skills/`, or add
dependencies on repository language/kernel packages.

All observable behavior starts in the shared black-box conformance corpus.
Rust and Bun implement that corpus independently. A product-specific unit test
may tighten an implementation but may not redefine shared behavior.

Agents share one worktree. Before editing, confirm an active exact path lease
in `protocol/OWNERSHIP.md`. Do not edit outside the lease, run a formatter over
unleased paths, change a shared manifest without a lease, or perform Git/index
operations. Stop and report any required cross-lease change to the lead.

Process adapters must spawn argument arrays directly, remain noninteractive,
keep structured stdout separate from diagnostics, and never invoke a shell or
allocate an outer PTY. No adapter may silently change harness, transport,
prompt placement, credentials, or billing owner.

Ordinary tests are hermetic: use fresh roots, an allowlisted environment, fake
executables/transports, fixed observable clocks/IDs, bounded resources, and no
provider credentials or network. Real-harness tests are always explicit and
opt-in.

Do not create a public artifact containing the sentinel image. The sentinel is
transport-test material only; canonical language inputs remain an external
release gate.
