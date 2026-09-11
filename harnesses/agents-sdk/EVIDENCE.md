# Agents SDK generic harness — initial evidence

2026-09-10. Runner implementation: `worktrees/agents-sdk/harnesses/agents-sdk`. Raw observations, copied inputs, and results: `runs/agents-sdk-v0/`. Language snapshot: `03d0538a88076ed7a0f666131ba7432d2338a8c8`.

## Profile

OpenAI Agents SDK 0.22.2 + OpenAI Python 3.13.0. Generic helpful coding-agent instructions, one ordinary local bash function tool, cwd, opaque optional instructions, user prompt. Agent+Runner provides native model/tool loop. No language parser, automatic library loading, semantic tools, reviewer, acceptance guardrail, or corrective prompt. Inputs contain no API keys. Key loaded only in parent harness process and excluded from tool environment. Workspaces separate copied directories, **not OS confinement**. No native subagent or web tool in this profile.

## Results

| Task | Model | Time | Input/output tokens | Observed result |
|---|---|---:|---:|---|
| File copy canary | gpt-5.4-mini | 3.888 s | 302 / 34 | Exact bytes, pass |
| File copy canary | gpt-5.5 | 3.182 s | 314 / 46 | Exact bytes, pass |
| Source-grounded brief | gpt-5.4-mini | 6.576 s | 5,463 / 578 | Correct facts/scope, incorrect line references; strict fail |
| Source-grounded brief | gpt-5.5 | 15.111 s | 7,196 / 817 | Correct facts, verified lines and only requested file added; pass |

Brief prompt identical: `Read README.md and execute examples/brief/program.md.` No instructions file injected for these runs; the model read the interpreter using shell itself. Both loaded the adopted conventions. The smaller model cited notes.md lines 1–4 although claims are on lines 3–6. It reproduced exact quotations elsewhere, but its invented line references still violate the adopted source-grounding convention. The larger model ran `nl -ba` and cited the actual lines. Both models reported fulfillment; outcome text is not an acceptance oracle.

A post-run byte comparison found no changed original snapshot files. Only `examples/brief/result.md` was added in each brief workspace. `verification.json` contains artifact hashes and canary comparisons. No evaluator repaired the artifacts.

Two local harness tests passed: actual shell read/write and nonzero exit result; timeout process-group termination prevents a delayed background write. SDK default transport retries may still occur; wrapper itself adds no semantic retries.

## Interpretation

This establishes a third working generic harness family and a useful cross-model discrepancy. It does not establish broad language conformance or production isolation. One run per model/task; no statistical reliability claim. Pin model snapshots and repeat on holdout tasks before comparing long-run rates. The mini citation failure should feed library/interpreter evaluation, not a harness-specific fact-checker or repair step.

SDK implementation reference: https://developers.openai.com/cookbook/examples/agents_sdk/migrate-from-claude-agent-sdk/readme . Installed signatures were checked directly. API model listing confirmed both selected model IDs are available to the configured account.
