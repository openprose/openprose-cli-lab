# W3 prompt-file mode observation red evidence

The independent product adversary found that builder tests asserted private
prompt-file permissions, but the separately owned launch observer recorded
only bytes and paths. The shared oracle was changed first to require the
observer's POSIX mode evidence.

- Red commit: `5d99db927abcfb851fd53eaa464db4975bf00e2f`.
- Command: `PYTHONDONTWRITEBYTECODE=1 python3 cli/conformance/adversarial/adapters/test_adapter_oracle.py`.
- Exit: `1`.
- Captured-output SHA-256:
  `11e381eb376be11986c3a0beb3439158b4a455aa53e2647906a3e643d0963d3d`.
- Expected failure: prompt-file observations have no `mode` field.

Windows ACL evidence remains a platform-specific containment test; a POSIX
numeric mode must not be presented as a Windows security claim.
