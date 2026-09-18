# Weave candidate readiness

Status: unpublished experiment, September 18, 2026. This is a readiness assessment, not a v1 release declaration. The candidate adds independent loop seeds and onboarding beside the Python reference; it does not add a public CLI command or package.

## Source and evidence boundary

The Python reference baseline is `22c14915cad5ab6eadb5f70b7c7177b89e6abe5b`. The extended Rust/Bun candidate is local commit `979e785417b593d4961559386445afac79e1cfca`; all 12 commands in the final source-level validation passed against that clean revision. The inspected kernel is `7dc90670b4ccd862b7d0939a75d8b819b03a2b1b` in its separately owned source repository. Kernel and contract semantics remain authoritative; the seeds do not parse or automatically bind Markdown.

The candidate now includes inspected upstream CLI main `332c511` through local merge `ba55296`. The ownership-record conflict was resolved by preserving both sets of leases. Twelve upstream upload-control tests and five assembly tests pass; these use fixtures and mocks, not actual uploads. New seed code remains isolated from the shipped CLI, so this does not qualify a new installed release.

## Checks actually performed

From the CLI repository root, the onboarding review ran:

```sh
python3 experiments/weave/demo.py
python3 -m unittest discover -s experiments/weave -p 'test_*.py' -q
```

With Python 3.9.6 on macOS, the demonstration produced the documented five statuses, four assessments, and one action; all 48 reference tests passed. The example uses temporary local state, synthetic data, and deterministic assessment/action. No provider was called. This does not establish semantic classifier reliability, source authenticity, or production service safety.

The onboarding review also executed the two seed examples on actual Bun 1.3.5 and Rust 1.98.1 (`48a229cea`, September 1, 2026), with each runtime executable selected explicitly because neither was on the shell's default `PATH`. The portable equivalents below assume those same installed runtimes on `PATH`:

```sh
bun experiments/weave-seed/examples/local.mjs
WEAVE_EXAMPLE_DIR=$(mktemp -d)
rustc --edition=2021 experiments/weave-seed/examples/local.rs -o "$WEAVE_EXAMPLE_DIR/local"
"$WEAVE_EXAMPLE_DIR/local"
```

Both examples passed assertions for `satisfied`, then `reused`, with exactly one action. Their host saves are in memory, and they do not establish persistence or crash recovery. A Node run alone must not be described as a Bun runtime check.

The final seed checks were also run with the same actual Bun and Rust versions:

```sh
bun experiments/weave-seed/bun/conformance.mjs
WEAVE_TEST_DIR=$(mktemp -d)
rustc --edition=2021 --test experiments/weave-seed/rust/lib.rs -o "$WEAVE_TEST_DIR/tests"
"$WEAVE_TEST_DIR/tests"
```

Both implementations passed the 25 shared lifecycle cases. Bun additionally passed four save-failure cases, settlement acceptance/rejection, checkpoint immutability and settlement validation, mutable-observer snapshot checks, and synchronous-callback enforcement. Rust reported three passing test functions covering the shared corpus, save failures, and settlement. These are source-level deterministic checks on macOS ARM64, not installed-package, cross-platform, or live-model qualification. The documentation review resolved 27 local Markdown links without missing targets.

## Gates still required for v1

| Area | Required evidence or decision |
|---|---|
| Semantic assessment | Independent labels and adversarial evaluation of complete real agreements, including source authority, missing evidence, reporting duties, and abstention. Deterministic fixtures do not establish model accuracy or calibration. |
| Complete invocation | An end-to-end integration must preserve per-invocation reports and required verification even when maintained state is unchanged. Artifact existence does not prove a claimed read occurred. |
| Host persistence and recovery | Specify and test durable storage, serialization, interruption, external-effect reconciliation, receipts, and repeated recovery. A callback loop alone is not a production host. |
| Permissions and costs | Define enforceable effect permissions, credential ownership, scheduling, attempt limits, timeouts, and provider-specific budgets. Contract declarations alone cannot enforce these. |
| Integration and compatibility | Verify an explicit kernel/evidence binding and the agreed output convention without introducing a prose parser or replacing the existing generative runner. |
| Runtime and distribution | Complete target-platform and runtime qualification, API review, packaging, upgrade behavior, dependency/security review, and release authorization. A source-level test is not an installed-package test. |
| Onboarding | Run a fresh developer/agent trial of the instructions and the kernel-backed example; independently inspect its artifacts and reported operations. The new prose example has not had a live model run in this phase. |
| Feedback | Establish maintainer ownership and triage for manually submitted reports; validate the synthetic reproduction workflow. No automatic evidence collection or upload is introduced. |
| Kernel origin | Plan any future `openprose/prose` origin migration separately, preserving exact identities, links, package selections, and compatibility. No live URL or consumer pin changes occur here. |

