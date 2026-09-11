from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner.rig import (
    CommandDriver,
    FakeDriver,
    Observation,
    _mechanical_validation,
    _process_containment_capability,
    _process_evidence_validation,
    _settlement_evidence,
    bootstrap_interval,
    build_schedule,
    canonical_json_bytes,
    derive_residual,
    redact,
    run_benchmark,
    summarize,
    validate_profile_claims,
)


class ClaimedSubprocessDriver(FakeDriver):
    """Adversarial driver that claims a subprocess boundary without proving it."""

    execution_boundary = "subprocess"


class UndeclaredBoundaryDriver:
    """Custom drivers do not receive the synthetic test exemption implicitly."""

    def invoke(self, _invocation) -> Observation:
        return Observation.success(1.0)


def settled_process_evidence() -> dict:
    containment = _process_containment_capability("posix")
    return {
        "containment": containment,
        "settlement": _settlement_evidence(
            containment,
            direct_process_exited=True,
            stdout_reader_settled=True,
            stderr_reader_settled=True,
            reader_errors=[],
            cleanup={"attempted": False, "status": "not-required"},
        ),
    }


def policy(repetitions: int = 3) -> dict:
    return {
        "schema": "openprose.benchmark-policy/1",
        "policyId": "unit-policy",
        "policyVersion": "1",
        "frozenAt": "2026-08-27T00:00:00Z",
        "warmups": 1,
        "repetitions": repetitions,
        "randomizedPairing": {
            "enabled": True,
            "seedSource": "profile-fixed",
            "recordSeed": True,
        },
        "timeoutAccounting": {
            "perRunMs": 100,
            "timeoutsCountAsFailures": True,
            "includeElapsed": True,
        },
        "noOutlierDrop": True,
        "retainFailures": True,
        "confidence": {"method": "paired-bootstrap", "level": 0.95, "resamples": 200},
        "machineQualification": {
            "recordOs": True,
            "recordArch": True,
            "recordCpu": True,
            "recordMemory": True,
            "recordLoad": True,
            "maximumLoadAveragePerCore": 1.0,
        },
        "regressionBudgets": [
            {
                "metric": "wall-ms",
                "direction": "lower-is-better",
                "maximumRelativeRegression": 0.10,
            }
        ],
        "holdout": {
            "requiredForPublicClaims": True,
            "protectedExternally": True,
            "identityHiddenFromImplementations": True,
        },
        "residualTiming": {
            "publishOnlyWithCompleteSpans": True,
            "requireCommonOrCorrelatedClock": True,
            "fallback": "publish-components-without-subtraction",
        },
    }


def profile() -> dict:
    return {
        "schema": "openprose.benchmark-profile/1",
        "profileId": "unit-profile",
        "profileVersion": "1",
        "seed": 12345,
        "releaseEligible": False,
        "image": {
            "version": "sentinel-v1",
            "sha256": "e" * 64,
            "releaseEligible": False,
        },
        "corpus": {"status": "unavailable", "id": None, "sha256": None},
        "semanticStatus": "unknown",
        "proseCompleteClaim": False,
        "stopConditions": {"maximumAuthoritativeCostUsd": 10.0, "maximumTimeouts": 5},
        "actions": [
            {"id": "transport", "scorecard": "transport", "argv": ["--version"]}
        ],
        "targets": [
            {
                "id": "rust",
                "surface": "wrapper",
                "comparisonGroup": "products",
                "artifact": "rust-prose",
                "harness": "mock",
                "transport": "deterministic",
                "model": {"status": "not-applicable", "id": None, "sha256": None},
            },
            {
                "id": "bun",
                "surface": "wrapper",
                "comparisonGroup": "products",
                "artifact": "bun-prose",
                "harness": "mock",
                "transport": "deterministic",
                "model": {"status": "not-applicable", "id": None, "sha256": None},
            },
        ],
        "program": {"id": "sentinel", "sha256": "a" * 64},
        "validator": {
            "kind": "mechanical-transport",
            "id": "shape",
            "sha256": "b" * 64,
        },
    }


