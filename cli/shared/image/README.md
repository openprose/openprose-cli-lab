# Skill Runtime Image packaging contract

The runner consumes an image manifest and treats every payload body as opaque
bytes. It may verify paths, sizes, versions, permitted placements, and hashes;
it must not inspect payload text to choose behavior.

For image format v1, `aggregateSha256` uses
`sha256-path-length-nul-v1`. For each payload entry, in manifest array order,
the hasher receives:

```text
UTF8(path) || NUL || UTF8(decimal byteLength) || NUL || rawBytes || NUL
```

Only `payload` entries contribute to that aggregate. The external task,
terminal, and one-field framing artifacts each carry an independent digest.
Paths are slash-separated, relative, normalized paths. Payloads are UTF-8 with
LF newlines and no byte-order mark.

`modelVisibleBytes.serialization` defines the exact bytes used when an
adapter exposes one instruction value. `ordered-raw-concatenation-v1` appends
each already-verified payload body in manifest order and inserts no separator,
header, path, or other bytes. The declared byte length and SHA-256 are verified
before launch. This serialization is mechanical and does not inspect Markdown.

`sentinel-v1` is deterministic transport-test material. Its manifest is
permanently `releaseEligible: false`; no public artifact may contain it.
