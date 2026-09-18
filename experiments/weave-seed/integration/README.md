# Explicit file and process integration

This is an unpublished Bun-only integration experiment. It connects the bounded loop to the local checkpoint host and synchronous child processes. It does not add a shipped `prose weave` command, provide a Rust process adapter, or automatically resolve OpenProse contracts.

`binding.mjs` reads an explicitly selected kernel file, contract files and evidence files. Their exact bytes participate in the evidence identity. Changes to a contract, kernel, report or other selected evidence invalidate reuse. Reads are bounded and require regular UTF-8 files inside the selected trusted root. Missing or invalid evidence produces a gap. Multiple files are read sequentially; this is not an atomic filesystem snapshot. Dependencies omitted from the selection remain invisible.

`process.mjs` starts explicit argument arrays without a shell. Each child receives one JSON object on standard input with schema `openprose.weave-input/1`, the evidence envelope, and the actor attempt ID where applicable. The evidence payload contains the explicitly bound source contents. Assessor output must be exactly one JSON field named `judgment`, with value `satisfied`, `work-needed`, or `unknown`. Extra fields, duplicate keys, escaped alternative spellings and extra output are rejected. Actor exit status zero requests fresh assessment; it is not proof of fulfillment. Nonzero status or timeout leaves uncertain effects pending.

The environment defaults to empty; supply required credentials and environment explicitly in memory when using the capability API. Do not commit secrets in JSON configuration. Timeouts send SIGKILL to the direct child. This is not process-tree supervision: detached descendants may remain and external effects may already have occurred. Output is bounded, but no operating-system sandbox or provider spending cap is supplied. Use only trusted commands. Executable and dependency immutability is a host requirement; changing capability code requires updating `capabilityVersion`.

## Provider-free checks

From the repository root:

```sh
bun --no-env-file experiments/weave-seed/integration/binding.test.mjs
bun --no-env-file experiments/weave-seed/integration/process.test.mjs
bun --no-env-file experiments/weave-seed/integration/run.test.mjs
```

These tests create disposable files and actual child processes. The process test retains pending state after a child writes an effect and exits unsuccessfully. The integration test uses synthetic kernel text and deterministic assessment, then demonstrates that a new batch report remains required even when the maintained state is already correct. It also verifies reuse and action-budget exhaustion across reopened hosts. It does not execute or validate the actual OpenProse kernel.

## One explicitly configured step

`run.mjs` accepts a trusted JSON configuration file:

```json
{
  "schema": 1,
  "root": ".",
  "kernel": "kernel.md",
  "contracts": ["program.md"],
  "evidence": ["state.json", "current-batch.json", "report.md"],
  "capabilityVersion": "my-host-policy-v1",
  "assessor": ["/absolute/path/to/assessor", "--protocol", "weave-input-v1"],
  "actor": ["/absolute/path/to/actor", "--protocol", "weave-input-v1"],
  "checkpointDirectory": ".weave-host",
  "maxAttempts": 2,
  "timeoutMs": 30000
}
```

The executable paths above are placeholders, not bundled services. Root and checkpoint-directory paths resolve from the configuration file. Source paths resolve from root. Run one step with `bun --no-env-file experiments/weave-seed/integration/run.mjs CONFIG.json`. The process prints only status, cumulative attempts and the pending attempt ID. Configuration is trusted execution policy, not untrusted task data. Repeated calls do not replenish the budget.

An actual OpenProse adapter must resolve and supply the effective agreement, preserve adopted definitions and required invocation outputs, and ensure the executor uses the selected kernel. Binding a kernel file here does not force an unrelated child to adopt it. The existing CLI's verified kernel startup and native harness remain the execution route; an adapter between these protocols still requires integration and live validation. Do not point the example actor at an arbitrary native CLI and assume its standard-input protocol matches.

## Cross-runtime checkpoint compatibility

Build the Rust host as described in `../rust-host/README.md`, then pass its absolute binary path to:

```sh
bun --no-env-file experiments/weave-seed/integration/interchange.test.mjs /absolute/path/to/weave-local-host-experiment
```

The check exchanges 100 checkpoints in both directions and rejects malformed shared inputs. These are source-level local tests, not installed-package or distributed-storage qualification.
