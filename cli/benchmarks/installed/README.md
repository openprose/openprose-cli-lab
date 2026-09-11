# Installed-package benchmark

This directory measures the packages emitted by `cli/ci/package_local.py`. It
does not rebuild either CLI and does not read mutable development binaries.
The benchmark is provider-free: every measured invocation selects the explicit
deterministic mock and forwards the same opaque task arguments.

## Run

The package directory must already exist. The install root must not exist; its
parent must exist and the tool creates it as an owned, disposable directory.
The tool never removes or reuses that directory.

```text
python3 cli/benchmarks/installed/benchmark.py run \
  --packages /absolute/path/to/package-local-output \
  --install-root /absolute/path/to/new-disposable-root \
  --trials 5 > installed-package-raw.json

python3 cli/benchmarks/installed/benchmark.py analyse \
  --report installed-package-raw.json > installed-package-analysis.json
```

The runner verifies `SHA256SUMS`, the four release-manifest artifacts, and the
closed required evidence set: release manifest, SBOM, provenance, and
`dependency-evidence.json`. Dependency evidence must retain its inventory-only,
non-release policy; its exact bytes are bound by the release manifest, SBOM,
and provenance. Source records, the Rust CLI/Bun/Windows-sidecar component
inventories, and matching SBOM dependency components are validated without
claiming license, vulnerability, or signing authority. Unknown evidence members
fail closed. Archives are read with bounded regular-file-only extraction:
traversal, links, duplicate portable names, special files, and expansion beyond
the limits are rejected.

Rust and Bun standalone archives are extracted into separate owned roots. The
two npm tarballs are installed together using the discovered npm executable
with `--offline`, `--ignore-scripts`, an owned empty cache/config, and an
unreachable loopback registry. The installed Node launcher, platform manifest,
and executable must be byte-identical to their packaged sources. On Windows,
the process-host sidecar must have the same evidence-bound digest in both
standalone archives, the npm platform package, and the installed package.

Before measurement and again before returning evidence, the runner
authenticates the complete retained install trees: portable relative path,
entry type, material mode, byte length, and file/symlink digest. The benchmark
executes the installed npm launcher and retains the resolved Node executable's
identity as external toolchain evidence. It does not capture Node's
dynamic-library/resource closure or eliminate the tool/launcher exec-boundary
TOCTOU window. The separate mechanical conformance step uses a verified
launcher snapshot and records the same external-interpreter limitation.

Installation wall time and regular-file byte count are reported separately
from invocation timing. Each subprocess has bounded output and must settle its
direct process and pipes. POSIX additionally requires its new process group to
be empty; failures trigger exact-group cleanup and produce no measurement.
This settles the direct process, bounded readers, and original group; it is not
strict descendant containment. A descendant that calls `setsid()` can escape,
so every report records `detachedDescendantContainment: not-enforced` and no
benchmark result can supply strict adapter or release authority.
Actual Windows measurement fails before spawning any installer or product until
a native Job Object containment route is available. Host-neutral Windows
package and sidecar validation remains supported; it is not runtime evidence.

The report retains artifact, package, binary, launcher, tool, and evidence
digests plus argv-based command provenance. Its limitations are explicit:
semantic, portability, release, and ranking evaluations are not performed.
The analysis command accepts only the closed report contract, including exactly
three installations and surfaces, equal contiguous trial ordinals, finite
nonnegative metrics, exact settlement/transport/exit/task/package identities,
and no unknown fields. It deterministically recomputes timing summaries and
does not select or name a winner.

## Tests

```text
python3 -m unittest -v cli/benchmarks/installed/test_benchmark.py
```

Tests create small package-local-shaped fixtures without providers, credentials,
network access, ambient npm caches, or lifecycle scripts. They cover successful
offline installation and measurement as well as checksum tamper, extra members,
malformed or unbound dependency evidence, dependency/SBOM component divergence,
duplicate checksums, traversal, archive links/duplicates, binary divergence,
lifecycle scripts, unsafe install roots, process settlement, strict report
reanalysis mutations, Windows pre-spawn refusal, unsupported platforms, and
host-neutral Windows sidecar binding.

The current focused suite contains 23 tests. A passing suite is local
development evidence only; the sentinel, detached-descendant limitation,
external interpreter closure, and absent release profile prevent semantic,
portability, strict-containment, winner, or release claims.