No publication, version bump, merge into the release branch, or origin migration follows from this assessment. The [seed overview](../experiments/weave-seed/README.md), [contribution guide](../experiments/weave-seed/CONTRIBUTING.md), and [feedback template](../experiments/weave-seed/FEEDBACK.md) are the entry points for bounded continuation.

Final port hardening: 10,000 sequential transitions passed in each implementation, including exactly 2,000 bounded actions per port, changing bindings, expiry, reuse, and budget exhaustion. These use deterministic callbacks and do not establish model accuracy, crash durability, or installed-package readiness.

## Extended local-host evidence

The extended candidate adds Rust and Bun local checkpoint hosts. Bun passes 13 host checks; Rust passes 17 tests including its embedded core tests, plus one helper invoked by a subprocess test. Checks cover competing processes, abrupt exit, pending-action restart, explicit settlement, malformed state and uncertain checkpoint publication. Cross-runtime testing passes 100 bidirectional checkpoint round trips and 19 shared malformed-state rejections. These close a bounded local-host implementation gap, not production or power-loss qualification.

A Bun-only file/process bridge now hashes explicitly selected kernel, contract and evidence bytes, and invokes bounded synchronous child commands. Its deterministic integration fixture preserves new batch-report obligations despite already-correct maintained state. The fixture uses synthetic kernel text. It does not validate the actual OpenProse kernel or supply an automatic adapter for the existing native CLI. The live integration and semantic-evaluation gates above remain open.


## Live integration findings — September 18, 2026

A subsequent isolated lab campaign exercised this source-level loop through the real Bun CLI and real Rust CLI, with the pinned kernel above, Jev `jev-1.13.0`, and an Agents SDK agent using `gpt-5.6-luna`. Each host completed a five-event sequence: initial repair, unchanged reuse, changed-source repair, missing-source stop, and a new invocation report while preserving unchanged maintained state. These are two finite synthetic sequences, not broad agreement conformance or semantic accuracy evidence.

The original shell-capable harness produced correct artifacts but made prohibited Git calls in two of six native actions. Artifact-only classifier assessment did not observe those operations; subsequent trace-disclosure and question-narrowing probes did not reliably identify the violations. These failures remain part of the evidence.

A separate laboratory profile then repeated both sequences with only scoped file reads, writes to two declared outputs, and JSON comparison tools. All ten event checks passed. Independent trace review is retained in the lab. This profile removes model-accessible shell/process tools; it is a capability restriction in a trusted harness, not an operating-system sandbox. Its fixed local Python environment is not a portable installed distribution.

The integration gate therefore has bounded live evidence. The productization gate remains open: package and select the profile explicitly, represent enforced capabilities in receipts, make evidence limitations visible in status, and test installed artifacts. Classifier satisfaction alone must not be presented as proof of required operations or permission compliance. The native CLI binaries tested here identify source `68297eff23990a5153e5d0a938febf68c3c4751d`; they are not freshly built releases of the experimental branch.

The maintained lab record is IMP-017, `docs/LIVE-LOOP-READOUT.md`, with the corrective implementation frozen at lab commit `d0a30ff`. The Rust live host uses the Rust loop and CLI but delegates file observation to Bun. No Bun-independent Rust integration, public CLI command, v1 release, or repository migration is established by these results.

A post-live offline check on Python 3.14.6 exposed unclosed SQLite connections in the Python reference observer and test fixtures. The observer now closes read-only connections after both successful queries and evidence gaps. All 49 reference tests pass, including a regression checking closure on success and SQL failure; all 77 lab tests and 13 file-harness checks pass. This resource-lifecycle repair does not change the frozen live Rust/Bun implementations or reinterpret their results.
