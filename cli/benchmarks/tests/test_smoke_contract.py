from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from os import chmod
from pathlib import Path
from unittest import mock

from runner.cli import (
    BENCHMARKS,
    DEFAULT_PROFILE,
    _publish_evidence_directory,
    _reauthenticate_execution_inputs,
    _snapshot_execution_inputs,
    _write_atomic_file,
    collect,
    load_and_verify,
    load_profile_contract,
    main,
)
from runner import cli as benchmark_cli


class FrozenInputTests(unittest.TestCase):
    def test_default_profile_and_shared_policy_bind_exact_declared_artifacts(
        self,
    ) -> None:
        prepared = load_profile_contract(DEFAULT_PROFILE)
        self.assertEqual(
            prepared["policy"]["policyId"], "openprose-local-sentinel-smoke"
        )
        self.assertFalse(prepared["profile"]["releaseEligible"])
        self.assertEqual(prepared["profile"]["semanticStatus"], "not-applicable")
        targets = prepared["verificationContract"]["targetArtifacts"]
        self.assertEqual(
            {target["id"] for target in targets},
            {"rust-development", "bun-development"},
        )
        self.assertTrue(all(len(target["sha256"]) == 64 for target in targets))
        self.assertTrue(all(target["bytes"] > 0 for target in targets))
        declared = {
            target["id"]: target
            for target in json.loads(DEFAULT_PROFILE.read_bytes())["targets"]
        }
        for target in targets:
            self.assertEqual(target["sha256"], declared[target["id"]]["artifactSha256"])
            self.assertEqual(target["bytes"], declared[target["id"]]["artifactBytes"])
        fixture = prepared["verificationContract"]["fixtureArtifact"]
        self.assertEqual(
            fixture["sha256"],
            declared_fixture := json.loads(DEFAULT_PROFILE.read_bytes())["fixture"][
                "sha256"
            ],
        )
        self.assertEqual(
            fixture["bytes"],
            json.loads(DEFAULT_PROFILE.read_bytes())["fixture"]["bytes"],
        )
        self.assertEqual(len(declared_fixture), 64)

    def test_evidence_contract_is_independent_of_mutable_build_output_presence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
            profile["policyArtifact"] = str(
                BENCHMARKS / "policy/local-smoke.policy.json"
            )
            profile["image"]["manifestArtifact"] = str(
                BENCHMARKS.parent / "shared/image/sentinel-v1/manifest.json"
            )
            profile["program"]["artifact"] = str(
                BENCHMARKS / "programs/sentinel-transport.v1.json"
            )
            profile["validator"]["artifact"] = str(
                BENCHMARKS / "validators/runner-result-shape.v1.json"
            )
            profile["fixture"]["path"] = str(
                BENCHMARKS.parent / "conformance/fake-harness/fake_harness.py"
            )
            for target in profile["targets"]:
                target["artifact"] = str(temporary / f"missing-{target['id']}")
            profile_path = temporary / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")

            contract = load_profile_contract(profile_path)
            self.assertEqual(
                {
                    entry["id"]
                    for entry in contract["verificationContract"]["targetArtifacts"]
                },
                {"rust-development", "bun-development"},
            )
            with self.assertRaisesRegex(ValueError, "artifact .*unavailable"):
                load_and_verify(profile_path)

    def test_target_artifact_digest_mismatch_refuses_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
            profile["policyArtifact"] = str(
                BENCHMARKS / "policy/local-smoke.policy.json"
            )
            profile["image"]["manifestArtifact"] = str(
                BENCHMARKS.parent / "shared/image/sentinel-v1/manifest.json"
            )
            manifest_path = Path(profile["image"]["manifestArtifact"])
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            profile["image"]["manifestSha256"] = hashlib.sha256(
                manifest_bytes
            ).hexdigest()
            profile["image"]["sha256"] = manifest["aggregateSha256"]["sha256"]
            profile["program"]["artifact"] = str(
                BENCHMARKS / "programs/sentinel-transport.v1.json"
            )
            profile["validator"]["artifact"] = str(
                BENCHMARKS / "validators/runner-result-shape.v1.json"
            )
            profile["fixture"]["path"] = str(
                BENCHMARKS.parent / "conformance/fake-harness/fake_harness.py"
            )
            for target in profile["targets"]:
                target["artifact"] = str(
                    (DEFAULT_PROFILE.parent / target["artifact"]).resolve()
                )
                target["artifactBytes"] = Path(target["artifact"]).stat().st_size
            profile["targets"][0]["artifactSha256"] = "0" * 64
            profile_path = temporary / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact .*digest mismatch"):
                load_and_verify(profile_path)

    def test_fixture_digest_mismatch_refuses_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
            profile["policyArtifact"] = str(
                BENCHMARKS / "policy/local-smoke.policy.json"
            )
            profile["image"]["manifestArtifact"] = str(
                BENCHMARKS.parent / "shared/image/sentinel-v1/manifest.json"
            )
            profile["program"]["artifact"] = str(
                BENCHMARKS / "programs/sentinel-transport.v1.json"
            )
            profile["validator"]["artifact"] = str(
                BENCHMARKS / "validators/runner-result-shape.v1.json"
            )
            profile["fixture"]["path"] = str(
                BENCHMARKS.parent / "conformance/fake-harness/fake_harness.py"
            )
            profile["fixture"]["sha256"] = "0" * 64
            for target in profile["targets"]:
                target["artifact"] = str(
                    (DEFAULT_PROFILE.parent / target["artifact"]).resolve()
                )
            profile_path = temporary / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fixture digest mismatch"):
                load_profile_contract(profile_path)

    def test_private_snapshots_are_isolated_and_finally_reauthenticated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            fixture = temporary / "fixture.py"
            fixture.write_bytes(b"fixture-v1\n")
            chmod(fixture, 0o700)
            targets = []
            target_contracts = []
            for name, body in (("rust", b"rust-v1\n"), ("bun", b"bun-v1\n")):
                source = temporary / name
                source.write_bytes(body)
                chmod(source, 0o700)
                digest = hashlib.sha256(body).hexdigest()
                targets.append(
                    {
                        "id": name,
                        "_sourceArtifact": str(source),
                        "artifactSha256": digest,
                        "artifactBytes": len(body),
                    }
                )
                target_contracts.append(
                    {"id": name, "sha256": digest, "bytes": len(body)}
                )
            fixture_bytes = fixture.read_bytes()
            prepared = {
                "fixture": fixture,
                "profile": {"targets": targets},
                "verificationContract": {
                    "fixtureArtifact": {
                        "sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                        "bytes": len(fixture_bytes),
                    },
                    "targetArtifacts": target_contracts,
                },
            }

            custody = _snapshot_execution_inputs(prepared, temporary / "private")
            snapshot_paths = [
                Path(target["_executionArtifact"])
                for target in prepared["profile"]["targets"]
            ]
            self.assertTrue(
                all(
                    path.parent == temporary / "private" / "targets"
                    for path in snapshot_paths
                )
            )
            self.assertEqual(prepared["fixture"].parent, temporary / "private")

            fixture.write_bytes(b"fixture-v2\n")
            for target in targets:
                Path(target["_sourceArtifact"]).write_bytes(b"source-mutated\n")
            final = _reauthenticate_execution_inputs(custody)
            self.assertEqual(final["status"], "pass")
            self.assertEqual(final["finalReauthentication"], "pass")

            chmod(snapshot_paths[0], 0o700)
            snapshot_paths[0].write_bytes(b"snapshot-mutated\n")
            with self.assertRaisesRegex(ValueError, "snapshot.*changed"):
                _reauthenticate_execution_inputs(custody)

    def test_collection_refuses_mutated_snapshot_before_writing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
            profile["policyArtifact"] = str(
                BENCHMARKS / "policy/local-smoke.policy.json"
            )
            for identity, path in (
                (
                    profile["image"],
                    BENCHMARKS.parent / "shared/image/sentinel-v1/manifest.json",
                ),
                (
                    profile["program"],
                    BENCHMARKS / "programs/sentinel-transport.v1.json",
                ),
                (
                    profile["validator"],
                    BENCHMARKS / "validators/runner-result-shape.v1.json",
                ),
            ):
                identity[
                    "manifestArtifact" if identity is profile["image"] else "artifact"
                ] = str(path)
            profile["fixture"]["path"] = str(
                BENCHMARKS.parent / "conformance/fake-harness/fake_harness.py"
            )
            for target in profile["targets"]:
                source = temporary / target["id"]
                source.write_bytes(b"#!/bin/sh\nexit 0\n")
                chmod(source, 0o700)
                body = source.read_bytes()
                target["artifact"] = str(source)
                target["artifactSha256"] = hashlib.sha256(body).hexdigest()
                target["artifactBytes"] = len(body)
            profile_path = temporary / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            output = temporary / "evidence"

            def mutate_snapshot(resolved_profile, *_args, **_kwargs):
                snapshot = Path(resolved_profile["targets"][0]["_executionArtifact"])
                chmod(snapshot, 0o700)
                snapshot.write_bytes(b"changed-during-run\n")
                return {}

            with mock.patch("runner.cli.run_benchmark", side_effect=mutate_snapshot):
                with self.assertRaisesRegex(ValueError, "snapshot.*changed"):
                    collect(profile_path, output, overwrite=False)
            self.assertFalse((output / "raw.json").exists())

    def test_collection_executes_only_snapshot_and_records_final_custody(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
            profile["policyArtifact"] = str(
                BENCHMARKS / "policy/local-smoke.policy.json"
            )
            profile["image"]["manifestArtifact"] = str(
                BENCHMARKS.parent / "shared/image/sentinel-v1/manifest.json"
            )
            profile["program"]["artifact"] = str(
                BENCHMARKS / "programs/sentinel-transport.v1.json"
            )
            profile["validator"]["artifact"] = str(
                BENCHMARKS / "validators/runner-result-shape.v1.json"
            )
            profile["fixture"]["path"] = str(
                BENCHMARKS.parent / "conformance/fake-harness/fake_harness.py"
            )
            profile["actions"] = [
                {
                    "id": "snapshot-version",
                    "scorecard": "dx",
                    "cacheState": "cold",
                    "expectation": "version",
                    "argv": ["--version"],
                }
            ]
            source = temporary / "candidate"
            source.write_bytes(
                b"#!/bin/sh\n"
                b'case "$0" in\n'
                b"  */execution-custody/targets/*) echo 'prose snapshot-fixture' ;;\n"
                b"  *) exit 9 ;;\n"
                b"esac\n"
            )
            chmod(source, 0o700)
            source_bytes = source.read_bytes()
            target = profile["targets"][0]
            target["artifact"] = str(source)
            target["artifactSha256"] = hashlib.sha256(source_bytes).hexdigest()
            target["artifactBytes"] = len(source_bytes)
            profile["targets"] = [target]
            profile_path = temporary / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            output = temporary / "evidence"

            with redirect_stdout(StringIO()):
                result = collect(profile_path, output, overwrite=False)
            self.assertEqual(result, 0)
            raw = json.loads((output / "raw.json").read_bytes())
            self.assertTrue(
                all(trial["status"] == "success" for trial in raw["trials"])
            )
            custody = raw["inputVerification"]["executionCustody"]
            self.assertEqual(custody["status"], "pass")
            self.assertEqual(custody["initialAdmission"], "pass")
            self.assertEqual(custody["finalReauthentication"], "pass")
            self.assertEqual(
                custody["targetArtifacts"],
                raw["inputVerification"]["targetArtifacts"],
            )

    def test_policy_digest_mismatch_refuses_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
            profile["policyArtifactSha256"] = "0" * 64
            profile_path = temporary / "profile.json"
            policy_source = BENCHMARKS / "policy" / "local-smoke.policy.json"
            (temporary / profile["policyArtifact"]).write_bytes(
                policy_source.read_bytes()
            )
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "policy digest"):
                load_and_verify(profile_path)


