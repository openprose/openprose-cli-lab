#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "openprose_direct_skill_real", HERE / "run.py"
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class DirectSkillRealRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = runner.read_json(HERE / "policy.v1.json")
        self.frozen_matrix = runner.read_json(HERE / "matrix.v1.json")
        self.matrix = deepcopy(self.frozen_matrix)
        self.fake = HERE / "fake_prime_agent.py"
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        skill_root = Path(fixture.name) / "portable-skill"
        (skill_root / "state").mkdir(parents=True)
        (skill_root / "SKILL.md").write_text("# Test skill\n", "utf-8")
        (skill_root / "prose.md").write_text("opaque language notes\n", "utf-8")
        (skill_root / "state/filesystem.md").write_text("opaque state notes\n", "utf-8")
        self.snapshot = runner.snapshot_skill_tree(skill_root)
        self.matrix["skill"].update(
            {
                "expectedTreeSha256": self.snapshot.tree_sha256,
                "expectedFileCount": self.snapshot.file_count,
                "expectedTotalBytes": self.snapshot.total_bytes,
                "relevantFiles": list(self.snapshot.files),
            }
        )

    def test_frozen_inputs_are_budgeted_non_admitting_and_match_exact_sources(
        self,
    ) -> None:
        runner.validate_frozen_inputs(self.policy, self.matrix, self.snapshot)
        self.assertEqual(
            2.0,
            sum(route["plannedTotalCostCeilingUsd"] for route in self.matrix["routes"]),
        )
        self.assertEqual(
            "observation-only",
            self.matrix["claims"]["exploratoryOldSkillCompatibility"],
        )
        self.assertEqual("unknown", self.matrix["claims"]["currentSkillCompatibility"])
        self.assertFalse(self.matrix["claims"]["interactiveTuiObserved"])
        self.assertFalse(self.matrix["claims"]["releaseEligible"])

    def test_default_validate_uses_declarations_and_retained_evidence_only(
        self,
    ) -> None:
        args = argparse.Namespace(
            policy=HERE / "policy.v1.json",
            matrix=HERE / "matrix.v1.json",
            evidence=HERE / "evidence/current",
            verify_live_inputs=False,
        )
        with mock.patch.object(
            runner,
            "snapshot_skill_tree",
            side_effect=AssertionError("private skill must not be read"),
        ), mock.patch.object(
            runner,
            "observe_version",
            side_effect=AssertionError("installed Prime must not run"),
        ):
            self.assertEqual(0, runner.command_validate(args))

    def test_public_inputs_are_location_free_and_keep_historical_binding(self) -> None:
        serialized = json.dumps(self.frozen_matrix, sort_keys=True)
        self.assertNotIn("sourcePath", self.frozen_matrix["skill"])
        self.assertNotIn("treeRoot", self.frozen_matrix["skill"])
        self.assertNotIn("executable", self.frozen_matrix["harness"])
        self.assertNotIn("/" + "Users/sl/", serialized)
        historical = {
            runner.read_json(path)["matrixSha256"]
            for path in (HERE / "evidence/current").glob("*.evidence.json")
        }
        self.assertEqual(
            {self.frozen_matrix["retainedEvidenceMatrixSha256"]}, historical
        )
        self.assertNotEqual(
            self.frozen_matrix["retainedEvidenceMatrixSha256"],
            runner.sha256_bytes(runner.canonical_json(self.frozen_matrix)),
        )
        run = next(
            action
            for action in runner.parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        ).choices["run"]
        actions = {action.dest: action for action in run._actions}
        self.assertTrue(actions["executable"].required)
        self.assertTrue(actions["skill_root"].required)
        self.assertIsNone(actions["env_file"].default)

    def test_live_validation_requires_exact_explicit_absolute_inputs(self) -> None:
        base = argparse.Namespace(
            policy=HERE / "policy.v1.json",
            matrix=HERE / "matrix.v1.json",
            evidence=HERE / "evidence/current",
            verify_live_inputs=True,
            executable=None,
            skill_root=None,
        )
        with self.assertRaisesRegex(runner.ConfigurationError, "requires"):
            runner.command_validate(base)
        base.executable = Path("prime-agent")
        base.skill_root = Path("legacy-skill")
        with self.assertRaisesRegex(runner.ConfigurationError, "absolute"):
            runner.command_validate(base)

    def test_run_never_discovers_ambient_credentials_or_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".env").write_text(
                "OPENROUTER_API_KEY=ambient-file-secret\n", "utf-8"
            )
            args = argparse.Namespace(
                i_understand_this_spends_money=True,
                i_understand_this_uses_a_legacy_skill=True,
                policy=HERE / "policy.v1.json",
                matrix=HERE / "matrix.v1.json",
                evidence=root / "new-evidence",
                env_file=None,
                executable=self.fake.resolve(),
                skill_root=self.snapshot.root,
            )
            with mock.patch.dict(
                os.environ,
                {"OPENROUTER_API_KEY": "ambient-process-secret"},
            ), mock.patch.object(
                runner,
                "prepare_harness_execution_custody",
                side_effect=AssertionError("must fail before executable custody"),
            ):
                with self.assertRaisesRegex(
                    runner.ConfigurationError, "explicit --env-file"
                ):
                    runner.command_run(args)
            self.assertFalse(args.evidence.exists())
        with self.assertRaisesRegex(runner.ConfigurationError, "absolute"):
            runner.parse_dotenv_selected(Path(".env"), ["OPENROUTER_API_KEY"])

    def test_public_diagnostics_redact_explicit_local_input_paths(self) -> None:
        executable = Path("/private/operator/bin/prime-agent")
        skill = Path("/private/operator/skills/open-prose/SKILL.md")
        env_file = Path("/private/operator/credentials.env")
        raw = json.dumps(
            {
                "executable": str(executable),
                "skill": str(skill),
                "credentials": str(env_file),
            }
        ).encode()
        sanitized = runner.bounded_sanitize(
            raw,
            prompt="not present",
            secrets=[],
            workspace=Path("/disposable/workspace"),
            maximum_bytes=4096,
            private_paths=(executable, skill, env_file),
        )
        self.assertNotIn("/private/operator", sanitized)
        self.assertEqual(3, sanitized.count("[PRIVATE_PATH]"))

    def test_default_validate_rejects_contradictory_derived_pass_fields(
        self,
    ) -> None:
        evidence, _ = self.run_fake()
        evidence["harness"]["executableSha256"] = self.matrix["harness"][
            "expectedExecutableSha256"
        ]
        evidence["toolAudit"].update(
            {
                "rlmRunCallsObserved": 0,
                "directSubagentStartEvents": 0,
                "subagentStartsObserved": 0,
            }
        )
        evidence["workspace"].update(
            {
                "effectsPassed": True,
                "subagentExecutionEvidenceCount": 0,
                "subagentExecutionProven": True,
                "unexpectedEffectPaths": ["adversarial.txt"],
            }
        )
        evidence["observation"]["classification"] = "old-skill-effect-pass"

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(self.matrix), "utf-8")
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            evidence_path = evidence_dir / "contradictory.evidence.json"
            evidence_path.write_text(json.dumps(evidence), "utf-8")
            before = evidence_path.read_bytes()
            args = argparse.Namespace(
                policy=HERE / "policy.v1.json",
                matrix=matrix_path,
                evidence=evidence_dir,
                verify_live_inputs=False,
            )

            with self.assertRaisesRegex(runner.ConfigurationError, "analyzer-owned"):
                runner.command_validate(args)
            self.assertEqual(before, evidence_path.read_bytes())

    def test_textual_rlm_reference_cannot_prove_observed_subagent_execution(
        self,
    ) -> None:
        audit = runner.tool_audit(
            [
                {
                    "type": "tool_execution_start",
                    "toolName": "ipython",
                    "args": {"code": "if False:\n    await rlm.run('never')"},
                }
            ]
        )
        self.assertEqual(1, audit["rlmRunCallsObserved"])
        self.assertEqual(0, audit["directSubagentStartEvents"])
        self.assertEqual(0, audit["subagentStartsObserved"])

        evidence, _ = self.run_fake(mode="text-only-subagent")
        self.assertTrue(evidence["workspace"]["effectsPassed"])
        self.assertEqual(1, evidence["toolAudit"]["rlmRunCallsObserved"])
        self.assertEqual(0, evidence["toolAudit"]["subagentStartsObserved"])
        self.assertFalse(evidence["workspace"]["subagentExecutionProven"])
        self.assertEqual(
            "filesystem-effect-pass-subagent-unproven",
            evidence["observation"]["classification"],
        )

    def test_default_validate_rejects_tampered_derived_reports(self) -> None:
        source = HERE / "evidence/current"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for path in source.iterdir():
                if path.is_file():
                    (root / path.name).write_bytes(path.read_bytes())
            args = argparse.Namespace(
                policy=HERE / "policy.v1.json",
                matrix=HERE / "matrix.v1.json",
                evidence=root,
                verify_live_inputs=False,
            )
            self.assertEqual(0, runner.command_validate(args))
            for name in ("report.json", "REPORT.md"):
                with self.subTest(name=name):
                    original = (root / name).read_bytes()
                    (root / name).write_bytes(original + b"\nTAMPERED\n")
                    with self.assertRaisesRegex(
                        runner.ConfigurationError, "derived report drift"
                    ):
                        runner.command_validate(args)
                    (root / name).write_bytes(original)

    def test_optional_live_validation_checks_exact_local_skill_and_harness(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            matrix = deepcopy(self.matrix)
            matrix["harness"]["expectedExecutableSha256"] = runner.file_digest(
                self.fake
            )
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(matrix), "utf-8")
            evidence = root / "evidence"
            evidence.mkdir()
            args = argparse.Namespace(
                policy=HERE / "policy.v1.json",
                matrix=matrix_path,
                evidence=evidence,
                verify_live_inputs=True,
                executable=self.fake.resolve(),
                skill_root=self.snapshot.root,
            )
            self.assertEqual(0, runner.command_validate(args))

    def test_execution_custody_uses_snapshots_after_sources_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            harness_root = root / "harness"
            harness_root.mkdir()
            source_executable = harness_root / "fake-prime"
            source_executable.write_bytes(self.fake.read_bytes())
            source_executable.chmod(0o700)
            executable_sha256 = runner.file_digest(source_executable)
            matrix = deepcopy(self.matrix)
            matrix["harness"]["expectedExecutableSha256"] = executable_sha256
            custody = runner.prepare_harness_execution_custody(
                source_executable, executable_sha256, root / "custody/harness"
            )
            skill_snapshot = runner.snapshot_tree_for_execution(
                self.snapshot.root, root / "custody/skill", "skill tree"
            )
            runner.validate_skill_snapshot_identity(matrix, skill_snapshot)

            source_executable.write_text(
                "#!/usr/bin/env python3\nraise SystemExit(91)\n", "utf-8"
            )
            (self.snapshot.root / "SKILL.md").write_text("mutated\n", "utf-8")

            version = runner.observe_version(
                custody.executable,
                runner.isolated_environment({}),
                custody.launch_prefix,
            )
            self.assertEqual("0.7.0", version)
            observation = root / "observation.json"
            env_file = root / "credentials.env"
            env_file.write_text("OPENROUTER_API_KEY=fake-secret\n", "utf-8")
            route = deepcopy(matrix["routes"][0])
            route["credentialEnvironment"] = ["OPENROUTER_API_KEY"]
            evidence = runner.run_trial(
                executable=custody.executable,
                executable_sha256=executable_sha256,
                observed_version=version,
                skill_snapshot=skill_snapshot,
                policy=self.policy,
                matrix=matrix,
                route=route,
                program=matrix["programs"][0],
                trial=1,
                env_file=env_file,
                executable_prefix=custody.launch_prefix,
                skill_source_path=skill_snapshot.root / "SKILL.md",
                execution_custody=custody.evidence,
                extra_environment={
                    "OPENPROSE_DIRECT_SKILL_TEST_OBSERVATION": str(observation)
                },
            )
            observed = json.loads(observation.read_text("utf-8"))
            used_skill = observed["argv"][observed["argv"].index("--skill") + 1]
            self.assertEqual(str(skill_snapshot.root / "SKILL.md"), used_skill)
            self.assertNotEqual(str(self.snapshot.root / "SKILL.md"), used_skill)
            self.assertEqual(
                executable_sha256,
                evidence["harness"]["executionCustody"]["entrySha256"],
            )
            self.assertTrue(
                evidence["harness"]["executionCustody"]["exactSnapshottedBytesExecuted"]
            )
            self.assertTrue(
                evidence["harness"]["executionCustody"]["interpreter"]["snapshotted"]
            )
            self.assertFalse(
                evidence["harness"]["executionCustody"]["dynamicDependenciesClosed"]
            )
            runner.validate_evidence_against_frozen_declarations(
                evidence, self.policy, matrix
            )

    def test_source_or_budget_drift_is_rejected_before_execution(self) -> None:
        matrix = deepcopy(self.matrix)
        matrix["routes"][0]["plannedTotalCostCeilingUsd"] = 0.61
        with self.assertRaisesRegex(runner.ConfigurationError, "threshold"):
            runner.validate_frozen_inputs(self.policy, matrix, self.snapshot)
        matrix = deepcopy(self.matrix)
        matrix["skill"]["expectedTreeSha256"] = "0" * 64
        with self.assertRaisesRegex(runner.ConfigurationError, "skill tree drift"):
            runner.validate_frozen_inputs(self.policy, matrix, self.snapshot)

    def test_evidence_is_bound_to_the_frozen_route_harness_and_task(self) -> None:
        evidence, _ = self.run_fake()
        evidence["harness"]["executableSha256"] = self.matrix["harness"][
            "expectedExecutableSha256"
        ]
        runner.validate_evidence_against_frozen(
            evidence, self.policy, self.matrix, self.snapshot
        )
        evidence["route"]["model"] = "substituted/model"
        with self.assertRaisesRegex(runner.ConfigurationError, "route drift"):
            runner.validate_evidence_against_frozen(
                evidence, self.policy, self.matrix, self.snapshot
            )

    def run_fake(self, *, mode: str = "pass", program_index: int = 0):
        route = self.matrix["routes"][0]
        program = self.matrix["programs"][program_index]
        outer = tempfile.TemporaryDirectory()
        self.addCleanup(outer.cleanup)
        root = Path(outer.name)
        observation = root / "fake-observation.json"
        env_file = root / "credentials.env"
        env_file.write_text(
            "OPENROUTER_API_KEY=wanted-secret\n"
            "OPENAI_API_KEY=must-not-pass\n"
            "UNRELATED_SECRET=must-not-pass-either\n",
            "utf-8",
        )
        route = deepcopy(route)
        route["credentialEnvironment"] = ["OPENROUTER_API_KEY"]
        evidence = runner.run_trial(
            executable=self.fake,
            executable_sha256=runner.file_digest(self.fake),
            observed_version="0.7.0",
            skill_snapshot=self.snapshot,
            policy=self.policy,
            matrix=self.matrix,
            route=route,
            program=program,
            trial=1,
            env_file=env_file,
            extra_environment={
                "OPENPROSE_DIRECT_SKILL_TEST_OBSERVATION": str(observation),
                "FAKE_DIRECT_SKILL_MODE": mode,
            },
        )
        observed = (
            json.loads(observation.read_text("utf-8")) if observation.exists() else None
        )
        return evidence, observed

    def test_provider_free_trial_uses_explicit_skill_and_exact_isolation(self) -> None:
        evidence, observed = self.run_fake()
        argv = observed["argv"]
        for flag in (
            "--print",
            "--mode",
            "--no-session",
            "--no-builtin-tools",
            "--no-extensions",
            "--no-skills",
            "--skill",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
        ):
            self.assertIn(flag, argv)
        self.assertEqual("ipython", argv[argv.index("--tools") + 1])
        self.assertEqual(
            str(self.snapshot.root / "SKILL.md"), argv[argv.index("--skill") + 1]
        )
        self.assertFalse(Path(observed["cwd"]).exists())
        self.assertEqual(
            runner.sha256_bytes(runner.TELEMETRY_DISABLED_BYTES),
            observed["telemetryEnvSha256"],
        )
        self.assertEqual(
            "filesystem-effect-pass-subagent-unproven",
            evidence["observation"]["classification"],
        )
        self.assertTrue(evidence["workspace"]["effectsPassed"])
        self.assertFalse(evidence["workspace"]["subagentExecutionProven"])
        self.assertTrue(evidence["isolation"]["proseAbsentFromPath"])
        self.assertFalse(evidence["toolAudit"]["proseInvocationObserved"])
        self.assertFalse(evidence["toolAudit"]["legacyTelemetryAttemptObserved"])
        self.assertGreaterEqual(evidence["toolAudit"]["subagentStartsObserved"], 1)
        requested = self.matrix["routes"][0]
        self.assertEqual(
            {
                "provider": requested["provider"],
                "model": requested["model"],
                "api": "fake-api",
            },
            evidence["observation"]["reportedRoute"],
        )
        self.assertTrue(evidence["observation"]["requestedRouteReportedExactly"])
        self.assertNotIn("wanted-secret", json.dumps(evidence))
        self.assertNotIn("must-not-pass", json.dumps(evidence))
        self.assertNotIn("prose run program.prose", json.dumps(evidence))
        runner.validate_evidence(evidence)

    def test_fanout_effect_observes_starts_without_claiming_execution_proof(
        self,
    ) -> None:
        evidence, _ = self.run_fake(program_index=1)
        self.assertEqual(
            "filesystem-effect-pass-subagent-unproven",
            evidence["observation"]["classification"],
        )
        self.assertEqual(
            {"combined", "left", "right"}, set(evidence["workspace"]["bindings"])
        )
        self.assertGreaterEqual(evidence["toolAudit"]["subagentStartsObserved"], 3)
        self.assertFalse(evidence["workspace"]["subagentExecutionProven"])

    def test_cli_or_legacy_telemetry_attempt_is_a_policy_violation(self) -> None:
        for mode, field in (
            ("prose-invocation", "proseInvocationObserved"),
            ("telemetry", "legacyTelemetryAttemptObserved"),
        ):
            with self.subTest(mode=mode):
                evidence, _ = self.run_fake(mode=mode)
                self.assertEqual(
                    "policy-violation", evidence["observation"]["classification"]
                )
                self.assertTrue(evidence["toolAudit"][field])

    def test_failure_timeout_and_output_limit_are_bounded_and_sanitized(self) -> None:
        failure, _ = self.run_fake(mode="fail")
        self.assertEqual(
            "harness-unavailable", failure["observation"]["classification"]
        )
        self.assertIn("[REDACTED_SECRET]", failure["process"]["sanitizedStderr"])
        self.assertNotIn("fixture-super-secret", json.dumps(failure))

        original = self.policy
        self.policy = deepcopy(original)
        self.policy["limits"]["timeoutSecondsPerTrial"] = 1
        timeout, _ = self.run_fake(mode="timeout")
        self.assertEqual("timeout", timeout["observation"]["classification"])
        self.assertTrue(timeout["process"]["timedOut"])
        self.assertTrue(timeout["process"]["settlement"]["leaderReaped"])
        self.assertTrue(timeout["process"]["settlement"]["readersSettled"])
        self.assertFalse(
            timeout["process"]["settlement"]["detachedDescendantsContained"]
        )

        self.policy = deepcopy(original)
        self.policy["limits"]["maximumCapturedProcessBytes"] = 4096
        overflow, _ = self.run_fake(mode="large-output")
        self.assertEqual("output-limit", overflow["observation"]["classification"])
        self.assertTrue(overflow["process"]["outputLimitExceeded"])
        self.policy = original

    @unittest.skipIf(os.name == "nt", "POSIX process-group oracle")
    def test_base_exception_settles_child_and_grandchild(self) -> None:
        program = textwrap.dedent(
            """
            import os
            from pathlib import Path
            import sys
            import time

            child = os.fork()
            if child == 0:
                while True:
                    time.sleep(1)
            Path(sys.argv[1]).write_text(
                f"{os.getpid()} {child} {os.getpgrp()}", encoding="utf-8"
            )
            while True:
                time.sleep(1)
            """
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            identities = root / "identities"

            def interrupt_after_publish(process: object, timeout: float) -> int:
                del process, timeout
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if identities.exists() and len(identities.read_text().split()) == 3:
                        break
                    time.sleep(0.01)
                if not identities.exists() or len(identities.read_text().split()) != 3:
                    self.fail("fixture did not publish identities")
                raise KeyboardInterrupt

            with mock.patch.object(
                runner, "wait_for_process", side_effect=interrupt_after_publish
            ):
                with self.assertRaises(KeyboardInterrupt):
                    runner.run_bounded(
                        [sys.executable, "-c", program, str(identities)],
                        root,
                        runner.isolated_environment({}),
                        2,
                        1024,
                    )
            process_group = int(identities.read_text().split()[2])
            self.assertTrue(self._wait_for_group_absent(process_group))

    @unittest.skipIf(os.name == "nt", "POSIX detached-descendant oracle")
    def test_detached_pipe_holder_does_not_extend_absolute_deadline(self) -> None:
        program = textwrap.dedent(
            """
            import os
            from pathlib import Path
            import sys
            import time

            child = os.fork()
            if child == 0:
                os.setsid()
                Path(sys.argv[1]).write_text(
                    f"{os.getpid()} {os.getpgrp()}", encoding="utf-8"
                )
                while True:
                    time.sleep(1)
            raise SystemExit(0)
            """
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            identities = root / "detached"
            started = time.monotonic()
            observation = runner.run_bounded(
                [sys.executable, "-c", program, str(identities)],
                root,
                runner.isolated_environment({}),
                1,
                1024,
            )
            elapsed = time.monotonic() - started
            child_pid, child_group = [
                int(value) for value in identities.read_text().split()
            ]
            self.addCleanup(self._kill_validated_detached, child_pid, child_group)
        self.assertLess(elapsed, 1.5)
        self.assertTrue(observation.readers_settled)
        self.assertTrue(observation.streams_closed)
        self.assertFalse(observation.detached_descendants_contained)
        self.assertFalse(
            any(
                thread.name.startswith("direct-skill-real-")
                for thread in runner.threading.enumerate()
            )
        )

    def test_reported_route_mismatch_cannot_pass(self) -> None:
        evidence, _ = self.run_fake()
        events = [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "provider": "unexpected",
                    "model": "unexpected",
                },
            }
        ]
        self.assertEqual(
            {"provider": "unexpected", "model": "unexpected", "api": None},
            runner.observed_route(events),
        )
        evidence["observation"]["requestedRouteReportedExactly"] = False
        corrected = runner.reanalyze_evidence(evidence, self.matrix)
        self.assertEqual(
            "harness-route-mismatch", corrected["observation"]["classification"]
        )
        runner.validate_evidence(corrected)

    def test_schema_rejects_unknown_fields_and_wrong_nested_types(self) -> None:
        evidence, _ = self.run_fake()
        mutations = []
        extra_top = deepcopy(evidence)
        extra_top["unexpected"] = True
        mutations.append(extra_top)
        extra_nested = deepcopy(evidence)
        extra_nested["process"]["unexpected"] = True
        mutations.append(extra_nested)
        wrong_bool = deepcopy(evidence)
        wrong_bool["workspace"]["effectsPassed"] = 1
        mutations.append(wrong_bool)
        wrong_integer = deepcopy(evidence)
        wrong_integer["toolAudit"]["subagentStartsObserved"] = True
        mutations.append(wrong_integer)
        non_json_number = deepcopy(evidence)
        non_json_number["observation"]["cost"]["totalUsd"] = float("nan")
        mutations.append(non_json_number)
        wrong_dynamic_value = deepcopy(evidence)
        wrong_dynamic_value["toolAudit"]["toolNames"]["ipython"] = "1"
        mutations.append(wrong_dynamic_value)
        extra_binding = deepcopy(evidence)
        first_binding = next(iter(extra_binding["workspace"]["bindings"].values()))
        first_binding["unexpected"] = True
        mutations.append(extra_binding)
        wrong_file_identity = deepcopy(evidence)
        wrong_file_identity["skill"]["relevantFiles"][0]["byteLength"] = False
        mutations.append(wrong_file_identity)
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                with self.assertRaisesRegex(runner.ConfigurationError, "schema"):
                    runner.validate_evidence(mutation)

    def test_binding_artifacts_cannot_substitute_for_direct_subagent_evidence(
        self,
    ) -> None:
        evidence, _ = self.run_fake(program_index=1)
        evidence["toolAudit"]["rlmRunCallsObserved"] = 0
        evidence["toolAudit"]["directSubagentStartEvents"] = 0
        evidence["toolAudit"]["subagentStartsObserved"] = 0
        corrected = runner.reanalyze_evidence(evidence, self.matrix)
        self.assertTrue(corrected["workspace"]["effectsPassed"])
        self.assertFalse(corrected["workspace"]["subagentExecutionProven"])
        self.assertEqual(
            "filesystem-effect-pass-subagent-unproven",
            corrected["observation"]["classification"],
        )
        runner.validate_evidence(corrected)

    def test_workspace_symlinks_and_oversize_files_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.write_bytes(b"safe")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(runner.ConfigurationError):
                runner._read_workspace_file(link, 100)
            with self.assertRaisesRegex(runner.ConfigurationError, "byte limit"):
                runner._read_workspace_file(target, 3)

    def test_report_is_deterministic_and_never_promotes_old_skill_evidence(
        self,
    ) -> None:
        evidence, _ = self.run_fake()
        first = runner.report_value([evidence], self.snapshot)
        second = runner.report_value([deepcopy(evidence)], self.snapshot)
        self.assertEqual(runner.canonical_json(first), runner.canonical_json(second))
        self.assertEqual("unknown", first["claims"]["currentSkillCompatibility"])
        self.assertFalse(first["claims"]["interactiveTuiObserved"])
        self.assertFalse(first["claims"]["releaseEligible"])
        self.assertEqual(self.snapshot.file_count, len(first["skill"]["files"]))
        self.assertEqual(
            {
                "kind": "harness-reported-between-trial-stop-threshold",
                "authoritativeSpendCap": False,
                "missingReportedCostCountsAsZero": True,
                "singleTrialOvershootPossible": True,
            },
            first["spendControl"],
        )
        self.assertEqual(
            1, first["processCustody"]["retainedEvidenceWithBoundedSettlement"]
        )
        self.assertEqual(
            0, first["processCustody"]["retainedEvidenceWithoutSettlement"]
        )
        self.assertFalse(first["processCustody"]["strictContainmentClaimed"])

    def test_report_never_rewrites_historical_observations(self) -> None:
        source = sorted((HERE / "evidence/current").glob("*.evidence.json"))[0]
        with tempfile.TemporaryDirectory() as raw:
            evidence_dir = Path(raw) / "evidence"
            evidence_dir.mkdir()
            copied = evidence_dir / source.name
            copied.write_bytes(source.read_bytes())
            before = copied.read_bytes()
            args = argparse.Namespace(
                policy=HERE / "policy.v1.json",
                matrix=HERE / "matrix.v1.json",
                evidence=evidence_dir,
                verify_live_inputs=False,
            )
            self.assertEqual(0, runner.command_report(args))
            self.assertEqual(before, copied.read_bytes())

    def test_run_refuses_to_overwrite_prior_evidence_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "prior"
            target.mkdir()
            marker = target / "custody.txt"
            marker.write_text("preserve", "utf-8")
            args = argparse.Namespace(
                i_understand_this_spends_money=True,
                i_understand_this_uses_a_legacy_skill=True,
                evidence=target,
            )
            with mock.patch.object(
                runner, "run_trial", side_effect=AssertionError("must not spawn")
            ):
                with self.assertRaisesRegex(
                    runner.ConfigurationError, "already exists"
                ):
                    runner.command_run(args)
            self.assertEqual("preserve", marker.read_text("utf-8"))

    def test_failed_run_publishes_no_partial_target_or_staging_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            matrix = deepcopy(self.matrix)
            matrix["harness"]["expectedExecutableSha256"] = runner.file_digest(
                self.fake
            )
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(matrix), "utf-8")
            env_file = root / "credentials.env"
            env_file.write_text("OPENROUTER_API_KEY=fake-secret\n", "utf-8")
            target = root / "candidate-v2"
            args = argparse.Namespace(
                i_understand_this_spends_money=True,
                i_understand_this_uses_a_legacy_skill=True,
                policy=HERE / "policy.v1.json",
                matrix=matrix_path,
                evidence=target,
                env_file=env_file,
                executable=self.fake.resolve(),
                skill_root=self.snapshot.root,
            )
            with mock.patch.object(
                runner,
                "run_trial",
                side_effect=runner.ConfigurationError("injected trial failure"),
            ):
                with self.assertRaisesRegex(runner.ConfigurationError, "injected"):
                    runner.command_run(args)
            self.assertFalse(target.exists())
            self.assertEqual([], list(root.glob(".candidate-v2.staging-*")))

    def test_atomic_publication_preserves_a_destination_that_wins_the_race(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, with_marker in (("empty", False), ("with-data", True)):
                with self.subTest(destination=name):
                    publication = root / f"publication-{name}"
                    publication.mkdir()
                    (publication / "new.txt").write_text("new", "utf-8")
                    destination = root / f"candidate-{name}"
                    destination.mkdir()
                    identity = destination.stat().st_dev, destination.stat().st_ino
                    marker = destination / "adversarial.txt"
                    if with_marker:
                        marker.write_text("preserve", "utf-8")

                    with self.assertRaisesRegex(
                        runner.ConfigurationError, "destination appeared"
                    ):
                        runner.publish_directory_no_replace(publication, destination)

                    self.assertEqual(
                        identity,
                        (destination.stat().st_dev, destination.stat().st_ino),
                    )
                    if with_marker:
                        self.assertEqual("preserve", marker.read_text("utf-8"))
                    self.assertTrue(publication.is_dir())
                    self.assertEqual(
                        "new", (publication / "new.txt").read_text("utf-8")
                    )

    def test_real_run_requires_both_explicit_acknowledgements(self) -> None:
        args = argparse.Namespace(
            i_understand_this_spends_money=False,
            i_understand_this_uses_a_legacy_skill=False,
        )
        with self.assertRaisesRegex(runner.ConfigurationError, "opt-in"):
            runner.command_run(args)

    def test_provider_free_full_matrix_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            matrix = deepcopy(self.matrix)
            matrix["harness"]["expectedExecutableSha256"] = runner.file_digest(
                self.fake
            )
            matrix_path = root / "matrix.json"
            matrix_path.write_text(json.dumps(matrix), "utf-8")
            env_file = root / "credentials.env"
            env_file.write_text("OPENROUTER_API_KEY=fake-secret\n", "utf-8")
            evidence_dir = root / "evidence"
            args = argparse.Namespace(
                i_understand_this_spends_money=True,
                i_understand_this_uses_a_legacy_skill=True,
                policy=HERE / "policy.v1.json",
                matrix=matrix_path,
                evidence=evidence_dir,
                env_file=env_file,
                executable=self.fake.resolve(),
                skill_root=self.snapshot.root,
            )
            self.assertEqual(0, runner.command_run(args))
            report = json.loads((evidence_dir / "report.json").read_text("utf-8"))
            self.assertEqual(16, report["evidenceCount"])
            self.assertEqual(0, report["oldSkillEffectPasses"])
            self.assertEqual(16, report["filesystemEffectPasses"])
            self.assertEqual(0, report["subagentProvenPasses"])
            self.assertAlmostEqual(0.016, report["harnessReportedCost"]["total"])
            self.assertNotIn("fake-secret", json.dumps(report))
            self.assertIn(
                "not an interactive TUI observation",
                (evidence_dir / "REPORT.md").read_text("utf-8"),
            )
            self.assertEqual(
                0,
                runner.command_validate(
                    argparse.Namespace(
                        policy=HERE / "policy.v1.json",
                        matrix=matrix_path,
                        evidence=evidence_dir,
                        verify_live_inputs=False,
                    )
                ),
            )

    @staticmethod
    def _wait_for_group_absent(process_group: int) -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.02)
        return False

    @staticmethod
    def _kill_validated_detached(pid: int, process_group: int) -> None:
        if pid <= 1 or process_group <= 1 or process_group == os.getpgrp():
            raise AssertionError("refusing unsafe detached cleanup")
        try:
            if os.getpgid(pid) != process_group:
                raise AssertionError("detached process identity changed")
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
