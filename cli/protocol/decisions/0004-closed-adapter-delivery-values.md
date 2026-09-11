# Decision 0004: closed adapter delivery values

Status: accepted for transport research

The admission-recipe argument grammar includes two additional mechanical
values:

- `image-utf8` expands to the digest-verified image bytes decoded as UTF-8 in
  exactly one argument; and
- `rendered-config-path` expands to an owned private file produced from a
  closed harness template and verified inputs.

Neither value permits adapter-authored instructions or inspection of
OpenProse source. Implementations reject invalid UTF-8, NUL, configured byte
or platform argument limits, and use `IMAGE_TOO_LARGE` instead of changing
placement. Generated files live in an owned mode-0700 directory, are mode
0600, contain no credential, and remain until the child no longer needs them.

Evidence records source-image, generated-config (when present), and decoded
model-visible delivery digests separately. Diagnostics and argv evidence
redact payload bodies. The schema names make a delivery form representable;
they do not grant an adapter claim. Every recipe still requires exact launch,
precedence, isolation, terminal, authentication/billing, and containment
admission.

Codex's current `-c key=value` interface additionally requires an exact,
audited configuration-value renderer. This decision does not pretend that a
raw `image-utf8` argument satisfies that interface, so Codex remains on its
fail-closed recipe until the representation and canary are frozen.
