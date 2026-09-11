# W3 RPC correlation red evidence

The product adversary found that the provider-free RPC probe acknowledged a
hard-coded fixture ID. A correct runner must require response correlation to
the actual prompt request rather than weakening validation for the fixture.

- Red commit: `3734d968f3cb4b4c2718a6777e75af75d56462db`.
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/adversarial/adapters/test_adapter_oracle.py`.
- Exit: `1`.
- Captured-output SHA-256:
  `b5cf90f18cd0f0874ab7d16f8957298c514647165598c8e234272413d59aee10`.
- Expected failure: the probe returns `fixture-invocation-0001` for a request
  carrying `dynamic-invocation-7f6b`.

The probe remains provider-free and preserves the fixed shared scenario when
the fixed request is supplied.