class PublicationTests(unittest.TestCase):
    def test_overwrite_refuses_existing_evidence_before_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            output = Path(temporary_text) / "evidence"
            output.mkdir()
            original = {
                "raw.json": b"old raw\n",
                "summary.json": b"old summary\n",
                "manifest.json": b"old manifest\n",
            }
            for name, value in original.items():
                (output / name).write_bytes(value)

            with mock.patch("runner.cli.load_and_verify") as loader:
                with self.assertRaisesRegex(ValueError, "immutable"):
                    collect(Path("unused-profile.json"), output, overwrite=True)
            loader.assert_not_called()
            self.assertEqual(
                {name: (output / name).read_bytes() for name in original}, original
            )

    def test_failed_staging_write_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            parent = Path(temporary_text)
            output = parent / "evidence"
            artifacts = {
                "raw.json": b"new raw\n",
                "summary.json": b"new summary\n",
                "manifest.json": b"new manifest\n",
            }
            original = benchmark_cli._write_exclusive_file
            calls = 0

            def fail_second(path, value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected publication failure")
                return original(path, value)

            with mock.patch.object(
                benchmark_cli, "_write_exclusive_file", side_effect=fail_second
            ):
                with self.assertRaisesRegex(OSError, "injected"):
                    _publish_evidence_directory(output, artifacts, overwrite=False)
            self.assertFalse(output.exists())
            self.assertEqual(list(parent.glob(".evidence.staging-*")), [])

    def test_destination_race_never_replaces_adversarial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            parent = Path(temporary_text)
            output = parent / "evidence"
            artifacts = {
                "raw.json": b"new raw\n",
                "summary.json": b"new summary\n",
                "manifest.json": b"new manifest\n",
            }
            original = benchmark_cli._rename_directory_noreplace

            def race(source, destination):
                destination.mkdir()
                (destination / "adversarial-marker").write_bytes(b"preserve")
                return original(source, destination)

            with mock.patch.object(
                benchmark_cli, "_rename_directory_noreplace", side_effect=race
            ):
                with self.assertRaisesRegex(ValueError, "appeared"):
                    _publish_evidence_directory(output, artifacts, overwrite=False)
            self.assertEqual((output / "adversarial-marker").read_bytes(), b"preserve")
            self.assertEqual(list(parent.glob(".evidence.staging-*")), [])

    def test_nonregular_and_symlink_evidence_destinations_are_never_followed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            parent = Path(temporary_text)
            victim = parent / "victim"
            victim.mkdir()
            marker = victim / "marker"
            marker.write_bytes(b"preserve me")
            output = parent / "evidence"
            output.symlink_to(victim, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlink|non-regular|immutable"):
                _publish_evidence_directory(
                    output,
                    {
                        "raw.json": b"raw",
                        "summary.json": b"summary",
                        "manifest.json": b"manifest",
                    },
                    overwrite=True,
                )
            self.assertTrue(output.is_symlink())
            self.assertEqual(marker.read_bytes(), b"preserve me")

            summary = parent / "summary.json"
            summary.unlink(missing_ok=True)
            summary.symlink_to(marker)
            with self.assertRaisesRegex(ValueError, "regular file"):
                _write_atomic_file(summary, b"replacement", replace=True)
            self.assertTrue(summary.is_symlink())
            self.assertEqual(marker.read_bytes(), b"preserve me")

    def test_atomic_file_failure_preserves_existing_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            summary = Path(temporary_text) / "summary.json"
            summary.write_bytes(b"old summary\n")
            with mock.patch("runner.cli.os.replace", side_effect=OSError("injected")):
                with self.assertRaisesRegex(OSError, "injected"):
                    _write_atomic_file(summary, b"new summary\n", replace=True)
            self.assertEqual(summary.read_bytes(), b"old summary\n")
            self.assertEqual(list(summary.parent.glob(".summary.json.staging-*")), [])
            _write_atomic_file(summary, b"new summary\n", replace=True)
            self.assertEqual(summary.read_bytes(), b"new summary\n")
            self.assertEqual(list(summary.parent.glob(".summary.json.staging-*")), [])


class ExampleEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = BENCHMARKS / "evidence" / "local-smoke-example"
        cls.raw_bytes = (cls.evidence / "raw.json").read_bytes()
        cls.raw = json.loads(cls.raw_bytes)
        cls.summary = json.loads((cls.evidence / "summary.json").read_bytes())
        cls.manifest = json.loads((cls.evidence / "manifest.json").read_bytes())

    def test_checked_example_is_bound_to_the_exact_frozen_development_inputs(
        self,
    ) -> None:
        self.assertFalse((self.evidence / "STALE.md").exists())
        prepared = load_profile_contract(DEFAULT_PROFILE)
        verification = self.raw["inputVerification"]
        self.assertEqual(
            verification["schema"], "openprose.benchmark-input-verification/2"
        )
        self.assertEqual(verification["status"], "historical-migrated")
        self.assertEqual(
            verification["fixtureArtifact"],
            prepared["verificationContract"]["fixtureArtifact"],
        )
        self.assertEqual(
            verification["targetArtifacts"],
            prepared["verificationContract"]["targetArtifacts"],
        )
        self.assertEqual(
            verification["profileArtifactSha256"], self.raw["profile"]["sha256"]
        )
        custody = verification["executionCustody"]
        self.assertEqual(custody["status"], "not-observed")
        self.assertEqual(custody["finalReauthentication"], "not-performed")
        self.assertEqual(custody["targetArtifacts"], verification["targetArtifacts"])
        migration = verification["migration"]
        self.assertEqual(
            migration["schema"], "openprose.benchmark-evidence-migration/1"
        )
        self.assertEqual(
            migration["sourceEvidenceCommit"],
            "fce8933aab58d9453fe9c2f67fdffc2fc482fe58",
        )
        self.assertEqual(
            migration["sourceRawArtifactSha256"],
            "320fea90851752be09fe96b3dfc21d70b3220c4bbe0f56d1d2517caf6423e369",
        )
        self.assertFalse(migration["trialObservationsChanged"])
        frozen_targets = {
            target["id"]: (target["sha256"], target["bytes"])
            for target in verification["targetArtifacts"]
        }
        recorded_targets = {
            target["id"]: (target["artifactSha256"], target["artifactBytes"])
            for target in self.raw["identities"]["targets"]
        }
        self.assertEqual(recorded_targets, frozen_targets)

    def test_evidence_is_complete_provider_free_and_explicitly_non_release(
        self,
    ) -> None:
        self.assertEqual(self.raw["plannedTrials"], 80)
        self.assertEqual(self.raw["plannedTrials"], len(self.raw["trials"]))
        self.assertTrue(
            all(trial["status"] == "success" for trial in self.raw["trials"])
        )
        self.assertTrue(
            all(
                trial["processEvidence"]["settlement"]["status"] == "settled"
                for trial in self.raw["trials"]
            )
        )
        for trial in self.raw["trials"]:
            self.assertEqual(
                trial["executionBoundary"],
                {"kind": "subprocess", "processEvidenceRequired": True},
            )
            evidence = trial["processEvidence"]
            self.assertEqual(
                evidence["containment"]["authority"], "owned-process-group"
            )
            self.assertFalse(
                evidence["containment"]["releaseContainmentSupported"]
            )
            self.assertEqual(
                evidence["containment"]["blocker"],
                "detached-descendant-containment-not-enforced",
            )
            settlement = evidence["settlement"]
            self.assertTrue(settlement["allowsSuccessfulTrial"])
            self.assertTrue(settlement["directProcessExited"])
            self.assertTrue(settlement["stdoutReaderSettled"])
            self.assertTrue(settlement["stderrReaderSettled"])
            self.assertEqual(settlement["readerErrors"], [])
            self.assertIn(settlement["cleanup"]["status"], {"not-required", "settled"})
        process_execution = self.summary["scorecards"]["transport"][
            "processExecution"
        ]
        for target in process_execution["targets"].values():
            self.assertFalse(target["releaseContainmentSupported"])
            self.assertEqual(
                target["containment"]["blocker"],
                "detached-descendant-containment-not-enforced",
            )
        self.assertFalse(self.raw["claims"]["releaseEligible"])
        self.assertTrue(self.raw["claims"]["sentinelOnly"])
        self.assertEqual(self.raw["claims"]["semanticStatus"], "not-applicable")
        self.assertFalse(self.raw["claims"]["proseComplete"])
        self.assertFalse(self.manifest["releaseEligible"])
        self.assertFalse(self.manifest["providerCallsMade"])
        self.assertEqual(self.manifest["semanticStatus"], "not-applicable")
        self.assertTrue(self.manifest["sentinelOnly"])
        semantic = self.summary["scorecards"]["semanticQuality"]
        self.assertEqual(semantic["status"], "not-applicable")
        self.assertFalse(semantic["surfaces"]["wrapper"]["evaluated"])
        self.assertFalse(semantic["surfaces"]["wrapper"]["proseComplete"])

    def test_doctor_requires_canonical_schema_and_failures_remain_raw(self) -> None:
        doctors = [
            trial for trial in self.raw["trials"] if trial["action_id"] == "doctor"
        ]
        self.assertTrue(doctors)
        for trial in doctors:
            schema = json.loads(trial["stdout"])["schema"]
            self.assertEqual(schema, "openprose.doctor-report/1")
            self.assertEqual(trial["status"], "success")
            self.assertEqual(trial["validation"]["status"], "passed")

        transport_runs = [
            trial
            for trial in self.raw["trials"]
            if trial["action_id"] == "transport-run"
        ]
        self.assertTrue(transport_runs)
        for trial in transport_runs:
            result = json.loads(trial["stdout"])
            self.assertEqual(result["schema"], "openprose.runner-result/1")
            self.assertEqual(result["semantic"]["status"], "not-applicable")
            self.assertEqual(trial["validation"]["status"], "passed")

    def test_scorecards_are_separate_and_never_collapse_a_winner(self) -> None:
        self.assertEqual(
            set(self.summary["scorecards"]),
            {
                "transport",
                "developerExperience",
                "agentEfficiency",
                "cost",
                "semanticQuality",
            },
        )
        self.assertFalse(self.summary["rules"]["winnerCollapsed"])
        self.assertEqual(
            set(self.summary["scorecards"]["transport"]["surfaces"]), {"wrapper"}
        )
        self.assertFalse(
            self.summary["scorecards"]["semanticQuality"]["surfaces"]["wrapper"][
                "evaluated"
            ]
        )
        self.assertTrue(
            all(
                target["estimatedOrImputedUsd"] is None
                for target in self.summary["scorecards"]["cost"]["targets"].values()
            )
        )
        process_execution = self.summary["scorecards"]["transport"]["processExecution"]
        self.assertEqual(process_execution["status"], "recorded")
        self.assertTrue(
            all(
                target["allExecutedTrialsSettled"]
                for target in process_execution["targets"].values()
            )
        )
        for surface in (
            self.summary["scorecards"]["transport"]["surfaces"],
            self.summary["scorecards"]["developerExperience"]["surfaces"],
        ):
            for action in surface["wrapper"]["actions"].values():
                for target in action["targets"].values():
                    self.assertIn("successLatencyMs", target)
                    self.assertIn("allAttemptDurationMs", target)
                    self.assertNotIn("wallMs", target)
                for comparison in action["pairedDifferencesMs"]:
                    self.assertEqual(
                        comparison["samples"], comparison["eligibleSuccessPairs"]
                    )
                    self.assertEqual(
                        comparison["plannedPairs"],
                        comparison["eligibleSuccessPairs"]
                        + comparison["excludedPairs"],
                    )

    def test_manifest_digests_and_analysis_are_reproducible(self) -> None:
        manifest_entries = {
            entry["path"]: entry for entry in self.manifest["artifacts"]
        }
        for name in ("raw.json", "summary.json"):
            value = (self.evidence / name).read_bytes()
            self.assertEqual(
                hashlib.sha256(value).hexdigest(), manifest_entries[name]["sha256"]
            )
            self.assertEqual(len(value), manifest_entries[name]["bytes"])
        with tempfile.TemporaryDirectory() as temporary_text:
            reproduced = Path(temporary_text) / "summary.json"
            result = main(
                [
                    "analyze",
                    "--raw",
                    str(self.evidence / "raw.json"),
                    "--policy",
                    str(BENCHMARKS / "policy" / "local-smoke.policy.json"),
                    "--summary",
                    str(reproduced),
                ]
            )
            self.assertEqual(result, 0)
            self.assertEqual(
                reproduced.read_bytes(), (self.evidence / "summary.json").read_bytes()
            )

    def test_evidence_contains_no_local_absolute_paths_or_secret_shapes(self) -> None:
        encoded = self.raw_bytes.decode("utf-8")
        for forbidden in (
            "/Users/",
            "Authorization: Bearer ",
            "sk-live-",
            "API_KEY=secret",
        ):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()
