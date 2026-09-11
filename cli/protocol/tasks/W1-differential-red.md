# W1 differential red evidence

The lead-owned black-box runner was introduced after both independent walking
skeletons passed their own suites. It immediately exposed cross-product drift
that product-local tests could not see.

- Oracle commit: `6be7c54b923e724c28ad1eb6b8ab245b5e73630c`.
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/runner/run.py --phase 1 --case core.mock-success --case core.openprose-hosted-unavailable`.
- Exit: `1`.
- Captured-output SHA-256:
  `66711227e2fa1047676a8a21c1100013437d6cab219c28072a6c8436d785175d`.
- Failures: deterministic-mock descriptor/version/cancellation drift and
  OpenProse-hosted descriptor/error-detail drift.

The shared deterministic mock descriptor was then frozen at
`cli/shared/fixtures/transport/deterministic-mock-adapter.json`, with JCS
SHA-256 `65849560c167c1ea98d8e6b46c7703b6ceb4b18dc2564c48de5cbba099576c4a`.
Both products must consume those facts, and the full Phase-1 corpus must be
differentially clean, before W1 is admitted.
