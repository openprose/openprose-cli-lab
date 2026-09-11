# Installed-process adapter admission oracle

This directory freezes the independently researched launch facts for the four
mandatory installed-process adapters. The JSON recipes validate against
`adapter-admission-recipe.schema.json`; `oracle.v1.json` carries evidence,
environment, and blocker details that the closed recipe schema cannot express.
That oracle is also the authority for exact adapter-owned child controls. Recipe
schema v1 has no environment-control slot, so Prime's observed
`PRIME_AGENT_TELEMETRY=0` run-only opt-out is deliberately bound in the oracle
and product parity tests without overloading unrelated recipe fields.

An empty `admissionClaims` array is deliberate. A documented feature is not an
earned OpenProse transport claim. Builders must satisfy the scenario and
adversarial fixtures before changing a claim. Strict semantic admission remains
blocked, but the closed functional-alpha profile may execute the four
version-admitted recipes with `echo-v0`; it keeps `admissionClaims` empty and
never emits a strict-wrapper claim.

The oracle never invokes a real provider. Each evidence record names its own
date and scope. In particular, the 2026-08-28 OMP audit inspected the ordinary
18.0.9 package, source, `--version`, and `--help` behavior under Bun 1.3.14; it
made no model call.

OMP 18.0.9 has one structured runtime prerequisite in both its frozen recipe
and the functional-alpha authority:

```json
{"runtime":"bun","versionRange":">=1.3.14","repairCommand":"npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9"}
```

Packaging, release-note rendering, and provider-free alpha admission fail
closed if either authority or the generated guidance drifts.

The OMP functional-alpha launch adds exact `--no-tools` and `--no-lsp`, strips ambient
`PI_CONFIG_FILES`, and passes a final mode-0600 wrapper-owned `--config`
overlay containing `retry.enabled: false` plus the closed OMP 18.0.9 set of
MCP-capable discovery providers in `disabledProviders`. After OMP's ready/command
inventory barrier, the wrapper sends one correlated `get_state` request and
delivers the prompt only after `data.dumpTools` proves an empty array. A false
`agent_end.isTerminal` is valid upstream continuation state but intentionally
unsupported here: it fails with the fixed
`unsupported_nonterminal_settlement` reason and never settles as success.
OMP's setting is global rather than MCP-scoped, so this exact provider list also
disables Cursor-backed OMP models. Cursor is not an admitted functional-alpha
credential route; the correlated state proof remains the authority against
future upstream provider drift.

The 2026-08-30 exact Prime 0.8.1 installed-source audit also binds the
provider-free text-only RPC branch: without a preceding thinking block, text
starts and remains at content index zero. Functional alpha admits that branch
as optional thinking followed by one required nonempty text block; it still
rejects thinking-only settlement, tools, multiple blocks, and post-text
reasoning. This source fact does not widen strict admission.
