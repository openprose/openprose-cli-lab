# 0007: Explicit harness credential routes remain distinct

Status: accepted for functional-alpha implementation

## Context

Prime and OMP can obtain credentials from both their own persistent stores and
provider environment variables. Merely passing an environment key does not
prove that the harness will prefer it over a cached login. Conversely, hiding
the harness store would break the deliberately convenient bring-your-own-login
route. A local readiness command cannot prove which account will ultimately be
charged without making a provider request.

## Decision

- Prime and OMP never receive an implicit authentication profile. The user
  selects either an explicit harness-login profile or one explicit provider
  environment profile.
- A harness-login profile retains the harness's ordinary store and strips all
  provider credential variables. The wrapper does not inspect the store,
  infer an account, or claim a billing identity.
- A provider environment profile retains only its declared credential group
  and gives the harness a fresh private per-run configuration directory. This
  prevents a cached harness login from silently outranking the selected
  environment route. Ambient config-directory overrides are never inherited.
- The private directory lives under the runner-owned transport directory, is
  mode 0700 on POSIX, remains alive for the complete child/service lifetime,
  and is removed only after settlement. Cleanup failure follows the ordinary
  fail-closed owned-resource policy.
- Inventory and `doctor` may validate executable identity, exact admitted
  version, platform support, profile membership, and required non-empty
  environment variables. They do not run Prime's model inventory or OMP's help
  command as an authentication oracle and report authentication readiness as
  `unknown` for both cached-login and environment-key routes.
- The actual harness run remains the first authority for whether the selected
  route can authenticate. The wrapper never retries with another profile,
  restores a cached credential store, or changes provider/model after failure.
- Provider-owned file references deliberately selected by a profile (for
  example an AWS profile or Google application-credentials path) remain
  harness/provider inputs. This isolation does not prove which upstream
  account was charged.

Codex and Claude retain their separately frozen authentication behavior. This
decision does not turn adapter code into a credential manager and does not add
language semantics to the CLI.

## Consequences

The convenient cached-login path and the deterministic environment-key path
remain available, but their evidence is intentionally different. Tests must
show that ambient store overrides are stripped, harness-login profiles keep
their ordinary HOME-backed store, environment profiles receive a fresh exact
directory, and no readiness probe or fallback process is launched for
Prime/OMP.
