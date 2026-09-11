# OpenProse CLI lab

Private development of two independent outer runners: Rust and Bun (distributed through npm). These runners load a Markdown-owned image, deliver a task to an existing agent harness, and transport observable results. They do not implement OpenProse language semantics.

The initial import preserves the current `cli/` working tree from the predecessor repository, including uncommitted work. `provenance/import.json` records every imported file hash and the predecessor HEAD; `provenance/source-status.txt` records its CLI working-tree state. This is not a claim that the import equals that Git commit.

See `cli/rust/README.md`, `cli/bun/README.md`, and `cli/shared/image/bundle/README.md` for build and image injection. Development changes may improve loading, harness compatibility, and developer experience. Contract interpretation and standard-library behavior remain Markdown owned by the separate language repository.
