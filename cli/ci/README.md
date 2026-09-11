# Local admission gates

These commands are the local authority that Phase 7 automation will call. A
GitHub workflow must not replace or weaken them.

Run the complete provider-free admission sequence from the repository root:

```sh
python3 cli/ci/run_local.py
```

Install the one pinned Python test environment first:

```sh
python3 -m pip install --require-hashes --only-binary=:all: -r cli/ci/requirements-test.txt
```

The Python test lock closes direct and transitive versions and admits only
hash-identified Python 3.10 wheels for the five CI target families. CI policy
rejects an install command that drops either hash verification or binary-only
resolution.

`run_local.py` strips provider credentials, runner overrides, and ambient build
hooks; poisons ordinary proxy-based network access; launches every command
directly without a shell; and stops at the first failed authority. `--list`,
repeatable `--only GATE`, and `--quick` make the same named gates convenient
during local development. The real-harness and direct-skill lanes' fake
provider-free contracts are included, while their explicitly acknowledged
live-provider commands remain opt-in.

These gates test the noninteractive CLI wrapper. They do not replace the direct
interactive path in which a user starts a harness TUI and that harness loads the
installed `open-prose` skill; that path neither calls nor requires this CLI.

The full pass also runs the isolated Windows-host static and non-native checks,
the adapter oracle and product adversary, direct artifact signal/cleanup tests,
installed archive/npm package tests, shell-free Windows resolution, dependency
inventory binding, deterministic release notes, and the release
preflight/workflow policy. The functional-alpha package gate additionally
builds release-profile products, installs both archives and the offline npm
pair, then exercises 12 provider-free first-run journeys: Prime, OMP, Codex,
and Claude through the direct Rust archive, direct Bun archive, and offline
npm-global installation. Every journey starts from a fresh process environment;
each surface deliberately reuses one isolated user configuration while switching
Prime → OMP → Codex → Claude so stale bundle members cannot survive. Each journey
persists the selected harness bundle with that candidate's `cli harness use`
operation, checks the exact saved configuration bytes, inspects readiness with that same
candidate's `cli doctor` operation, and
runs `echo-v0` without a harness or transport override.
The frozen live-harness fixture supplies the admitted protocol response. Prime
and OMP save the implemented explicit `prime-harness-login` and
`omp-harness-login` routes with a fixture model during selection; doctor and
run then use only the saved bundle. The fake harness observes that
no provider credential is passed, so these checks contact no provider. The
real harness-login routes retain unknown provider/account/billing identity and
auth readiness. Explicit Prime/OMP provider-key profiles instead use a fresh
runner-owned mode-0700 configuration directory for the complete child/service
lifetime, and actual execution is the first auth authority; no model-list/help
probe or fallback is permitted. Every actual Prime child receives exactly
`PRIME_AGENT_TELEMETRY=0`, overriding ambient conflict, while OMP, Codex, and
Claude receive no such adapter control. Functional-alpha version admission is
an exact allowlist: Prime `0.7.0` and `0.8.1`, OMP `18.0.9`, Codex
`0.149.0-alpha.4.1`, and Claude `2.1.243`. `echo-v0` remains explicitly
nonsemantic. OMP `18.0.9` additionally requires Bun `>=1.3.14`; the exact
combined repair is
`npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9`. The recipe,
functional-alpha authority, generated package guidance, release notes, and
provider-free package admission structurally bind that same prerequisite.
There are currently 39 named gates. A
green non-Windows pass does not promote the Windows runtime capability; native
Windows CI evidence remains a separate release gate. The host plus independent
Rust and Bun protocol clients and product integrations are tested, and Windows
packages carry the exact digest-bound sibling sidecar. Compiled admission
remains false until native Windows evidence exists; packaging presence cannot
promote runtime readiness.

The authoritative provider-free local execution completed all 39/39 gates on
2026-08-31. Its success line deliberately labels the result as limited local
evidence and says detached descendant containment is not enforced. The alpha
package gate selects all 11 `AlphaPackageAdmissionTests` class tests in that
graph; the complete alpha admission suite contains 22 tests. Current
constituent authorities are 20 shared-contract tests, 14 image tests, 16
adapter-oracle tests, 17 cross-product adversary tests, 224 Rust workspace
tests, 396 Bun tests / 2,473 expects, 48 package-local tests, 17 workflow-policy
tests, and 56 live contract tests.

The separate cost-acknowledged lane now retains a current evidence-v5/matrix-v4
12-cell Darwin ARM64 aggregate for exact source commit
`31d81c55c8c90a7358b1cd8c5a0ccba631290a83`. Rust, Bun, and npm each
passed Prime, OMP, Codex, and Claude once on their valid exact routes; see the
checked-in [evidence report](../conformance/live-alpha/evidence/31d81c55c8c90a7358b1cd8c5a0ccba631290a83/REPORT.md).
Historical direct 8/8 and evidence-v2 12/12 collections were not relabeled.
Evidence v5 and matrix v4 add target-bound candidate custody plus bounded
declared harness package/runtime byte custody, distribution route, model, and
auth-route category. Ambient harness configuration, plugins, skills, cached
account and provider state, dynamic resources, and provider-side routing remain
explicitly external and unbound. The historical W54 Prime/OMP
malformed-protocol failures remain unresolved history rather than being
promoted by the stronger evidence format.

Live reports remain candidate-reported, provider spend remains unverified, and
semantic status remains `not-applicable`. These `echo-v0` results do not add
semantic, portability, strict-wrapper, release, or publication authority to
the provider-free gate. No artifact from this implementation has been
published.

For an ordinary local build rather than admission, the repository also has a
local-only driver:

```sh
python3 cli/ci/build_local.py --smoke --json
```

