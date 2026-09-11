# W6 red oracle

Before this bundle contract, Rust `main.rs` and Bun `assets/sentinel.ts`
compiled five source paths directly, including exactly two payload filenames.
Replacing the language-owned image with three payloads or renamed external
artifacts therefore required product source edits.

The W6 oracle is red until all of these are data-only operations:

- build and validate an arbitrary image-format-v1 directory;
- stage one deterministic bundle and checksum;
- build both products against a synthetic image containing at least three
  payloads and renamed external artifacts;
- observe identical aggregate and exact model-visible digests;
- reject source drift, bundle tamper, unsafe files, and sentinel release use.

This note records the red boundary only. It grants no semantic or release
claim.
