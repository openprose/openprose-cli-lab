# Decision 0001: local toolchains and provisional platforms

Status: accepted for local development

The independent CLI workspaces pin Rust 1.87.0 and Bun 1.3.5. The npm launcher
targets Node 20 or newer. Dependency lockfiles live only inside their product
trees.

The provisional Tier-1 release matrix is macOS arm64/x64, Linux x64/arm64
glibc, and Windows x64. Linux musl and Windows arm64 remain optional. Local
development on macOS arm64 does not constitute cross-platform admission;
platform-specific containment and packaging remain gated on their matching
test lanes.
