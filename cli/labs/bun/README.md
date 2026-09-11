# Bun embedded-agent labs

This directory is provider-free research, not a stable CLI surface. It tests four
ways a future Bun implementation could host an agent while keeping the OpenProse
runtime image opaque and outside the task envelope. Nothing here is registered as
a public adapter, reads credentials, or makes a provider/model request.

## Reproduce

Use Bun 1.3.14 or newer (the current OMP package's declared minimum):

```sh
bun install --frozen-lockfile
bun run research
```

`research` type-checks and runs all provider-free tests, compiles the four probes,
then runs each artifact in a fresh directory with only `HOME` and a minimal
`PATH`. The Codex artifact is expected to fail with its documented packaging
blocker; the other three probes must start cleanly. No API key or logged-in agent
session is needed.

The machine-readable evidence, exact versions, integrity hashes, sizes, commands,
and stable-admission gates are in [`scorecard.json`](./scorecard.json).

## Result

| Research ID | Provider-free adapter | Clean artifact | Stable admission |
| --- | --- | --- | --- |
| `research/codex-sdk@0.150.1` | Green, including the real SDK driving a fake Codex JSONL executable | Blocked: the 59.9 MB single file cannot find the platform Codex binary when copied away from `node_modules` | Blocked |
| `research/claude-agent-sdk@0.3.250` | Green with a fake SDK query stream | Green on macOS arm64 as one 269.2 MB file; the SDK extracts its bundled Claude executable from BunFS | Blocked |
| `research/pi-openrouter@pi-coding-agent-0.73.1` | Green with an in-memory session and fake provider boundary | Green on macOS arm64 as a 66.8 MB executable plus a 3.5 KB `package.json` sidecar | Blocked |
| `research/omp-embedded@pi-coding-agent-18.0.8` | Green with isolated settings and a fake provider boundary | Green on macOS arm64 as an 81.3 MB executable plus a 150.7 MB native sidecar | Blocked |

All four plans pass the runtime image and task as separate fields, supply an
explicit working directory and environment, expose cancellation, and fail closed
when the SDK/session stream lacks a terminal event. These are transport findings,
not claims that any candidate already implements OpenProse semantics.

No candidate is benchmark-eligible yet. The shared missing gate is a real
provider-free semantic conformance corpus supplied by the language layer. Further
candidate-specific gates are:

- Codex: the current SDK does not expose strict equivalents for `--ephemeral`,
  `--ignore-user-config`, or `--ignore-rules`; process-tree cancellation and a
  clean platform-binary bundle also need proof.
- Claude: a real authentication/billing path is API/cloud-credential based rather
  than a demonstrated Claude subscription path; process-tree cancellation,
  multi-platform packaging, and the package's non-SPDX legal terms need review.
- Pi/OpenRouter: the coding-agent SDK can be isolated, but a complete subagent
  profile must be explicitly supplied and tested; OpenRouter auth/billing is owned
  by the OpenRouter account. The session API has no per-session environment
  boundary, so an isolated worker/process is still required for benchmark-safe
  environment and child-tool inheritance.
- OMP: the machine used for this probe had Bun 1.3.5, below OMP's declared
  `>=1.3.14`; authentication/billing behavior, native sidecars, and the intended
  subagent semantics still need a supported-runtime trial. It has the same
  in-process environment-boundary problem as Pi.

## Source basis

The Codex attempt follows the current official [Codex SDK documentation](https://developers.openai.com/codex/sdk/)
and the pinned package's own README/source. The Claude attempt follows the
[Claude Agent SDK TypeScript repository](https://github.com/anthropics/claude-agent-sdk-typescript).
The Pi and OMP attempts use their primary repositories:
[pi-mono](https://github.com/badlogic/pi-mono) and
[oh-my-pi](https://github.com/can1357/oh-my-pi). Registry facts were captured
from the exact pinned npm artifacts, not from floating tags.

Historical recovery notes for commit
`5e1d5b49e22379765c2c053c8d94b4f1c6c946af` are in
[`HISTORICAL.md`](./HISTORICAL.md).
