# Embedded Skill Runtime Image bundle v1

The embedded bundle is a deterministic staging envelope. It does not add
language semantics and does not replace the image-format-v1 manifest as the
authority for paths, order, sizes, normalization, or digests.

All integers are unsigned, big-endian, and minimally represented by their
fixed width:

```text
"OPENPROSE-IMAGE-BUNDLE" NUL 0x01
u32 manifest_byte_length
manifest_bytes
u32 file_count
repeat file_count times:
  u32 utf8_path_byte_length
  utf8_path_bytes
  u64 file_byte_length
  file_bytes
```

`manifest_bytes` are the exact normalized bytes of `manifest.json`. File
records appear in this one canonical order:

1. every payload path in manifest array order;
2. `taskEnvelope.path`;
3. `oneFieldFraming.path`;
4. `terminalEnvelope.path`.

There are no separators, compression, timestamps, source paths, padding, or
runner-owned prompt text. The entire bundle therefore has reproducible bytes
for identical image directory bytes. `current.bundle.sha256` is lowercase
SHA-256 followed by LF and covers `current.bundle.bin` exactly.

Every consumer must reject malformed framing, trailing bytes, unsafe or
duplicate paths, undeclared/missing files, invalid UTF-8, CR, BOM, NUL, size
overflow, manifest drift, and digest drift before exposing bytes to an
adapter. `--require-release-eligible` additionally requires
`releaseEligible: true` and rejects the sentinel purpose.
