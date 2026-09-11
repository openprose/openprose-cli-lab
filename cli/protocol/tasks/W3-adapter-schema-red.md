# W3 adapter delivery-schema red evidence

The adapter recipe schema did not yet represent the two closed mechanical
delivery forms required by the frozen installed-harness research: an exact
UTF-8 image argument and an owned rendered-config path. A shared oracle test
was added before changing the schema.

- Red commit: `baf5e45789c532321820c75fd961c720ed444d99`.
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/adversarial/adapters/test_adapter_oracle.py`.
- Exit: `1`.
- Captured-output SHA-256:
  `42c8bcd3c33ba4293ab3ce970c84898e367216601a507e7449de4ddd853e738e`.
- Expected failure: `image-utf8` is not valid under the existing closed
  `argvTemplate` placeholder enumeration.

The test also proves that an invented semantic placeholder remains invalid.
Admission of these two names does not itself admit an adapter: runtime byte
limits, NUL rejection, private-file handling, digest evidence, exact launch
observation, terminal recovery, authentication, and containment remain
separate gates.
