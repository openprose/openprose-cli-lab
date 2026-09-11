# Generated package fixtures

`test_benchmark.py` generates package-local-shaped fixtures in fresh temporary
directories. The fixtures include two standalone archives, npm meta/platform
tarballs, release evidence, SBOM, provenance, dependency evidence, and canonical
`SHA256SUMS` membership. Executables are provider-free scripts implementing only
the closed mock result needed by the black-box measurement.

Fixtures are generated instead of checked in so tests can select the executing
host's package platform, create adversarial tar member types, and recalculate
internally consistent evidence after a deliberate mutation. A separate
host-neutral fixture uses `win32-x64` to validate sidecar identity without
executing Windows bytes on another operating system.
