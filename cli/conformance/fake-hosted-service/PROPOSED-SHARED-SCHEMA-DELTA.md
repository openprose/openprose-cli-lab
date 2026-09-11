# Proposed shared-schema delta (not applied)

This proposal records what the scripted hosted seam exposed. It does not choose
placement or authorize the default adapter.

## Case controls

Either extend `openprose.runner-case/1` with a closed
`controls.fakeHostedService` object, or teach the differential runner to consume
the separate `openprose.hosted-oracle-case/1` corpus. The control needs only a
scenario ID, placement candidate, deterministic service executable, lifecycle
stimuli, stream-credit plan, and local capability fixture. It must not contain
an endpoint, provider, model, credential, price, or semantic expectation.

Add placeholders only for owned fixture paths, for example
`{{FAKE_HOSTED_SERVICE}}` and `{{HOSTED_REQUEST}}`. They are conformance-runner
substitutions, never production environment expansion.

## Hosted wire and evidence

After the placement decision, promote a production-owned successor to the local
`openprose.hosted-wire/1` shape with:

- separately digested opaque image/task channels;
- request, invocation, run, sequence, and capability correlation;
- idempotency fingerprint and replay behavior;
- byte/frame/queue/credit limits;
- disconnect/cancel/timeout settlement and a mandatory terminal;
- closed auth category and billing owner, without credential material in the
  invocation record;
- authoritative-or-unavailable usage/cost; and
- default-deny, path-granted local capability requests if the hosted-agent
  placement wins.

The scripted bridge proves bounded stdout/stderr capture and direct-child
reaping only. Its evidence intentionally reports process filesystem sandboxing
and descendant containment as `unsupported`; a production hosted-agent bridge
must add platform containment evidence rather than inheriting a stronger claim
from this fixture.

The service and bridge evidence shapes should also be promoted only after their
authority is assigned. The fake labels `none-test-only`,
`fixture-authoritative`, and `test-fixture-only` must not enter a production API.
The existing shared `runner-result` already has authoritative/unavailable usage,
but a hosted implementation may need a sanitized `evidenceRef` to bind the
authoritative statement to service evidence without embedding account data.

Map fake `assistant.delta`, usage, capability, and terminal records into the
existing normalized-event vocabulary rather than exposing this research wire as
the public runner output.

## Errors still needing a decision

The three hosted admission mappings already exist in the shared taxonomy and do
not need new values. `LOCAL_CAPABILITY_REJECTED` is deliberately research-only:
if the hosted-agent placement wins, decide whether its public successor is a new
stable error or a precise mapping to an existing boundary. Likewise, the
existing `STARTUP_TIMEOUT` name does not clearly cover an already-running hosted
deadline; do not silently broaden it without a taxonomy decision.

EOF without `run.terminal` remains `PROTOCOL_TRUNCATED`. Mechanically complete
transport with no language-owned terminal envelope remains
`SEMANTIC_STATUS_UNKNOWN`/23.

## External gates

Do not add production endpoint formats, login/device flows, token scopes,
retention/region fields, quota/spend semantics, pricing, canonical image bytes,
or semantic-terminal fields from this fixture. Those are explicitly external
Phase-5 decisions. Until they land with versioned authorities, the stable
`openprose` selection continues to return `HOSTED_UNAVAILABLE` and never falls
back.
