# Shared black-box runner cases

Each JSON manifest is a normative outer-runner behavior case. `invocation.argv`
contains arguments after the installed `prose` executable. The conformance
runner supplies a fresh workspace and the deterministic controls named by the
manifest; production users cannot set those controls.

The case corpus may assert exact forwarded task arguments and mechanical
effects. It never parses OpenProse source or defines language semantics.

Paths in manifests are repository-relative. `{{WORKSPACE}}`,
`{{FAKE_HARNESS}}`, `{{SENTINEL_IMAGE}}`, and `{{SENTINEL_TASK}}` are
conformance-runner placeholders, not production environment expansion.
