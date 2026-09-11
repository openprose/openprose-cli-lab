# W1 shared-oracle red evidence

The shared black-box cases are the admission authority for the two walking
skeletons. The red integration commit contains the schemas, fixtures, sentinel
image, fake harness, and case manifests but no Rust or Bun product tree.

- Red commit: `ed2458c9585f64b7deed0df4c8ce07f6a926f1fd`.
- Red tree: `ef5aac7d2355e43c879450a5391b4ef6749b206d`.
- Expected failure: neither requested product executable exists.
- Replay command: `python3 cli/conformance/runner/run.py --build` once the
  lead-owned differential runner is added; before that runner exists, the
  absence is demonstrated by checking both documented binary paths.
- Rust binary path: `cli/rust/target/debug/prose`.
- Bun binary path: `cli/bun/dist/prose`.
- Red assertion: `test -n "$(git ls-tree -r --name-only <red> cli/rust cli/bun)"`.
- Red exit: `1`.
- Red output digest: SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
  (the authoritative product-path listing is empty).
- Oracle validation: `PYTHONDONTWRITEBYTECODE=1 python3 cli/shared/tests/test_contracts.py`.
- Fake-harness validation: `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/fake-harness/test_fake_harness.py`.

The product implementations remained untracked in the working tree during the
red commit and enter repository history only in the following green commit.
