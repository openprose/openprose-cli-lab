# Proposed benchmark-policy schema delta

The shared `openprose.benchmark-policy/1` schema is authoritative and remains
unchanged. The local smoke policy validates against it. A future v2 should add
the following trust controls currently frozen in the companion benchmark
profile because v1 has `additionalProperties: false`:

- the recorded numeric randomization seed, not only its source;
- authoritative-cost and timeout stop thresholds plus the rule that every
  planned-but-unrun trial remains represented;
- retry visibility, declared retry accounting, and hidden-retry
  disqualification;
- explicit comparison surface and comparison group, preventing direct-skill
  and wrapper pairing;
- cache preparation/qualification for cold and warm measurements;
- per-platform process-containment authority and release support, plus
  direct-process, output-reader, and cleanup settlement evidence;
- success-only latency eligibility, separate all-attempt duration, and
  success-to-success paired-delta exclusion accounting;
- authoritative versus unavailable cost evidence with no imputation state;
- immutable artifact, harness descriptor, transport, model, image, program,
  corpus, validator, policy-schema, and profile digests; and
- an explicit `releaseEligible`/semantic-claim gate tied to canonical language
  artifacts and protected holdout availability.

Until such a schema is approved, `local-smoke.profile.json` is a closed local
contract validated by this rig. It cannot weaken any v1 policy constant.
