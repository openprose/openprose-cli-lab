from __future__ import annotations

import argparse
from contextlib import redirect_stderr
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import run_public_alpha_verification as runner
import verify_public_alpha as verification


VERSION = "0.15.0-alpha.1"
SOURCE_SHA = "a" * 40
REPOSITORY = "openprose/prose"
RELEASE_ID = 1234
DRAFT_WORKFLOW_RUN_ID = 4321
DRAFT_WORKFLOW_RUN_ATTEMPT = 2
DRAFT_AUTHORITY_SHA256 = "f" * 64
DRAFT_AUTHORITY_ARTIFACT = "openprose-cli-alpha-draft-authority-run-4321-attempt-2"


def arguments(root: Path, **changes: object) -> argparse.Namespace:
    github_cli = root / "gh"
    if not github_cli.exists():
        github_cli.write_text("#!/bin/sh\nexit 0\n")
        github_cli.chmod(0o700)
    values: dict[str, object] = {
        "repository": REPOSITORY,
        "release_id": str(RELEASE_ID),
        "version": VERSION,
        "tag": f"cli-v{VERSION}",
        "source_sha": SOURCE_SHA,
        "target_id": "linux-x64",
        "workflow_run_id": "5678",
        "workflow_run_attempt": "1",
        "draft_authority": (root / "draft-authority" / "alpha-draft-authority.json"),
        "draft_authority_sha256": DRAFT_AUTHORITY_SHA256,
        "draft_workflow_run_id": str(DRAFT_WORKFLOW_RUN_ID),
        "draft_workflow_run_attempt": str(DRAFT_WORKFLOW_RUN_ATTEMPT),
        "confirmation": runner.operator_confirmation(
            repository=REPOSITORY,
            version=VERSION,
            tag=f"cli-v{VERSION}",
            source_sha=SOURCE_SHA,
            release_id=RELEASE_ID,
        ),
        "lineage": root / "lineage.json",
        "image_manifest": root / "manifest.json",
        "evidence": root / "evidence" / "alpha-public-verification.json",
        "workspace": root / "workspace",
        "journey_root": root / "journey",
        "github_cli": github_cli.resolve(strict=True),
    }
    values.update(changes)
    return argparse.Namespace(**values)


def workflow_environment(**changes: str) -> dict[str, str]:
    values = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_RUN_ID": "5678",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    values.update(changes)
    return values


def journey_request(
    root: Path, target_id: str = "linux-x64"
) -> verification.JourneyRequest:
    platform = verification.TARGET_PLATFORMS[target_id]
    names = {
        "direct-rust": (f"openprose-prose-cli-rust-{VERSION}-{platform}.tar.gz",),
        "direct-bun": (f"openprose-prose-cli-bun-{VERSION}-{platform}.tar.gz",),
        "npm-launcher": (
            f"openprose-prose-cli-{platform}-{VERSION}.tgz",
            f"openprose-prose-cli-{VERSION}.tgz",
        ),
    }
    specs = []
    for surface in verification.SURFACES:
        artifacts = []
        for name in names[surface]:
            body = f"authenticated:{name}".encode()
            (root / name).write_bytes(body)
            artifacts.append(
                verification.JourneyArtifact(
                    name=name,
                    path=root / name,
                    sha256=hashlib.sha256(body).hexdigest(),
                )
            )
        specs.append(
            verification.JourneySpec(
                surface=surface,
                target_id=target_id,
                version=VERSION,
                source_sha=SOURCE_SHA,
                artifacts=tuple(artifacts),
            )
        )
    for name in runner.PACKAGE_EVIDENCE:
        (root / f"{target_id}-{name}").write_bytes(f"authenticated:{name}".encode())
    return verification.JourneyRequest(journeys=tuple(specs))


