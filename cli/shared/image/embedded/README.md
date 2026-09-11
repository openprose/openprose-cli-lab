# Staged embedded image

`current.bundle.bin` is the single data asset embedded by both development
products. It is generated from `../echo-v0` by the stdlib-only bundle tool;
`current.bundle.sha256` covers its exact bytes. Product builds fail on drift.

The currently staged image is a user-authorized, release-eligible public-alpha
echo placeholder. It proves installed-harness launch, opaque image/task
delivery, and terminal-envelope recovery only. Its semantic status is
`not-applicable`: it does not execute OpenProse and must be replaced by a
language-owned image without changing adapter semantics. Passing
`--require-release-eligible` remains necessary structural evidence, not a
semantic, benchmark, or general-availability release claim.
