# Scripted hosted transport fixtures

These fixtures are provider-free inputs for the Phase-5 hosted oracle. They are
not production endpoints, credentials, pricing, quota, or semantic examples.
The runtime image and task are deliberately separate byte claims. Their payloads
are opaque to the oracle and exist only to prove byte preservation.

The `none-test-only` authentication category carries no secret. The
`fixture-authoritative` usage label means authoritative only inside the fake
scenario; it makes no product billing claim.
