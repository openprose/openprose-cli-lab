# W3 installed-adapter oracle red evidence

The four mandatory installed-process adapters begin with provider-free,
fail-closed oracle cases. The oracle intentionally grants no admission claim.

- Oracle commit: `0f6e8fd2850d8581e6cae02496270e08a7a505d7`.
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/runner/run.py --phase 3 --case adapters.codex-degraded-channel-refused --case adapters.claude-unadmitted-version-refused --case adapters.prime-degraded-channel-refused --case adapters.omp-unadmitted-version-refused`.
- Exit: `1`.
- Captured-output SHA-256:
  `1e693785a3da40c20bc10071d8c336d342346528a4e1d28a2600e1c438953c34`.
- Product gaps: wrong refusal codes/actions in Rust; bare non-result errors
  and wrong exits in Bun.

The oracle also records external strict-admission blockers. Making these four
refusal cases green is necessary but not sufficient for an adapter claim;
exact launch observation, terminal-envelope carriage, prompt/isolation proof,
auth/billing identity, and platform containment remain required.
