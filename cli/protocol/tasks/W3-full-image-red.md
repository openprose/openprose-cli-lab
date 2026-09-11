# W3 full-image delivery red evidence

The independent product adversary found that the installed-adapter scenarios
delivered only `payload/00-sentinel.md`, even though the verified Skill Runtime
Image contains two ordered payload entries. Shared tests were changed first to
require a manifest-declared model-visible serialization and the exact ordered
346-byte image.

- Red commit: `4846591d16b831ff1b00b00c21e3cea4c29d34e0`.
- Commands:
  - `PYTHONDONTWRITEBYTECODE=1 python3 cli/shared/tests/test_contracts.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/adversarial/adapters/test_adapter_oracle.py`
- Exit: `1` for each command.
- Captured-output SHA-256, in command order:
  - `77f40cc9257a7287a4f226bfceddf6c4e87b7411672245e7f2e318d8096b6baa`
  - `722f888ab9a2578b7251ee09e1b9eb15b36abd837f64625a2915934720e287f`
- Expected failures: missing `modelVisibleBytes`; adapter wire fixtures retain
  obsolete 186-byte image bytes and digests.

The correction is mechanical: concatenate every verified payload entry in
manifest order with no inserted separator. It does not inspect or rewrite the
Markdown bodies.