It scrubs provider credentials and ambient build overrides, builds both
development candidates, and immediately captures each result into a private
owned snapshot. Smoke, install, and packaging consume only those snapshot
bytes; later changes to repository build outputs cannot change the admitted
candidate. Commands have bounded output and time, and POSIX cleanup owns the
direct process and its original child process group. A descendant can escape
that group with `setsid()`, so the driver explicitly reports detached
descendant containment as not enforced. The driver can optionally create an
explicit disposable install directory or deterministic development package
directory.
It is a convenience surface, not a release or semantic authority; its report
always identifies the development profile and enabled test seams.

For the closest local approximation of the downloadable experience, run the
non-publishing rehearsal with a path that does not yet exist:

```sh
python3 cli/ci/rehearse_release.py \
  --output /tmp/openprose-local-rehearsal \
  --trials 3
```

That command builds and packages once, safely extracts both standalone
archives, installs the two npm tarballs together offline with lifecycle scripts
disabled, and exercises direct Rust, direct Bun, and the installed npm launcher
through the deterministic mock. It retains raw timing, deterministic
reanalysis, exact digest cross-bindings, a manifest, and checksums. The output
explicitly denies semantic, portability, ranking, release, publication, and
native-Windows claims. Windows rehearsal stops before building or spawning
until native Job Object containment is available.

A real one-trial local rehearsal has completed successfully for the current
development profile: the two standalone installs and offline npm install
completed all 180 selected mechanical candidate/differential validations. It
still used the sentinel and deterministic test seam, and therefore did not
evaluate semantics, cross-harness portability, strict detached-descendant
containment, the release profile, or publication readiness.

`check_workflows.py` guards all three root workflow definitions with mutation
tests. It requires exact tool and Action revisions, credential-free checkouts,
the five native target rows, the closed build-once graph, a `main`-owned control
checkout separated from an ancestor candidate, least privilege, quoted
dispatch-input boundaries, and the absence of publication/promotion commands.
The protected draft step uses `create_draft_release.py`, whose closed asset
inventory, dependency-component lineage, deterministic human-readable notes,
and literal draft-only GitHub API request are independently tested.
It requires an existing version tag that peels to the exact source before the
release request and uses no tag-creation endpoint. Because GitHub exposes no
atomic tag-SHA precondition on release creation, protected tag rules and a
publication-time identity check remain outside this local proof.
If an asset upload is accepted but its response is lost, a retry performs a
bounded authenticated draft search, accepts only the exact tag/name/body and
draft identity, reuses only assets whose remote SHA-256/size/state/type/URL
match the admitted bytes, and re-peels the tag after reconciliation. It never
deletes, updates, publishes, or duplicates remote state automatically.
Actionlint is a useful additional local syntax check when installed:

```sh
actionlint .github/workflows/openprose-cli-ci.yml \
  .github/workflows/openprose-cli-draft-release.yml \
  .github/workflows/openprose-cli-alpha-release.yml
```

`release_preflight.py` is the first draft-release boundary. It binds the main
control revision, full candidate commit, both product versions, complete image
directory, exact embedded bundle and checksum, image/manifest digests, and
closed canonical/evidence attestations. Against the current tree it must return
nonzero before builds because `echo-v0` is not the canonical language runtime
and the protected attestations are absent. The full-release draft workflow
references an exact
separately protected producer workflow and artifact, but that producer workflow
is intentionally not present in this repository yet. Candidate-controlled
files are never accepted as substitutes. Its unit test exercises that expected
failure; ordinary local admission remains green.

After packaging, the release graph redownloads the original package artifact
for each target and invokes `release_package_admission.py` from the protected
control checkout. POSIX targets install the exact two standalone archives and
offline npm pair, then execute the frozen eight-case release-safe corpus on all
three surfaces. Windows performs closed static archive/npm/sidecar validation
and records `blocked-before-execution`; it never substitutes Python process
groups for Job Object authority. Assembly redownloads the original package and
the canonical report separately, independently checks the raw corpus digest,
ordered case identity, exit status, empty stderr, structured projections,
complete byte map, protected inputs, native lineage, workflow identity, and
fixed non-authoritative claims, then includes all five reports in the aggregate
checksum. The draft helper repeats those checks against the protected corpus,
control SHA, and actual native/package bytes before any network request. This
does not establish
language semantics, program portability, strict containment, publication
authority, or a completed workflow run.

If a package-admission matrix cell fails, the workflow validates and retains
its bounded `openprose.release-package-admission-error/1` envelope under a
separate `package-admission-failure-*` artifact. Failure evidence can never
enter the successful assembly artifact namespace, while maintainers still get
an actionable machine-readable diagnostic after the runner workspace is gone.

`check_dependencies.py` verifies the exact Rust, Cargo, Bun, JSON Schema, and
referencing versions plus the supported Python, Node, and npm floors before the
contract suites run. It reports every observed version in one JSON object.

`dependency_evidence.py` separately inventories the resolved Rust CLI,
Windows process-host, and Bun lock graphs without network access. Package and
draft boundaries bind that exact report and its components. The protected
profile-admission job regenerates it with main-owned control code against the
exact candidate checkout, and every target package must match those bytes. The
release check remains nonzero until independent license, vulnerability,
fetched-package, and signing authorities exist.

`check_architecture.py` verifies the structural half of the CLI/language
boundary: stable product dependencies and relative source references remain
inside `cli/`; stable runner source contains no language-format or ambient
skill-installation knowledge; no shell-string or outer-PTY launch path is
introduced; and forwarded argv is not used as a program-content read target.

The check is intentionally conservative and is paired with black-box exact-byte
delivery tests. It does not claim that static matching proves runtime sandboxing
or model semantics.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s cli/ci -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 cli/ci/check_architecture.py
```