class InputTests(unittest.TestCase):
    def test_target_cohort_is_exactly_the_four_advertised_posix_targets(self) -> None:
        self.assertEqual(
            ("linux-x64", "linux-arm64", "darwin-arm", "darwin-x64"),
            runner.POSIX_TARGETS,
        )

    def test_exact_identity_is_converted_to_the_core_confirmation(self) -> None:
        for target_id in runner.POSIX_TARGETS:
            with self.subTest(target_id=target_id), TemporaryDirectory() as temporary:
                root = Path(temporary)
                (
                    parsed,
                    authority,
                    evidence,
                    workspace,
                    journey,
                    github_cli,
                ) = runner._inputs(
                    arguments(root, target_id=target_id), workflow_environment()
                )
                self.assertEqual(REPOSITORY, parsed.repository)
                self.assertEqual(target_id, parsed.target_id)
                self.assertEqual(
                    verification.expected_confirmation(parsed), parsed.confirmation
                )
                self.assertTrue(evidence.is_absolute())
                self.assertNotEqual(workspace, journey)
                self.assertEqual((root / "gh").resolve(), github_cli)
                self.assertEqual(
                    root / "draft-authority" / "alpha-draft-authority.json",
                    authority.path,
                )
                self.assertEqual(DRAFT_AUTHORITY_SHA256, authority.sha256)
                self.assertEqual(DRAFT_WORKFLOW_RUN_ID, authority.workflow_run_id)
                self.assertEqual(
                    DRAFT_WORKFLOW_RUN_ATTEMPT, authority.workflow_run_attempt
                )

    def test_numeric_identity_rejects_signs_whitespace_and_zeroes(self) -> None:
        for value in ("0", "01", "+1", " 1", "1 ", "1.0"):
            with self.subTest(value=value), TemporaryDirectory() as temporary:
                with self.assertRaises(runner.RunnerError):
                    runner._inputs(
                        arguments(Path(temporary), release_id=value),
                        workflow_environment(),
                    )

    def test_repository_tag_confirmation_and_paths_are_exact(self) -> None:
        mutations = (
            ({"tag": f"v{VERSION}"}, workflow_environment()),
            ({}, workflow_environment(GITHUB_REPOSITORY="fork/prose")),
            ({"confirmation": "VERIFY PUBLIC ALPHA"}, workflow_environment()),
            ({"workspace": Path("relative")}, workflow_environment()),
            ({"workflow_run_id": "9"}, workflow_environment()),
            ({"target_id": "win-x64"}, workflow_environment()),
            ({"github_cli": Path("gh")}, workflow_environment()),
        )
        for changes, environment in mutations:
            with self.subTest(changes=changes), TemporaryDirectory() as temporary:
                with self.assertRaises(runner.RunnerError):
                    runner._inputs(arguments(Path(temporary), **changes), environment)

    def test_draft_authority_contract_rejects_replay_tamper_and_unsafe_paths(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            mutations = (
                {"draft_authority_sha256": "f" * 63},
                {"draft_workflow_run_id": "0"},
                {"draft_workflow_run_id": "01"},
                {"draft_workflow_run_attempt": "+2"},
                {"draft_authority": Path("alpha-draft-authority.json")},
                {"draft_authority": root / "draft-authority.json"},
                {
                    "draft_authority": root
                    / "authority"
                    / ".."
                    / "alpha-draft-authority.json"
                },
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation), self.assertRaises(
                    runner.RunnerError
                ):
                    runner._inputs(arguments(root, **mutation), workflow_environment())

    def test_existing_or_credential_bearing_runs_are_refused(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(runner.RunnerError):
                runner._inputs(
                    arguments(root, workspace=existing),
                    workflow_environment(),
                )
            with self.assertRaisesRegex(runner.RunnerError, "credentials"):
                runner._inputs(
                    arguments(root),
                    workflow_environment(OPENAI_API_KEY="must-not-be-accepted"),
                )

    def test_github_cli_must_be_existing_canonical_regular_and_executable(self) -> None:
        for mutation in ("missing", "symlink", "non-executable"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                root = Path(temporary)
                args = arguments(root)
                if mutation == "missing":
                    args.github_cli = root / "missing-gh"
                elif mutation == "symlink":
                    link = root / "gh-link"
                    link.symlink_to(args.github_cli)
                    args.github_cli = link
                else:
                    args.github_cli.chmod(0o600)
                with self.assertRaisesRegex(runner.RunnerError, "GitHub CLI"):
                    runner._inputs(args, workflow_environment())


class JourneyTests(unittest.TestCase):
    def test_each_posix_target_drives_its_exact_existing_admission_once(self) -> None:
        for target_id in runner.POSIX_TARGETS:
            with self.subTest(target_id=target_id), TemporaryDirectory() as temporary:
                root = Path(temporary)
                assets = root / "assets"
                assets.mkdir()
                request = journey_request(assets, target_id)
                image = root / "manifest.json"
                image.write_text("{}")
                observed: dict[str, object] = {}

                def admit(**kwargs: object) -> dict[str, object]:
                    package_root = kwargs["packages"]
                    assert isinstance(package_root, Path)
                    observed["names"] = tuple(
                        sorted(path.name for path in package_root.iterdir())
                    )
                    observed["kwargs"] = kwargs
                    report = {"status": "passed"}
                    out = kwargs["out"]
                    assert isinstance(out, Path)
                    out.write_text("{}\n")
                    return report

                boundary = runner.ProviderFreeAlphaJourney(
                    root=root / "journey",
                    image_manifest=image,
                    target_id=target_id,
                )
                with patch.object(
                    runner.admission, "run_admission", side_effect=admit
                ) as run_call, patch.object(
                    runner.admission,
                    "validate_report",
                    return_value={"status": "passed"},
                ) as validate_call:
                    results = boundary.run(request)

                expected_names = {
                    *(
                        artifact.name
                        for spec in request.journeys
                        for artifact in spec.artifacts
                    ),
                    *runner.PACKAGE_EVIDENCE,
                }
                self.assertEqual(tuple(sorted(expected_names)), observed["names"])
                self.assertEqual(target_id, observed["kwargs"]["target_id"])
                self.assertEqual(1, run_call.call_count)
                self.assertEqual(1, validate_call.call_count)
                self.assertEqual(
                    verification.SURFACES, tuple(item.surface for item in results)
                )
                self.assertTrue(
                    all(
                        item.provider_calls == verification.PROVIDER_CALLS
                        for item in results
                    )
                )
                self.assertTrue(
                    all(item.semantic_evaluation is False for item in results)
                )
                with self.assertRaisesRegex(runner.RunnerError, "retried"):
                    boundary.run(request)

    def test_journey_rejects_a_different_matrix_target(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            boundary = runner.ProviderFreeAlphaJourney(
                root=root / "journey",
                image_manifest=root / "manifest.json",
                target_id="darwin-arm",
            )
            with self.assertRaisesRegex(runner.RunnerError, "target differs"):
                boundary.run(journey_request(assets, "linux-x64"))

    def test_reordered_surface_cross_root_and_digest_tamper_are_rejected(self) -> None:
        for mutation in ("reorder", "cross-root", "digest"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                root = Path(temporary)
                assets = root / "assets"
                assets.mkdir()
                request = journey_request(assets)
                journeys = list(request.journeys)
                if mutation == "reorder":
                    journeys.reverse()
                elif mutation == "cross-root":
                    other = root / "other"
                    other.mkdir()
                    artifact = journeys[0].artifacts[0]
                    other_path = other / artifact.name
                    other_path.write_bytes(artifact.path.read_bytes())
                    journeys[0] = verification.JourneySpec(
                        **{
                            **journeys[0].__dict__,
                            "artifacts": (
                                verification.JourneyArtifact(
                                    name=artifact.name,
                                    path=other_path,
                                    sha256=artifact.sha256,
                                ),
                            ),
                        }
                    )
                else:
                    artifact = journeys[0].artifacts[0]
                    artifact.path.write_bytes(b"tampered")
                boundary = runner.ProviderFreeAlphaJourney(
                    root=root / "journey",
                    image_manifest=root / "manifest.json",
                    target_id="linux-x64",
                )
                with self.assertRaises(runner.RunnerError), patch.object(
                    runner.admission, "run_admission"
                ) as admission_call:
                    boundary.run(verification.JourneyRequest(journeys=tuple(journeys)))
                admission_call.assert_not_called()

    def test_symlinked_evidence_and_existing_root_are_rejected(self) -> None:
        for mutation in ("symlink", "existing"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                root = Path(temporary)
                assets = root / "assets"
                assets.mkdir()
                request = journey_request(assets)
                journey_root = root / "journey"
                if mutation == "symlink":
                    name = runner.PACKAGE_EVIDENCE[0]
                    source = assets / f"linux-x64-{name}"
                    target = root / "target"
                    target.write_bytes(source.read_bytes())
                    source.unlink()
                    source.symlink_to(target)
                else:
                    journey_root.mkdir()
                boundary = runner.ProviderFreeAlphaJourney(
                    root=journey_root,
                    image_manifest=root / "manifest.json",
                    target_id="linux-x64",
                )
                with self.assertRaises(runner.RunnerError), patch.object(
                    runner.admission, "run_admission"
                ) as admission_call:
                    boundary.run(request)
                admission_call.assert_not_called()


class RunCompositionTests(unittest.TestCase):
    def test_production_runner_wires_only_read_boundaries_and_selected_journey(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root, target_id="darwin-arm")
            environment = workflow_environment(GITHUB_TOKEN="read-token")
            lineage = object()
            http = object()
            github = object()
            attestations = object()
            registry = object()
            journeys = object()
            report = {"status": "complete"}
            with patch.object(
                runner.promotion, "load_lineage", return_value=lineage
            ), patch.object(
                runner.verification, "ReadOnlyHttp", return_value=http
            ), patch.object(
                runner.verification, "PublicGitHub", return_value=github
            ) as github_factory, patch.object(
                runner.verification,
                "GhAttestationVerifier",
                return_value=attestations,
            ) as attestation_factory, patch.object(
                runner.verification, "PublicNpmRegistry", return_value=registry
            ), patch.object(
                runner, "ProviderFreeAlphaJourney", return_value=journeys
            ) as journey_factory, patch.object(
                runner.verification, "execute_verification", return_value=report
            ) as execute:
                self.assertIs(report, runner.run(args, environment=environment))

            github_factory.assert_called_once_with(
                repository=REPOSITORY,
                source_sha=SOURCE_SHA,
                token="read-token",
                http=http,
            )
            attestation_factory.assert_called_once_with(
                executable=str((root / "gh").resolve()), token="read-token"
            )
            journey_factory.assert_called_once_with(
                root=root / "journey",
                image_manifest=root / "manifest.json",
                target_id="darwin-arm",
            )
            called = execute.call_args.kwargs
            self.assertEqual("darwin-arm", called["inputs"].target_id)
            self.assertEqual(
                verification.DraftAuthorityInput(
                    path=(root / "draft-authority" / "alpha-draft-authority.json"),
                    sha256=DRAFT_AUTHORITY_SHA256,
                    workflow_run_id=DRAFT_WORKFLOW_RUN_ID,
                    workflow_run_attempt=DRAFT_WORKFLOW_RUN_ATTEMPT,
                ),
                called["draft_authority"],
            )
            self.assertIs(lineage, called["lineage"])
            self.assertIs(github, called["github"])
            self.assertIs(attestations, called["attestations"])
            self.assertIs(registry, called["registry"])
            self.assertIs(journeys, called["journeys"])

    def test_missing_read_token_fails_before_any_remote_boundary(self) -> None:
        with TemporaryDirectory() as temporary, patch.object(
            runner.promotion, "load_lineage"
        ) as lineage:
            with self.assertRaisesRegex(runner.RunnerError, "read-only GitHub"):
                runner.run(
                    arguments(Path(temporary)), environment=workflow_environment()
                )
        lineage.assert_not_called()

    def test_main_never_emits_an_unexpected_exception_detail(self) -> None:
        stderr = io.StringIO()
        with patch.object(runner, "parser") as parser_factory, patch.object(
            runner, "run", side_effect=RuntimeError("token /Users/private")
        ), redirect_stderr(stderr):
            parser_factory.return_value.parse_args.return_value = object()
            self.assertEqual(1, runner.main([]))
        self.assertEqual(
            "public alpha verification refused at a closed boundary\n",
            stderr.getvalue(),
        )


class WorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            Path(__file__).resolve().parents[2]
            / ".github"
            / "workflows"
            / "openprose-cli-alpha-post-public.yml"
        ).read_text("utf-8")

    def test_manual_read_only_workflow_has_exact_identity_and_no_privileged_environment(
        self,
    ) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        for name in (
            "repository",
            "version",
            "tag",
            "source_sha",
            "release_id",
            "draft_workflow_run_id",
            "draft_workflow_run_attempt",
            "draft_authority_artifact",
            "confirmation",
        ):
            self.assertRegex(self.workflow, rf"(?m)^      {name}:$")
        self.assertRegex(
            self.workflow,
            r"(?m)^permissions:\n  actions: read\n  contents: read\n"
            r"  attestations: read$",
        )
        self.assertNotRegex(self.workflow, r"(?m)^    environment:")
        for forbidden in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "NPM_TOKEN",
            "NODE_AUTH_TOKEN",
            "contents: write",
            "actions: write",
            "id-token: write",
            "secrets.",
        ):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("  require-main:\n", self.workflow)
        self.assertIn("  authenticate-draft-authority:\n", self.workflow)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', self.workflow)
        self.assertEqual(1, self.workflow.count("    needs: require-main"))
        self.assertIn(
            "    needs: [require-main, authenticate-draft-authority]",
            self.workflow,
        )
        self.assertNotRegex(
            self.workflow, r"(?m)^    if: github\.ref == 'refs/heads/main'$"
        )

    def test_controller_and_candidate_checkouts_are_separate_and_actions_are_pinned(
        self,
    ) -> None:
        self.assertEqual(
            3,
            self.workflow.count(
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
            ),
        )
        self.assertIn("path: control", self.workflow)
        self.assertIn("path: candidate", self.workflow)
        self.assertIn("ref: ${{ inputs.source_sha }}", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn(
            "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405",
            self.workflow,
        )
        self.assertIn(
            "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020", self.workflow
        )
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            self.workflow,
        )
        self.assertEqual(
            2,
            self.workflow.count(
                "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
            ),
        )
        self.assertNotRegex(self.workflow, r"uses: [^\n]+@(main|master|v[0-9])(?:\s|$)")

    def test_pinned_dependencies_posix_matrix_and_target_evidence_are_explicit(
        self,
    ) -> None:
        self.assertIn('python-version: "3.10.18"', self.workflow)
        self.assertIn('node-version: "24.20.0"', self.workflow)
        self.assertIn("timeout-minutes: 120", self.workflow)
        self.assertIn("--require-hashes --only-binary=:all:", self.workflow)
        self.assertIn("control/cli/ci/requirements-test.txt", self.workflow)
        expected = (
            ("linux-x64", "ubuntu-22.04"),
            ("linux-arm64", "ubuntu-22.04-arm"),
            ("darwin-arm", "macos-15"),
            ("darwin-x64", "macos-15-intel"),
        )
        self.assertEqual(1, self.workflow.count("matrix:\n"))
        for target_id, host in expected:
            self.assertEqual(
                1,
                self.workflow.count(f"{{target: {target_id}, runner: {host}}}"),
            )
        self.assertIn("runs-on: ${{ matrix.runner }}", self.workflow)
        self.assertIn("TARGET_ID: ${{ matrix.target }}", self.workflow)
        self.assertIn('--target-id "$TARGET_ID"', self.workflow)
        self.assertIn('--github-cli "$GH_TOOL"', self.workflow)
        self.assertIn("all 38 release assets", self.workflow)
        self.assertIn("native installed journeys", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("alpha-public-verification-$TARGET_ID.json", self.workflow)
        self.assertIn("${{ matrix.target }}", self.workflow)
        self.assertIn("retention-days: 90", self.workflow)
        self.assertNotRegex(
            self.workflow,
            r"(?i)\b(npm publish|npm dist-tag|gh release edit|curl|wget)\b",
        )

    def test_one_exact_immutable_draft_authority_is_authenticated_and_shared(
        self,
    ) -> None:
        artifact_expression = "${{ inputs.draft_authority_artifact }}"
        run_expression = "${{ inputs.draft_workflow_run_id }}"
        self.assertEqual(1, self.workflow.count(f"name: {artifact_expression}"))
        self.assertEqual(1, self.workflow.count(f"run-id: {run_expression}"))
        self.assertEqual(
            1,
            self.workflow.count(
                "name: ${{ needs.authenticate-draft-authority.outputs."
                "draft_authority_artifact }}"
            ),
        )
        self.assertEqual(
            1,
            self.workflow.count(
                "run-id: ${{ needs.authenticate-draft-authority.outputs."
                "draft_workflow_run_id }}"
            ),
        )
        self.assertEqual(2, self.workflow.count("github-token: ${{ github.token }}"))
        self.assertIn(
            'test "$DRAFT_AUTHORITY_ARTIFACT" = '
            '"openprose-cli-alpha-draft-authority-run-'
            '$DRAFT_WORKFLOW_RUN_ID-attempt-$DRAFT_WORKFLOW_RUN_ATTEMPT"',
            self.workflow,
        )
        self.assertIn(
            'api --method GET --header "Accept: application/vnd.github+json"',
            self.workflow,
        )
        self.assertIn(
            '"/repos/$REPOSITORY_INPUT/actions/runs/$DRAFT_WORKFLOW_RUN_ID/'
            'attempts/$DRAFT_WORKFLOW_RUN_ATTEMPT"',
            self.workflow,
        )
        self.assertIn("promotion.load_draft_authority(", self.workflow)
        self.assertIn("draft_authority_sha256={authority.sha256}", self.workflow)
        self.assertIn(
            "DRAFT_AUTHORITY_SHA256: "
            "${{ needs.authenticate-draft-authority.outputs.draft_authority_sha256 }}",
            self.workflow,
        )
        self.assertIn(
            "DRAFT_WORKFLOW_RUN_ID: "
            "${{ needs.authenticate-draft-authority.outputs.draft_workflow_run_id }}",
            self.workflow,
        )
        self.assertIn(
            "DRAFT_WORKFLOW_RUN_ATTEMPT: "
            "${{ needs.authenticate-draft-authority.outputs."
            "draft_workflow_run_attempt }}",
            self.workflow,
        )
        for argument in (
            '--draft-authority "$DRAFT_AUTHORITY"',
            '--draft-authority-sha256 "$DRAFT_AUTHORITY_SHA256"',
            '--draft-workflow-run-id "$DRAFT_WORKFLOW_RUN_ID"',
            '--draft-workflow-run-attempt "$DRAFT_WORKFLOW_RUN_ATTEMPT"',
        ):
            self.assertIn(argument, self.workflow)
        self.assertNotIn("render_release_notes.py", self.workflow)


if __name__ == "__main__":
    unittest.main()
