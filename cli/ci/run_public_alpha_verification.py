#!/usr/bin/env python3
"""Run the read-only post-public functional-alpha verifier.

The controller authenticates every public GitHub and npm byte before it runs
the existing provider-free installed-package admission on one exact native
POSIX target.  It deliberately has no publication, release-edit, provider, or
model credential boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Mapping, Sequence

import alpha_package_admission as admission
import promote_alpha_release as promotion
import verify_public_alpha as verification


POSIX_TARGETS = ("linux-x64", "linux-arm64", "darwin-arm", "darwin-x64")
WORKFLOW_PATH = ".github/workflows/openprose-cli-alpha-post-public.yml"
PACKAGE_EVIDENCE = (
    "release-manifest.json",
    "sbom.cdx.json",
    "provenance.json",
    "dependency-evidence.json",
    "SHA256SUMS",
)
PROVIDER_OR_PUBLICATION_CREDENTIALS = frozenset(
    {
        *admission.PROVIDER_CREDENTIALS,
        "NODE_AUTH_TOKEN",
        "NPM_TOKEN",
    }
)
MAX_JOURNEY_SECONDS = 600
STEP_TIMEOUT_SECONDS = 15


class RunnerError(RuntimeError):
    """A fixed, sanitized post-public runner failure."""


def operator_confirmation(
    *, repository: str, version: str, tag: str, source_sha: str, release_id: int
) -> str:
    return (
        f"VERIFY PUBLIC ALPHA {repository} {version} {tag} "
        f"{source_sha} {release_id}"
    )


def _positive_integer(value: Any, label: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise RunnerError(f"{label} must be an unsigned positive integer")
    return int(value)


def _absolute_new_path(value: Path, label: str) -> Path:
    requested = Path(os.path.abspath(value))
    if not value.is_absolute() or value != requested or os.path.lexists(value):
        raise RunnerError(f"{label} must be an absolute, canonical, unused path")
    return requested


def _absolute_executable(value: Path, label: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise RunnerError(f"{label} must be an absolute canonical executable")
    try:
        resolved = value.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise RunnerError(
            f"{label} must be an absolute canonical executable"
        ) from error
    if (
        value != resolved
        or not stat.S_ISREG(metadata.st_mode)
        or not os.access(resolved, os.X_OK)
    ):
        raise RunnerError(f"{label} must be an absolute canonical executable")
    return resolved


def _safe_write(path: Path, encoded: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o400)
        try:
            verification._write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise RunnerError("provider-free package staging failed") from error


def _copy_authenticated(
    source: Path, destination: Path, *, expected_sha256: str | None = None
) -> None:
    try:
        encoded = promotion._regular_bytes(
            source, promotion.draft.MAX_ALPHA_ASSET_BYTES
        )
    except promotion.PromotionError as error:
        raise RunnerError("provider-free package input is unsafe") from error
    if (
        expected_sha256 is not None
        and hashlib.sha256(encoded).hexdigest() != expected_sha256
    ):
        raise RunnerError("provider-free package digest differs")
    _safe_write(destination, encoded)


class ProviderFreeAlphaJourney:
    """Adapt one authenticated public target package to alpha admission."""

    def __init__(self, *, root: Path, image_manifest: Path, target_id: str) -> None:
        if target_id not in POSIX_TARGETS:
            raise RunnerError(
                "post-public journey target is not an advertised POSIX target"
            )
        self.root = root
        self.image_manifest = image_manifest
        self.target_id = target_id
        self._called = False

    def _validate_request(
        self,
        request: verification.JourneyRequest,
    ) -> tuple[Path, str, str, str, dict[str, verification.JourneySpec]]:
        if not isinstance(request, verification.JourneyRequest):
            raise RunnerError("provider-free journey request is malformed")
        if tuple(item.surface for item in request.journeys) != verification.SURFACES:
            raise RunnerError("provider-free journey surface closure differs")
        by_surface = {item.surface: item for item in request.journeys}
        if len(by_surface) != len(verification.SURFACES):
            raise RunnerError("provider-free journey surface identity is ambiguous")
        identities = {
            (item.target_id, item.version, item.source_sha) for item in request.journeys
        }
        if len(identities) != 1:
            raise RunnerError("provider-free journey release identity diverges")
        target_id, version, source_sha = next(iter(identities))
        if target_id != self.target_id:
            raise RunnerError(
                "provider-free journey target differs from the matrix cell"
            )
        platform = verification.TARGET_PLATFORMS[target_id]
        expected = {
            "direct-rust": (f"openprose-prose-cli-rust-{version}-{platform}.tar.gz",),
            "direct-bun": (f"openprose-prose-cli-bun-{version}-{platform}.tar.gz",),
            "npm-launcher": (
                f"openprose-prose-cli-{platform}-{version}.tgz",
                f"openprose-prose-cli-{version}.tgz",
            ),
        }
        roots: set[Path] = set()
        for surface in verification.SURFACES:
            spec = by_surface[surface]
            if tuple(artifact.name for artifact in spec.artifacts) != expected[surface]:
                raise RunnerError("provider-free journey artifact closure differs")
            for artifact in spec.artifacts:
                if artifact.path.name != artifact.name:
                    raise RunnerError("provider-free journey artifact path differs")
                roots.add(artifact.path.parent)
        if len(roots) != 1:
            raise RunnerError("provider-free journey assets do not share one root")
        return next(iter(roots)), target_id, version, source_sha, by_surface

    def run(
        self, request: verification.JourneyRequest
    ) -> Sequence[verification.JourneyResult]:
        if self._called:
            raise RunnerError("provider-free journey cannot be retried")
        self._called = True
        asset_root, target_id, version, source_sha, by_surface = self._validate_request(
            request
        )
        if os.path.lexists(self.root):
            raise RunnerError("provider-free journey root must be unused")
        try:
            self.root.mkdir(mode=0o700, parents=False, exist_ok=False)
            os.chmod(self.root, 0o700)
            package_root = self.root / "packages"
            package_root.mkdir(mode=0o700, exist_ok=False)
        except OSError as error:
            raise RunnerError("provider-free journey root is unavailable") from error

        for surface in verification.SURFACES:
            for artifact in by_surface[surface].artifacts:
                _copy_authenticated(
                    artifact.path,
                    package_root / artifact.name,
                    expected_sha256=artifact.sha256,
                )
        for name in PACKAGE_EVIDENCE:
            _copy_authenticated(asset_root / f"{target_id}-{name}", package_root / name)

        report_path = self.root / "alpha-package-admission.json"
        work_root = self.root / "alpha-package-admission-work"
        try:
            report = admission.run_admission(
                packages=package_root,
                work_root=work_root,
                out=report_path,
                target_id=target_id,
                version=version,
                source_sha=source_sha,
                image_manifest=self.image_manifest,
                timeout_seconds=STEP_TIMEOUT_SECONDS,
                deadline_monotonic=time.monotonic() + MAX_JOURNEY_SECONDS,
            )
            admitted = admission.validate_report(
                report_path=report_path,
                packages=package_root,
                target_id=target_id,
                version=version,
                source_sha=source_sha,
                image_manifest=self.image_manifest,
            )
        except admission.AdmissionError as error:
            raise RunnerError(
                "provider-free installed-package admission failed"
            ) from error
        if report != admitted:
            raise RunnerError(
                "provider-free admission report changed during validation"
            )

        return tuple(
            verification.JourneyResult(
                surface=spec.surface,
                target_id=spec.target_id,
                version=spec.version,
                source_sha=spec.source_sha,
                schema=admission.SCHEMA,
                status="passed-provider-free-functional-alpha",
                provider_calls="none-provider-free-fixture",
                semantic_evaluation=False,
                artifact_sha256=tuple(artifact.sha256 for artifact in spec.artifacts),
            )
            for spec in request.journeys
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository", required=True)
    result.add_argument("--release-id", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--tag", required=True)
    result.add_argument("--source-sha", required=True)
    result.add_argument("--target-id", required=True)
    result.add_argument("--workflow-run-id", required=True)
    result.add_argument("--workflow-run-attempt", required=True)
    result.add_argument("--confirmation", required=True)
    result.add_argument("--draft-authority", type=Path, required=True)
    result.add_argument("--draft-authority-sha256", required=True)
    result.add_argument("--draft-workflow-run-id", required=True)
    result.add_argument("--draft-workflow-run-attempt", required=True)
    result.add_argument("--lineage", type=Path, required=True)
    result.add_argument("--image-manifest", type=Path, required=True)
    result.add_argument("--evidence", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--journey-root", type=Path, required=True)
    result.add_argument("--github-cli", type=Path, required=True)
    return result


def _inputs(
    args: argparse.Namespace, environment: Mapping[str, str]
) -> tuple[
    verification.VerificationInputs,
    verification.DraftAuthorityInput,
    Path,
    Path,
    Path,
    Path,
]:
    release_id = _positive_integer(args.release_id, "release ID")
    workflow_run_id = _positive_integer(args.workflow_run_id, "workflow run ID")
    workflow_run_attempt = _positive_integer(
        args.workflow_run_attempt, "workflow run attempt"
    )
    draft_workflow_run_id = _positive_integer(
        args.draft_workflow_run_id, "draft authority workflow run ID"
    )
    draft_workflow_run_attempt = _positive_integer(
        args.draft_workflow_run_attempt,
        "draft authority workflow run attempt",
    )
    if args.target_id not in POSIX_TARGETS:
        raise RunnerError("post-public target is not an advertised POSIX target")
    if args.tag != f"cli-v{args.version}":
        raise RunnerError("tag must exactly derive from the alpha version")
    if environment.get("GITHUB_REPOSITORY") != args.repository:
        raise RunnerError("repository input differs from the workflow repository")
    if (
        environment.get("GITHUB_RUN_ID") != args.workflow_run_id
        or environment.get("GITHUB_RUN_ATTEMPT") != args.workflow_run_attempt
    ):
        raise RunnerError("workflow run identity differs from the runner environment")
    present = sorted(
        name for name in PROVIDER_OR_PUBLICATION_CREDENTIALS if environment.get(name)
    )
    if present:
        raise RunnerError(
            "provider or publication credentials are forbidden in this workflow"
        )
    expected = operator_confirmation(
        repository=args.repository,
        version=args.version,
        tag=args.tag,
        source_sha=args.source_sha,
        release_id=release_id,
    )
    if args.confirmation != expected:
        raise RunnerError("confirmation does not exactly identify the public alpha")
    evidence = _absolute_new_path(args.evidence, "evidence")
    workspace = _absolute_new_path(args.workspace, "workspace")
    journey_root = _absolute_new_path(args.journey_root, "journey root")
    github_cli = _absolute_executable(args.github_cli, "GitHub CLI")
    paths = (evidence, workspace, journey_root)
    if len(set(paths)) != 3 or any(
        left in right.parents or right in left.parents
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    ):
        raise RunnerError("verification output paths must be distinct")
    inputs = verification.VerificationInputs(
        repository=args.repository,
        release_id=release_id,
        version=args.version,
        source_sha=args.source_sha,
        target_id=args.target_id,
        workflow_path=WORKFLOW_PATH,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
        confirmation="",
    )
    inputs = verification.VerificationInputs(
        **{
            **inputs.__dict__,
            "confirmation": verification.expected_confirmation(inputs),
        }
    )
    verification.validate_inputs(inputs)
    draft_authority = verification.DraftAuthorityInput(
        path=args.draft_authority,
        sha256=args.draft_authority_sha256,
        workflow_run_id=draft_workflow_run_id,
        workflow_run_attempt=draft_workflow_run_attempt,
    )
    try:
        verification.validate_draft_authority_input(draft_authority)
    except verification.VerificationError as error:
        raise RunnerError("draft authority input identity is unsafe") from error
    return inputs, draft_authority, evidence, workspace, journey_root, github_cli


def run(
    args: argparse.Namespace, *, environment: Mapping[str, str] = os.environ
) -> dict[str, Any]:
    (
        inputs,
        draft_authority,
        evidence,
        workspace,
        journey_root,
        github_cli,
    ) = _inputs(args, environment)
    token = environment.get("GITHUB_TOKEN", "")
    if not token or "\n" in token or "\r" in token:
        raise RunnerError("the read-only GitHub workflow token is unavailable")
    try:
        lineage = promotion.load_lineage(args.lineage, inputs.version)
        http = verification.ReadOnlyHttp()
        github = verification.PublicGitHub(
            repository=inputs.repository,
            source_sha=inputs.source_sha,
            token=token,
            http=http,
        )
        report = verification.execute_verification(
            inputs=inputs,
            draft_authority=draft_authority,
            lineage=lineage,
            github=github,
            attestations=verification.GhAttestationVerifier(
                executable=str(github_cli), token=token
            ),
            registry=verification.PublicNpmRegistry(http),
            journeys=ProviderFreeAlphaJourney(
                root=journey_root,
                image_manifest=args.image_manifest,
                target_id=inputs.target_id,
            ),
            writer=verification.EvidenceWriter(evidence),
            workspace=workspace,
        )
    except promotion.PromotionError as error:
        raise RunnerError("public-alpha lineage admission failed") from error
    return report


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        report = run(args)
    except Exception:
        print("public alpha verification refused at a closed boundary", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": report["schema"],
                "status": report["status"],
                "version": report["version"],
                "sourceSha": report["sourceSha"],
                "assetCount": report["github"]["assetCount"],
                "journeyTargetId": report["targetId"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
