# OpenProse trusted benchmark rig

This directory owns black-box measurement and analysis only. It does not parse
OpenProse, call providers, alter either CLI product, or turn the sentinel image
into semantic evidence.

The benchmark model keeps two execution surfaces separate: an interactive
harness may load the installed `open-prose` skill directly without the CLI,
while the `prose` CLI is a thin noninteractive wrapper around a selected
harness adapter. The checked sentinel smoke does not run a TUI or prove direct
skill behavior; similarly, wrapper evidence cannot be relabeled as direct-skill
evidence.

The checked-in example benchmarks the frozen installed-development Rust and
Bun executables against the shared provider-free `mock/fake-process` fixture.
It is an immutable observation of only the exact binary and input digests
recorded in its raw evidence and manifest. The development artifact paths are
mutable build outputs and are not claimed to retain those bytes after a later
build, test, or lint gate. The evidence is deliberately marked:

- `releaseEligible: false`;
- `semanticStatus: not-applicable`;
- `proseComplete: false`; and
- `sentinelOnly: true`.

The canonical language image, semantic terminal contract, language corpus,
validators, immutable release-candidate artifacts, protected holdouts, and
native release ceremony are absent. Nothing here supports a semantic,
portability, Prose Complete, product-winner, or public performance claim.

## Trust model

Policy is frozen before collection in
[`policy/local-smoke.policy.json`](policy/local-smoke.policy.json). It validates
against the shared, read-only `benchmark-policy.schema.json`. The companion
closed profile records the deterministic random seed and controls that the v1
schema cannot represent. The proposed v2 additions are documented in
[`policy/schema-delta.md`](policy/schema-delta.md); the shared schema is not
modified here.

Checked-evidence admission and new-collection admission are intentionally
separate. The checked-evidence tests validate the policy, profile declarations,
recorded input verification, raw/summary digests, and deterministic re-analysis
without consulting whatever bytes later occupy mutable `target/` or `dist/`
paths. Profile v2 also binds the fixture size and SHA-256. Before any new
collection, `runner.cli verify` reads the live fixture and target bytes and
fails closed unless their declared identities match. Collection then copies
those exact bytes into a private read-only custody directory, executes only the
snapshots, and reauthenticates every snapshot before writing evidence. This
prevents build order from relabeling historical evidence and prevents a later
build from changing bytes during a new run. Snapshot custody is same-user
hardening plus detection, not a privilege boundary against a process that can
rewrite and perfectly restore its own files.

The runner enforces these properties:

- warmups and measured repetitions are separate, but both remain in raw data;
- every repetition is a complete randomized block, with deterministic order
  from the recorded seed;
- pairing never crosses `wrapper` and `direct-skill` surfaces or an explicit
  comparison group;
- timeouts and failures remain trials, and extreme timings are never dropped;
- latency distributions contain successful trials only, while duration for all
  executed attempts is reported separately;
- paired latency contains only success-to-success pairs, with every excluded
  pair counted by reason;
- hitting the authoritative-cost or timeout stop keeps every remaining planned
  trial as an explicit `not-run` record;
- cost is either authoritative or unavailable—never estimated, imputed, or
  treated as zero;
- command stdout has no authority for cost, retry, or component-span facts;
  absent a separately runner-owned observation channel, those fields remain
  unavailable;
- declared retries, observed attempts, unavailable visibility, and hidden-retry
  suspicions remain distinct;
- wrapper residual is calculated only for complete spans on one monotonic clock
  or a validated correlation; otherwise component spans are published without
  subtraction;
- external wall distributions and paired differences use deterministic
  bootstrap median intervals; and
- raw stdout/stderr are bounded and sanitized, while structured diagnostics
  redact sensitive keys and common credential forms.

The command driver always launches an admitted private snapshot as an argv
array directly with `shell=False`,
a closed environment, separate stdout/stderr, a deadline, bounded capture, and
no implicit retries. On POSIX it starts a new session and owns the resulting
original process group, which it can terminate with group signals. A descendant
can escape that boundary with `setsid()`, so this is not race-free or strict
descendant containment and cannot support adapter/release admission. POSIX
evidence therefore records `releaseContainmentSupported: false` with the
blocker `detached-descendant-containment-not-enforced`; settled original-group
readers and cleanup do not upgrade that claim. On
Windows the current Python driver creates a new process group but can terminate only the
direct child; its evidence therefore says `direct-child-only` and
`releaseContainmentSupported: false`. Windows release benchmarking remains
blocked until the native Job Object process host is integrated.