class ScheduleTests(unittest.TestCase):
    def test_pairing_is_deterministic_complete_and_surface_separated(self) -> None:
        configured = profile()
        configured["targets"].append(
            {
                "id": "direct",
                "surface": "direct-skill",
                "comparisonGroup": "products",
                "artifact": "not-a-wrapper",
                "harness": "mock",
                "transport": "interactive-tui",
                "model": {"status": "not-applicable", "id": None, "sha256": None},
            }
        )
        first = build_schedule(configured, policy())
        second = build_schedule(configured, policy())
        self.assertEqual(first, second)
        blocks: dict[str, set[str]] = {}
        surfaces: dict[str, set[str]] = {}
        for trial in first:
            blocks.setdefault(trial.pair_id, set()).add(trial.target_id)
            surfaces.setdefault(trial.pair_id, set()).add(trial.surface)
        self.assertTrue(all(len(values) == 1 for values in surfaces.values()))
        wrapper_blocks = [
            values for key, values in blocks.items() if key.startswith("wrapper/")
        ]
        self.assertTrue(wrapper_blocks)
        self.assertTrue(all(values == {"rust", "bun"} for values in wrapper_blocks))
        raw = run_benchmark(
            configured,
            policy(1),
            FakeDriver(),
            machine={"qualified": True},
        )
        report = summarize(raw, policy(1))
        surfaces_report = report["scorecards"]["transport"]["surfaces"]
        self.assertEqual(set(surfaces_report), {"wrapper", "direct-skill"})
        direct = surfaces_report["direct-skill"]["actions"]["transport"]
        self.assertEqual(direct["pairedDifferencesMs"], [])
        wrapper = surfaces_report["wrapper"]["actions"]["transport"]
        self.assertTrue(wrapper["pairedDifferencesMs"])


class StopAndRetentionTests(unittest.TestCase):
    def test_authoritative_cost_and_timeout_stops_retain_unrun_trials(self) -> None:
        configured = profile()
        configured["stopConditions"] = {
            "maximumAuthoritativeCostUsd": 0.50,
            "maximumTimeouts": 1,
        }
        configured["seed"] = 1
        outcomes = [
            Observation.success(10.0, cost_status="authoritative", cost_usd=0.50),
            Observation.timeout(100.0),
        ]
        raw = run_benchmark(
            configured, policy(4), FakeDriver(outcomes), machine={"qualified": True}
        )
        self.assertEqual(len(raw["trials"]), len(build_schedule(configured, policy(4))))
        self.assertTrue(any(trial["status"] == "not-run" for trial in raw["trials"]))
        self.assertEqual(raw["stop"]["reason"], "authoritative-cost-limit")
        self.assertEqual(raw["accounting"]["authoritativeCostUsd"], 0.50)
        self.assertEqual(raw["accounting"]["timeouts"], 0)

        configured["stopConditions"]["maximumAuthoritativeCostUsd"] = 50.0
        timeout_raw = run_benchmark(
            configured,
            policy(4),
            FakeDriver([Observation.timeout(100.0)]),
            machine={"qualified": True},
        )
        self.assertEqual(timeout_raw["stop"]["reason"], "timeout-limit")
        self.assertEqual(timeout_raw["accounting"]["timeouts"], 1)
        self.assertTrue(
            any(trial["status"] == "not-run" for trial in timeout_raw["trials"])
        )

    def test_failures_and_extreme_values_are_all_retained(self) -> None:
        configured = profile()
        outcomes = [
            Observation.success(1.0),
            Observation.failure(2.0, exit_code=9, stderr="broken"),
            Observation.success(10000.0),
            Observation.timeout(100.0),
            Observation.success(4.0),
            Observation.success(5.0),
            Observation.success(6.0),
            Observation.success(7.0),
        ]
        raw = run_benchmark(
            configured, policy(3), FakeDriver(outcomes), machine={"qualified": True}
        )
        measured = [trial for trial in raw["trials"] if trial["phase"] == "measurement"]
        self.assertEqual(len(measured), 6)
        self.assertTrue(any(trial["status"] == "failure" for trial in raw["trials"]))
        self.assertTrue(any(trial["wallMs"] == 10000.0 for trial in raw["trials"]))
        report = summarize(raw, policy(3))
        self.assertEqual(report["rules"]["outliersDropped"], 0)
        self.assertEqual(report["rules"]["failuresRetained"], True)


