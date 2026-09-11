# Shared runner contracts

This tree contains only outer-runner transport contracts and immutable test
inputs. It is independent of both product implementations and has no
dependency on OpenProse language or kernel packages.

## JSON digest rule

Where a contract names the digest of a JSON value, v1 uses the UTF-8 bytes of
the JSON Canonicalization Scheme (RFC 8785) representation and SHA-256. Test
fixtures intentionally use the I-JSON subset without floating-point values so
the reference checker remains small and unambiguous.

`normalizedEventsSha256` covers every normalized event before the one final
`runner.completed` or `runner.failed` record. Each event contributes its RFC
8785 bytes followed by one LF. Excluding the final record avoids a
self-referential result digest.

Skill Runtime Image payload hashing is a different, byte-preserving operation
defined in `image/README.md`.
