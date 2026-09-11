# Shared runner schemas

These Draft 2020-12 JSON Schemas are the normative data contracts for the
outer OpenProse runners. They deliberately do not define OpenProse source,
task-envelope semantics, or the language-owned terminal envelope.

All objects are closed unless a field is explicitly documented as opaque.
Opaque values are transported and hashed, never interpreted by the runner.
Schema identifiers are stable HTTPS identifiers; local file names are the
canonical repository copies.

Run the contract checks from the repository root with:

```sh
python3 cli/shared/tests/test_contracts.py
```

The test dependency is pinned in `../requirements-test.txt`.