class TrustTests(unittest.TestCase):
    def test_synthetic_driver_is_explicitly_distinguished_from_a_subprocess(
        self,
    ) -> None:
        raw = run_benchmark(
            profile(),
            policy(1),
            FakeDriver([Observation.success(1.0)] * 4),
            machine={"qualified": True},
        )
        first = raw["trials"][0]
        self.assertEqual(
            first["executionBoundary"],
            {
                "kind": "synthetic-in-process",
                "processEvidenceRequired": False,
            },
        )
        self.assertEqual(first["processEvidenceValidation"]["status"], "not-applicable")
        self.assertEqual(first["status"], "success")

    def test_subprocess_driver_claimed_success_without_evidence_fails_closed(
        self,
    ) -> None:
        raw = run_benchmark(
            profile(),
            policy(1),
            ClaimedSubprocessDriver([Observation.success(1.0)] * 4),
            machine={"qualified": True},
        )
        first = raw["trials"][0]
        self.assertEqual(first["processStatus"], "success")
        self.assertEqual(first["status"], "failure")
        self.assertEqual(first["failureKind"], "process-evidence-invalid")
        self.assertEqual(
            first["processEvidenceValidation"],
            {
                "status": "failed",
                "reason": "process-evidence-missing",
            },
        )

    def test_undeclared_custom_driver_defaults_to_subprocess_and_fails_closed(
        self,
    ) -> None:
        raw = run_benchmark(
            profile(),
            policy(1),
            UndeclaredBoundaryDriver(),
            machine={"qualified": True},
        )
        first = raw["trials"][0]
        self.assertEqual(first["executionBoundary"]["kind"], "subprocess")
        self.assertTrue(first["executionBoundary"]["processEvidenceRequired"])
        self.assertEqual(first["status"], "failure")
        self.assertEqual(
            first["processEvidenceValidation"]["reason"], "process-evidence-missing"
        )

    def test_partial_unknown_and_inconsistent_process_evidence_fail_closed(
        self,
    ) -> None:
        complete = settled_process_evidence()
        cases = [
            (None, "process-evidence-missing"),
            ({"settlement": complete["settlement"]}, "containment-missing"),
            ({"containment": complete["containment"]}, "settlement-missing"),
            (
                {
                    **complete,
                    "settlement": {
                        key: value
                        for key, value in complete["settlement"].items()
                        if key != "stdoutReaderSettled"
                    },
                },
                "settlement-missing-field",
            ),
            (
                {
                    **complete,
                    "settlement": {
                        **complete["settlement"],
                        "cleanup": {"attempted": False},
                    },
                },
                "cleanup-missing-field",
            ),
            ({**complete, "futureClaim": True}, "process-evidence-unknown-field"),
            (
                {
                    **complete,
                    "settlement": {**complete["settlement"], "status": "unknown"},
                },
                "settlement-invalid-value",
            ),
            (
                {
                    **complete,
                    "settlement": {
                        **complete["settlement"],
                        "directProcessExited": False,
                    },
                },
                "settlement-inconsistent",
            ),
        ]
        configured = profile()
        configured_policy = policy(1)
        for evidence, reason in cases:
            with self.subTest(reason=reason):
                observation = Observation(
                    status="success",
                    wall_ms=0.01,
                    exit_code=0,
                    process_evidence=evidence,
                )
                raw = run_benchmark(
                    configured,
                    configured_policy,
                    ClaimedSubprocessDriver([observation] * 4),
                    machine={"qualified": True},
                )
                first = raw["trials"][0]
                self.assertEqual(first["status"], "failure")
                self.assertEqual(first["failureKind"], "process-evidence-invalid")
                self.assertEqual(first["processEvidenceValidation"]["reason"], reason)

    def test_bad_process_evidence_cannot_create_latency_pair_or_cost_victory(
        self,
    ) -> None:
        configured = profile()
        configured["stopConditions"]["maximumAuthoritativeCostUsd"] = 1_000.0
        configured_policy = policy(1)
        complete = settled_process_evidence()
        outcomes = []
        for plan in build_schedule(configured, configured_policy):
            if plan.target_id == "bun":
                outcomes.append(
                    Observation(
                        status="success",
                        wall_ms=0.01,
                        exit_code=0,
                        cost_status="authoritative",
                        cost_usd=0.01,
                        process_evidence=None,
                    )
                )
            else:
                outcomes.append(
                    Observation(
                        status="success",
                        wall_ms=100.0,
                        exit_code=0,
                        cost_status="authoritative",
                        cost_usd=10.0,
                        process_evidence=complete,
                    )
                )
        raw = run_benchmark(
            configured,
            configured_policy,
            ClaimedSubprocessDriver(outcomes),
            machine={"qualified": True},
        )
        action = summarize(raw, configured_policy)["scorecards"]["transport"][
            "surfaces"
        ]["wrapper"]["actions"]["transport"]
        self.assertEqual(action["targets"]["bun"]["successLatencyMs"]["samples"], 0)
        self.assertEqual(action["pairedDifferencesMs"][0]["eligibleSuccessPairs"], 0)
        self.assertIsNone(action["winner"])
        cost = summarize(raw, configured_policy)["scorecards"]["cost"]
        self.assertEqual(cost["targets"]["bun"]["authoritative"]["samples"], 0)
        self.assertIsNone(cost["winner"])
        self.assertEqual(raw["accounting"]["authoritativeCostUsd"], 20.02)

    def test_doctor_validation_accepts_only_the_canonical_schema(self) -> None:
        configured = profile()
        action = {
            "id": "doctor",
            "scorecard": "dx",
            "expectation": "doctor",
            "argv": [],
        }
        target = configured["targets"][0]
        body = {
            "ready": True,
            "selectedHarness": target["harness"],
            "selectedTransport": target["transport"],
            "image": {
                "sha256": configured["image"]["sha256"],
                "releaseEligible": False,
            },
        }
        legacy = _mechanical_validation(
            Observation.success(
                1.0, stdout=json.dumps({**body, "schema": "openprose.doctor/1"})
            ),
            action,
            target,
            configured,
        )
        canonical = _mechanical_validation(
            Observation.success(
                1.0,
                stdout=json.dumps({**body, "schema": "openprose.doctor-report/1"}),
            ),
            action,
            target,
            configured,
        )
        self.assertEqual(legacy["status"], "failed")
        self.assertEqual(canonical["status"], "passed")

    def test_containment_capability_is_explicit_and_windows_is_not_release_supported(
        self,
    ) -> None:
        posix = _process_containment_capability("posix")
        windows = _process_containment_capability("nt")
        self.assertEqual(posix["authority"], "owned-process-group")
        self.assertFalse(posix["releaseContainmentSupported"])
        self.assertEqual(
            posix["blocker"], "detached-descendant-containment-not-enforced"
        )
        self.assertEqual(windows["authority"], "direct-child-only")
        self.assertFalse(windows["releaseContainmentSupported"])
        self.assertTrue(windows["nativeHelperRequired"])

    def test_reader_or_cleanup_non_settlement_cannot_be_successful(self) -> None:
        containment = _process_containment_capability("posix")
        reader_failure = _settlement_evidence(
            containment,
            direct_process_exited=True,
            stdout_reader_settled=False,
            stderr_reader_settled=True,
            reader_errors=[],
            cleanup={"attempted": True, "status": "not-settled"},
        )
        cleanup_failure = _settlement_evidence(
            containment,
            direct_process_exited=True,
            stdout_reader_settled=True,
            stderr_reader_settled=True,
            reader_errors=[],
            cleanup={"attempted": True, "status": "not-settled"},
        )
        self.assertEqual(reader_failure["status"], "not-settled")
        self.assertEqual(cleanup_failure["status"], "not-settled")
        self.assertFalse(reader_failure["allowsSuccessfulTrial"])
        self.assertFalse(cleanup_failure["allowsSuccessfulTrial"])

    def test_command_driver_turns_non_settlement_into_failure(self) -> None:
        driver = CommandDriver()
        forced = {
            "status": "not-settled",
            "allowsSuccessfulTrial": False,
            "directProcessExited": True,
            "stdoutReaderSettled": False,
            "stderrReaderSettled": True,
            "readerErrors": [],
            "cleanup": {"attempted": True, "status": "not-settled"},
            "authority": "owned-process-group",
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "runner.rig._settlement_evidence", return_value=forced
        ):
            observation = driver.invoke_argv(
                ["python3", "-c", "print('complete')"],
                None,
                timeout_ms=1000,
                cwd=temporary,
                environment={"PATH": "/usr/bin:/bin"},
            )
        self.assertEqual(observation.exit_code, 0)
        self.assertEqual(observation.status, "failure")
        self.assertEqual(observation.diagnostic["kind"], "process-settlement-failed")

    def test_runner_rejects_driver_claimed_success_with_failed_settlement(self) -> None:
        containment = _process_containment_capability("posix")
        observation = Observation(
            status="success",
            wall_ms=1.0,
            exit_code=0,
            process_evidence={
                "containment": containment,
                "settlement": {
                    "status": "not-settled",
                    "allowsSuccessfulTrial": False,
                    "directProcessExited": True,
                    "stdoutReaderSettled": False,
                    "stderrReaderSettled": True,
                    "readerErrors": [],
                    "cleanup": {
                        "attempted": True,
                        "status": "not-settled",
                        "authority": containment["authority"],
                    },
                    "authority": containment["authority"],
                },
            },
        )
        raw = run_benchmark(
            profile(),
            policy(1),
            ClaimedSubprocessDriver([observation]),
            machine={"qualified": True},
        )
        first = raw["trials"][0]
        self.assertEqual(first["status"], "failure")
        self.assertEqual(first["failureKind"], "process-settlement-failure")

    def test_hidden_retry_is_flagged(self) -> None:
        raw = run_benchmark(
            profile(),
            policy(1),
            FakeDriver(
                [Observation.success(1.0, observed_attempts=3, declared_retries=0)] * 4
            ),
            machine={"qualified": True},
        )
        flagged = [
            trial for trial in raw["trials"] if trial["retry"]["hiddenRetrySuspected"]
        ]
        self.assertTrue(flagged)
        self.assertFalse(raw["qualification"]["retryAccountingTrusted"])

    def test_redaction_covers_nested_fields_and_diagnostic_text(self) -> None:
        value = {
            "token": "top-secret",
            "nested": {"apiKey": "also-secret", "ok": "visible"},
            "text": "OPENAI_API_KEY=sk-live-123 Authorization: Bearer abc.def.ghi",
        }
        cleaned = redact(value)
        encoded = json.dumps(cleaned)
        self.assertNotIn("top-secret", encoded)
        self.assertNotIn("also-secret", encoded)
        self.assertNotIn("sk-live-123", encoded)
        self.assertNotIn("abc.def.ghi", encoded)
        self.assertIn("visible", encoded)
        self.assertIn("<redacted>", encoded)

    def test_residual_is_not_subtracted_without_complete_correlated_spans(self) -> None:
        incomplete = derive_residual(
            100.0,
            {
                "complete": False,
                "clock": "common-monotonic",
                "providerMs": 60.0,
                "toolMs": 10.0,
            },
        )
        self.assertIsNone(incomplete["residualWrapperMs"])
        self.assertEqual(incomplete["providerMs"], 60.0)
        self.assertEqual(incomplete["reason"], "incomplete-spans")
        uncorrelated = derive_residual(
            100.0,
            {
                "complete": True,
                "clock": "uncorrelated",
                "providerMs": 60.0,
                "toolMs": 10.0,
            },
        )
        self.assertIsNone(uncorrelated["residualWrapperMs"])
        complete = derive_residual(
            100.0,
            {
                "complete": True,
                "clock": "common-monotonic",
                "providerMs": 60.0,
                "toolMs": 10.0,
            },
        )
        self.assertEqual(complete["residualWrapperMs"], 30.0)

    def test_summary_and_bootstrap_are_byte_reproducible(self) -> None:
        raw = run_benchmark(
            profile(),
            policy(3),
            FakeDriver([Observation.success(float(index + 1)) for index in range(8)]),
            machine={"qualified": True},
        )
        first = canonical_json_bytes(summarize(raw, policy(3)))
        second = canonical_json_bytes(summarize(json.loads(json.dumps(raw)), policy(3)))
        self.assertEqual(first, second)
        self.assertEqual(
            bootstrap_interval([1.0, 2.0, 8.0], 200, 0.95, 99),
            bootstrap_interval([1.0, 2.0, 8.0], 200, 0.95, 99),
        )

    def test_sentinel_profile_cannot_make_semantic_or_release_claims(self) -> None:
        validate_profile_claims(profile())
        invalid = profile()
        invalid["semanticStatus"] = "success"
        with self.assertRaisesRegex(ValueError, "semantic"):
            validate_profile_claims(invalid)
        invalid = profile()
        invalid["releaseEligible"] = True
        with self.assertRaisesRegex(ValueError, "release"):
            validate_profile_claims(invalid)

    def test_command_driver_is_direct_no_shell_and_timeout_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture.py"
            fixture.write_text(
                "import json,sys,time\n"
                "request=json.load(sys.stdin)\n"
                "print(json.dumps({'seen':request,'_benchmarkDriver':"
                "{'costStatus':'authoritative','costUsd':'not-a-number','attempts':'many',"
                "'declaredRetries':{},'spans':'forged'}}))\n",
                encoding="utf-8",
            )
            driver = CommandDriver(output_limit_bytes=8192)
            observation = driver.invoke_argv(
                ["python3", str(fixture)],
                {"hello": "world"},
                timeout_ms=1000,
                cwd=temporary,
                environment={"PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(observation.status, "success")
            self.assertEqual(observation.cost_status, "unavailable")
            self.assertIsNone(observation.cost_usd)
            self.assertIsNone(observation.observed_attempts)
            self.assertIsNone(observation.declared_retries)
            self.assertIsNone(observation.spans)
            self.assertEqual(
                observation.process_evidence["settlement"]["status"], "settled"
            )
            self.assertTrue(
                observation.process_evidence["settlement"]["allowsSuccessfulTrial"]
            )
            self.assertEqual(
                _process_evidence_validation(
                    "subprocess", observation.status, observation.process_evidence
                )["status"],
                "passed",
            )
            fixture.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            timed_out = driver.invoke_argv(
                ["python3", str(fixture)],
                None,
                timeout_ms=20,
                cwd=temporary,
                environment={"PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(timed_out.status, "timeout")
            self.assertGreaterEqual(timed_out.wall_ms, 20)

    def test_runner_owned_metadata_contract_rejects_bad_types_deterministically(
        self,
    ) -> None:
        configured = profile()
        configured_policy = policy(1)
        invalid = (
            Observation.success(1.0, cost_status="authoritative", cost_usd="free"),
            Observation.success(1.0, observed_attempts="one"),
            Observation.success(1.0, declared_retries=-1),
            Observation.success(
                1.0, spans={"complete": True, "clock": "common-monotonic"}
            ),
        )
        for observation in invalid:
            with self.subTest(observation=observation):
                with self.assertRaisesRegex(ValueError, "observation metadata"):
                    run_benchmark(
                        configured,
                        configured_policy,
                        FakeDriver([observation]),
                        machine={"qualified": True},
                    )

    def test_failed_or_timed_out_pairs_never_enter_success_latency(self) -> None:
        configured = profile()
        configured_policy = policy(3)
        outcomes = []
        for plan in build_schedule(configured, configured_policy):
            if plan.phase == "warmup":
                outcomes.append(Observation.success(9999.0))
            elif plan.pair_index == 0:
                outcomes.append(
                    Observation.failure(1.0, exit_code=9)
                    if plan.target_id == "bun"
                    else Observation.success(10.0)
                )
            elif plan.pair_index == 1:
                outcomes.append(
                    Observation.success(20.0)
                    if plan.target_id == "bun"
                    else Observation.timeout(1.0)
                )
            else:
                outcomes.append(
                    Observation.success(40.0 if plan.target_id == "bun" else 30.0)
                )
        raw = run_benchmark(
            configured,
            configured_policy,
            FakeDriver(outcomes),
            machine={"qualified": True},
        )
        action = summarize(raw, configured_policy)["scorecards"]["transport"][
            "surfaces"
        ]["wrapper"]["actions"]["transport"]
        bun = action["targets"]["bun"]
        rust = action["targets"]["rust"]
        self.assertEqual(bun["successLatencyMs"]["samples"], 2)
        self.assertEqual(rust["successLatencyMs"]["samples"], 2)
        self.assertEqual(bun["successLatencyMs"]["p50"], 30.0)
        self.assertEqual(rust["successLatencyMs"]["p50"], 20.0)
        self.assertEqual(bun["allAttemptDurationMs"]["samples"], 3)
        comparison = action["pairedDifferencesMs"][0]
        self.assertEqual(comparison["eligibleSuccessPairs"], 1)
        self.assertEqual(comparison["excludedPairs"], 2)
        self.assertEqual(comparison["p50"], 10.0)
        self.assertEqual(
            comparison["exclusionReasons"],
            {"left-not-success": 1, "right-not-success": 1},
        )


if __name__ == "__main__":
    unittest.main()
