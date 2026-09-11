# Native tool lifecycle integration candidate

Prime 0.7 and OMP 18.0.9 can read/write files through their ordinary tools. The prior adapters admitted only a no-tool lifecycle; OMP additionally forced tools off both in the shared recipe and Rust's independent launch construction.

This change admits observed native tool cycles in both independent implementations. Tool calls are correlated by native identity/name, execution arguments, ordered start/update/end, and returned content/error status. Each called tool must report its result before turn_end. A subsequent turn is allowed after toolUse; terminal settlement requires the actual final agent_end and process exit, not a tool result. OMP's isTerminal flag remains required. Missing acknowledgement, unknown/misordered calls, inconsistent result content, premature terminal, and EOF remain failures.

The existing strict no-tool paths remain for their original streams. Prime switches to the native lifecycle for tool-call content or the observed indexed content metadata. OMP uses its real nonempty registered tool inventory to select the tool-capable path. OMP preserves its owned configuration overlay and state barrier, but no longer requires zero tools. Rust retains only tool names from the potentially sensitive state response. Nothing interprets Contracts, models, programs, or business outcomes.

Two observed metadata differences are handled explicitly: OMP moves its generated `i` intent argument out of executable arguments, and omits completedAt from subsequent assistant snapshots. Identity and substantive content still match. Prime/Omp use different native terminal metadata; neither is silently substituted for the other.

## Evidence

Shared JSON fixtures under `shared/fixtures/adapters/tool-lifecycle/` derive from real RPC Haiku read/write traces. The OMP state response was reduced to tool names and request IDs changed to a fixed test invocation; the assistant/tool lifecycle is retained. Tests include real completion, missing terminal, unmatched completion, and invented tool-result content. These are mechanical transport fixtures, not OpenProse semantics.

Explicit live canary script: `run_local.py --output ABSOLUTE_FRESH_DIRECTORY`, optionally `--products rust --harnesses omp`. It uses this worktree's built binaries, private installed OMP/Bun dependencies, and provider keys only in child environments. It is an opt-in local expedition script, not a hermetic unit test. It does not repair outputs or inject completion messages.

The initial eight-cell matrix is retained externally in the private lab's `runs/adapter-tools-v0/`. Both products with Prime/OpenAI and Bun with OMP/both providers created correct files and exited zero. Prime/Haiku created correct files but omitted the image's final envelope and was rejected; no transport rule was weakened to conceal that. Initial Rust/OMP binaries still forced tools off through Rust's separate launch vector, discovered by the no-file outcome despite a transport-success report. That construction is now corrected and specifically retested in `runs/adapter-tools-v1/`.

Runner completion and an actual file are evaluated separately. A successful process with no requested artifact is not a passing file canary. The CLI owner is separately replacing the legacy language-produced envelope requirement with native-output mode; that work is outside this candidate.

The corrected Rust/OMP retest created correct code/net-quantity artifacts for both providers. OpenAI exited zero; Haiku again hit only the final image-envelope requirement after writing the artifact. Across the retained repaired cells, every product/harness/provider combination has actual correct file evidence. This is not an all-zero-exit matrix: Haiku's envelope failures are preserved for the separate native-output work.

Validation before integration: Bun typecheck and 167 focused adapter/protocol tests; Rust 118 runner-core tests; 22 shared contract/schema tests; 16 adversarial adapter-oracle tests. The earlier full Bun run had three expected recipe/launch mismatches; after synchronizing exact recipe digests and scenario argv, the affected launch suite is included in the passing 167 tests.
