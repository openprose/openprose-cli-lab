# Installed-adapter product adversary report

Status: **green for the provider-free functional-alpha gate** on the local
macOS host. This is not strict-wrapper admission and is not a semantic
compatibility claim.

The suite runs ten independent test methods against both built products. It
covers:

- missing-harness failure and canonical `auto` selection without fallback;
- honest list, doctor, and dry-run readiness, billing/auth, prompt placement,
  and isolation;
- ordinary, non-seam success through fake installed Claude, Codex, Prime, and
  OMP executables;
- exact recipe argv including model placement, explicit global
  `--auth-profile` selection, full `echo-v0` image/task wire bytes, private 0600
  prompt files and cleanup, selected credential environment, and RPC
  correlation;
- exact recipe-byte descriptor digests, schema-valid JSONL, recomputed event
  digests, preserved assistant message boundaries, and removal of the final
  image terminal from visible events; and
- normalized Rust/Bun result and event parity across all four adapters.

The OMP path targets upstream `@oh-my-pi/pi-coding-agent@18.0.9` exactly and
covers its ready-first, order-flexible RPC stream plus the closed terminal
metadata/compaction shapes emitted by that version.

No provider network, model, subscription, or real credential is used. The
provider-free harness emits the image-owned `not-applicable` terminal, so a
successful run proves only functional-alpha transport mechanics.

Open gates remain explicit: real-harness prompt and auth behavior still needs
opt-in live evidence; OpenProse semantics remain absent until a language-owned
runtime image replaces `echo-v0`; strict detached-descendant containment is not
claimed; and installed-process functional-alpha execution remains unadmitted on
Windows.