Every newly collected trial records its execution boundary. `CommandDriver`
records `subprocess`; subprocess is also the fail-closed default for a custom
driver that does not declare a boundary. The provider-free `FakeDriver` records
`synthetic-in-process`, so its test-only exemption from subprocess evidence is
visible in raw data and cannot be confused with a measured command.

Every subprocess trial must supply the complete, closed containment and
settlement record. Missing containment or settlement, missing reader or cleanup
facts, unknown fields or values, mismatched authority, and internally
inconsistent claims all fail evidence admission. A driver-claimed success is
not accepted unless the direct process exited, both bounded output readers
settled without read errors, required cleanup settled, and the derived
`allowsSuccessfulTrial` value agrees. The attempted trial and its stable
evidence-admission reason remain raw. It is excluded from success latency,
success-to-success pairs, residual efficiency, and success-qualified cost; its
cost still counts toward the raw spend stop so failed evidence cannot erase
money already spent.

## Independent scorecards

The report has five non-collapsible scorecards:

- **transport:** success-only external latency and success-to-success paired
  differences for the scripted fake-process run, plus separate all-attempt
  duration, explicit exclusion accounting, containment, and settlement;
- **developer experience:** cold/warm local startup, doctor, dry-run, artifact
  byte size/digest, and the explicitly limited development install surface;
- **agent efficiency:** retry visibility and residual-span eligibility, with
  model calls/tokens unavailable or not applicable in this smoke;
- **cost:** success-qualified authoritative totals and unavailable counts
  without imputation; all attempted authoritative spend remains separately in
  raw accounting and stop enforcement; and
- **semantic quality:** unevaluated and not applicable until canonical language
  inputs exist.

No composite score or winner is calculated. The checked measurements describe
only their exact development artifacts, local machine, sentinel fixture, and
recorded run; they are not recommendations or public performance claims.

The checked example predates snapshot custody. Its v2 input-verification block
is a mechanical historical migration: it binds the fixture bytes from the Git
object at the original evidence commit, retains the original profile and raw
digests as provenance, and explicitly records custody and final
reauthentication as not observed/not performed. No timing or trial observation
was changed, and the historical run is not upgraded to the new custody claim.

## Exact commands

Run from `cli/benchmarks/`:

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 -m runner.cli verify \
  --profile policy/local-smoke.profile.json
PYTHONPATH=. python3 -m runner.cli run \
  --profile policy/local-smoke.profile.json \
  --output-dir evidence/my-local-smoke
```

The `verify` command is the immediate pre-collection gate. It is expected to
refuse after another build command replaces either development artifact; never
update a digest merely to make that check pass or attribute checked evidence to
the replacement bytes.

Evidence directories are write-once by default. The checked example was
refrozen once from its exact verified development artifacts under the recorded
policy and seed. A future input change must first make the example explicitly
stale, freeze new digests, and pass every admission gate in a new output
directory before an intentional reviewed repository update. Existing evidence
directories are immutable even when `--overwrite` is supplied; the flag cannot
authorize a non-atomic in-place generation replacement.

New evidence is written into a private sibling staging directory, fully
reauthenticated, and published with an operating-system no-replace rename. The
three-file generation therefore appears together or not at all. Existing,
symlink, and non-directory destinations fail before collection, and a staging
failure leaves prior evidence untouched. Single-file deterministic analysis
output uses a random same-directory staging file plus atomic replacement and
never follows a destination symlink.

Reproduce a report byte-for-byte from raw evidence:

```sh
PYTHONPATH=. python3 -m runner.cli analyze \
  --raw evidence/local-smoke-example/raw.json \
  --policy policy/local-smoke.policy.json \
  --summary /tmp/openprose-summary.json
cmp evidence/local-smoke-example/summary.json /tmp/openprose-summary.json
```

The example contains:

- [`evidence/local-smoke-example/raw.json`](evidence/local-smoke-example/raw.json)
  with the complete randomized order, all warmups/trials, sanitized command
  evidence, mechanical validation, timing, costs, retries, machine facts, and
  exact identities;
- [`evidence/local-smoke-example/summary.json`](evidence/local-smoke-example/summary.json)
  with deterministic analysis and separate scorecards; and
- [`evidence/local-smoke-example/manifest.json`](evidence/local-smoke-example/manifest.json)
  binding the raw and summary byte digests.

## Release use

Later release infrastructure may consume sanitized evidence produced by this
rig, but this lane never calls a real model. An official run must replace this
profile with immutable conformance-passing release artifacts and externally
protected canonical/holdout inputs, then retain the same artifact bytes for
promotion. A release job must also qualify native machines and measure the
installation/package surfaces omitted by this development smoke.
