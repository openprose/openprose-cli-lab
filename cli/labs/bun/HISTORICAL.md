# Historical CLI recovery notes

Source inspected: commit `5e1d5b49e22379765c2c053c8d94b4f1c6c946af`,
especially `tools/cli/src/harnesses`, its process runner, installer/packaging tests,
doctor flow, README, and opt-in smoke test.

## Recover

- Injected SDK factory/query seams and dynamic imports, so transport behavior can
  be exercised without a provider call.
- Typed SDK event consumption, separate stdout/stderr evidence, explicit working
  and additional directories, and cancellation forwarding.
- Installer tests for shell syntax, dry runs, unsafe release labels, missing
  runtimes, local tarball installation, runnable shims, checksums, archive roots,
  escaping symlinks, and hardlinks.
- Package assertions for `bin`, `main`, and `exports`, plus an explicit opt-in
  smoke harness with timeouts, temporary state, and credential preflight.

## Reject

- Automatic skill installation/loading and language-specific command or
  `canonicalPrompt` semantics in the CLI. The language-facing prompt belongs to
  the skill/runtime image; adapters transport it opaquely.
- Ambient `process.env`, user/project settings, or implicit credential fallback as
  benchmark inputs. A benchmark invocation must declare its environment and
  configuration.
- Treating an SDK choice as a billing policy. Billing/authentication is a separate,
  recorded adapter property.
- Killing only one immediate child, or flattening cancellation to exit 143 without
  normalized terminal evidence. Descendant containment and settlement remain
  explicit conformance gates.
- Treating EOF or a non-terminal item/message as success. Codex now requires
  `turn.completed`/`turn.failed`; Claude requires a terminal `result`; the Pi
  variants require `agent_end`.
- Unbounded stream forwarding without backpressure or lifecycle ownership. The old
  simple stream behavior is useful test material, not a stable protocol contract.

The historical code is therefore an implementation reference for seams, events,
packaging, and hostile installer cases—not the semantic or lifecycle authority for
the new VM surface.
