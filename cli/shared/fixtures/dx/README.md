# Shared runner DX fixtures

These JSON documents are exact, product-neutral authorities for stable
machine-facing dry-run behavior. `{{WORKSPACE}}` is replaced only with the
canonical per-candidate workspace allocated by the conformance runner.

Configuration provenance is ordered as `cwd` followed by the eight runner
configuration values. `authProfile` is redacted even when its value is absent;
the record reports provenance, not credential material. The default hosted
selection is `openprose/hosted` over `hosted`, and the explicit deterministic
mock has auth readiness `not-applicable`.

Human mock/error text is not frozen here. The current products share error
codes, messages, and actions but do not yet have one authoritative rendering
format. Machine output is the stable agent surface until that formatting
decision is made explicitly.
