from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from release_preflight import assess, load_bundle_module, main


CLI = Path(__file__).resolve().parents[1]
SENTINEL = CLI / "shared" / "image" / "sentinel-v1" / "manifest.json"
ECHO = CLI / "shared" / "image" / "echo-v0" / "manifest.json"
RUST_MANIFEST = CLI / "rust" / "Cargo.toml"
BUN_PACKAGE = CLI / "bun" / "package.json"
SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
CONTROL_SHA = "fedcba9876543210fedcba9876543210fedcba98"
PRODUCER_SHA = "1111111111111111111111111111111111111111"
AUTHORITY_ARTIFACT_ID = "987654321"
AUTHORITY_REPOSITORY = "openprose/prose"
AUTHORITY_WORKFLOW = ".github/workflows/openprose-cli-protected-release-authority.yml"
AUTHORITY_ENVIRONMENT = "openprose-cli-release-authority"


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), "utf-8")
    return path


def valid_inputs(root: Path) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    image_root = root / "image"
    shutil.copytree(SENTINEL.parent, image_root)
    image_path = image_root / "manifest.json"
    image = json.loads(image_path.read_text("utf-8"))
    image["purpose"] = "canonical-language-runtime"
    image["releaseEligible"] = True
    write_json(image_path, image)
    bundle_path = root / "image.bundle.bin"
    checksum_path = root / "image.bundle.sha256"
    bundle_module = load_bundle_module()
    bundle_bytes = bundle_module.build_bundle(image_root, require_release_eligible=True)
    bundle_path.write_bytes(bundle_bytes)
    checksum_path.write_bytes(bundle_module.checksum_bytes(bundle_bytes))
    image_sha256 = image["aggregateSha256"]["sha256"]
    image_manifest_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
    authority = root / "protected-authority"
    authority.mkdir()
    canonical = write_json(
        authority / "canonical-profile-attestation.json",
        {
            "schema": "openprose.canonical-profile-attestation/1",
            "authority": "protected-canonical-profile",
            "sourceSha": SOURCE_SHA,
            "imageSha256": image_sha256,
            "imageManifestSha256": image_manifest_sha256,
            "status": "pass",
        },
    )
    evidence = write_json(
        authority / "release-evidence-attestation.json",
        {
            "schema": "openprose.release-evidence-attestation/1",
            "authority": "protected-release-evidence",
            "sourceSha": SOURCE_SHA,
            "imageSha256": image_sha256,
            "imageManifestSha256": image_manifest_sha256,
            "status": "pass",
            "checks": {
                "benchmarkTrust": "pass",
                "conformance": "pass",
                "portability": "pass",
                "vulnerabilityReview": "pass",
            },
        },
    )
    metadata = write_json(
        root / "protected-authority-run.json",
        {
            "schema": "openprose.github-protected-authority-run/1",
            "repository": AUTHORITY_REPOSITORY,
            "runId": 123456789,
            "runAttempt": 1,
            "headSha": PRODUCER_SHA,
            "headBranch": "main",
            "workflowPath": AUTHORITY_WORKFLOW,
            "event": "workflow_dispatch",
            "conclusion": "success",
            "artifactId": int(AUTHORITY_ARTIFACT_ID),
            "artifactName": "openprose-cli-protected-release-authority",
        },
    )
    provenance = write_json(
        authority / "authority-provenance.json",
        {
            "schema": "openprose.protected-release-authority-provenance/1",
            "producer": {
                "repository": AUTHORITY_REPOSITORY,
                "runId": 123456789,
                "runAttempt": 1,
                "headSha": PRODUCER_SHA,
                "workflowPath": AUTHORITY_WORKFLOW,
                "environment": AUTHORITY_ENVIRONMENT,
            },
            "subject": {
                "sourceSha": SOURCE_SHA,
                "imageSha256": image_sha256,
                "imageManifestSha256": image_manifest_sha256,
            },
            "attestations": {
                "canonicalProfile": {
                    "path": canonical.name,
                    "byteLength": len(canonical.read_bytes()),
                    "sha256": hashlib.sha256(canonical.read_bytes()).hexdigest(),
                },
                "releaseEvidence": {
                    "path": evidence.name,
                    "byteLength": len(evidence.read_bytes()),
                    "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                },
            },
            "publicationAuthorized": False,
        },
    )
    return image_path, bundle_path, checksum_path, canonical, evidence, metadata, provenance


def authority_args(metadata: Path, provenance: Path) -> dict[str, object]:
    return {
        "authority_run_metadata": metadata,
        "authority_provenance": provenance,
        "authority_artifact_id": AUTHORITY_ARTIFACT_ID,
        "authority_repository": AUTHORITY_REPOSITORY,
        "authority_workflow_path": AUTHORITY_WORKFLOW,
        "authority_environment": AUTHORITY_ENVIRONMENT,
    }


class ReleasePreflightTest(unittest.TestCase):
    def test_functional_alpha_image_is_not_a_canonical_full_release_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess(
                version="0.1.0",
                source_sha=SOURCE_SHA,
                checked_out_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                control_ref="refs/heads/main",
                draft_only="true",
                image_manifest=ECHO,
                image_bundle=CLI / "shared" / "image" / "embedded" / "current.bundle.bin",
                image_checksum=CLI / "shared" / "image" / "embedded" / "current.bundle.sha256",
                canonical_profile=root / "absent-canonical.json",
                release_evidence=root / "absent-evidence.json",
                **authority_args(
                    root / "absent-authority-run.json",
                    root / "absent-authority-provenance.json",
                ),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "purpose must be canonical-language-runtime",
            "\n".join(report["failures"]),
        )

    def test_current_sentinel_and_absent_external_gates_fail_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel_bundle = root / "sentinel.bundle.bin"
            sentinel_checksum = root / "sentinel.bundle.sha256"
            bundle_module = load_bundle_module()
            bundle_bytes = bundle_module.build_bundle(
                SENTINEL.parent,
                require_release_eligible=False,
            )
            sentinel_bundle.write_bytes(bundle_bytes)
            sentinel_checksum.write_bytes(bundle_module.checksum_bytes(bundle_bytes))
            report = assess(
                version="0.1.0",
                source_sha=SOURCE_SHA,
                checked_out_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                control_ref="refs/heads/main",
                draft_only="true",
                image_manifest=SENTINEL,
                image_bundle=sentinel_bundle,
                image_checksum=sentinel_checksum,
                canonical_profile=root / "absent-canonical.json",
                release_evidence=root / "absent-evidence.json",
                **authority_args(
                    root / "absent-authority-run.json",
                    root / "absent-authority-provenance.json",
                ),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
        self.assertEqual(report["status"], "fail")
        failures = "\n".join(report["failures"])
        self.assertIn("releaseEligible must be true", failures)
        self.assertIn("purpose must be canonical-language-runtime", failures)
        self.assertIn("canonical profile", failures)
        self.assertIn("release evidence", failures)

    def test_exact_protected_inputs_pass_and_are_digest_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, bundle, checksum, canonical, evidence, metadata, provenance = valid_inputs(root)
            report = assess(
                version="0.1.0",
                source_sha=SOURCE_SHA,
                checked_out_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                control_ref="refs/heads/main",
                draft_only="true",
                image_manifest=image,
                image_bundle=bundle,
                image_checksum=checksum,
                canonical_profile=canonical,
                release_evidence=evidence,
                **authority_args(metadata, provenance),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
            expected_digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["gates"]["canonicalProfile"]["sha256"], expected_digest)
        self.assertEqual(report["protectedAuthority"]["status"], "pass")
        self.assertEqual(report["protectedAuthority"]["artifactId"], AUTHORITY_ARTIFACT_ID)
        self.assertEqual(report["protectedAuthority"]["producerRunId"], 123456789)
        self.assertFalse(report["publicationAuthorized"])
        self.assertEqual(report["releaseKind"], "draft-only")

    def test_identity_draft_and_closed_attestation_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, bundle, checksum, canonical, evidence, metadata, provenance = valid_inputs(root)
            canonical_value = json.loads(canonical.read_text("utf-8"))
            canonical_value["unexpected"] = True
            write_json(canonical, canonical_value)
            report = assess(
                version="v1.2.3",
                source_sha="short",
                checked_out_sha=SOURCE_SHA,
                control_sha="bad",
                control_ref="refs/heads/feature",
                draft_only="false",
                image_manifest=image,
                image_bundle=bundle,
                image_checksum=checksum,
                canonical_profile=canonical,
                release_evidence=evidence,
                **authority_args(metadata, provenance),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
        failures = "\n".join(report["failures"])
        for expected in (
            "version must be exact SemVer",
            "source SHA must be 40 lowercase hexadecimal characters",
            "checked-out SHA does not match",
            "control SHA must be 40 lowercase hexadecimal characters",
            "release control ref must be refs/heads/main",
            "draft-only must be true",
            "canonical profile has unknown or missing fields",
        ):
            self.assertIn(expected, failures)

    def test_manifest_metadata_and_product_versions_are_independently_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, bundle, checksum, canonical, evidence, metadata, provenance = valid_inputs(root)
            image.write_bytes(image.read_bytes() + b"\n")
            report = assess(
                version="0.1.1",
                source_sha=SOURCE_SHA,
                checked_out_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                control_ref="refs/heads/main",
                draft_only="true",
                image_manifest=image,
                image_bundle=bundle,
                image_checksum=checksum,
                canonical_profile=canonical,
                release_evidence=evidence,
                **authority_args(metadata, provenance),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
        failures = "\n".join(report["failures"])
        self.assertIn("canonical profile imageManifestSha256 does not match", failures)
        self.assertIn("release evidence imageManifestSha256 does not match", failures)
        self.assertIn("rust product version 0.1.0 does not match requested version 0.1.1", failures)
        self.assertIn("bun product version 0.1.0 does not match requested version 0.1.1", failures)

    def test_complete_image_directory_is_verified_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, bundle, checksum, canonical, evidence, metadata, provenance = valid_inputs(root)
            (image.parent / "payload" / "00-sentinel.md").write_text(
                "tampered\n", "utf-8"
            )
            report = assess(
                version="0.1.0",
                source_sha=SOURCE_SHA,
                checked_out_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                control_ref="refs/heads/main",
                draft_only="true",
                image_manifest=image,
                image_bundle=bundle,
                image_checksum=checksum,
                canonical_profile=canonical,
                release_evidence=evidence,
                **authority_args(metadata, provenance),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
        self.assertEqual(report["status"], "fail")
        self.assertIn("image bundle failed closed validation", "\n".join(report["failures"]))

    def test_cli_writes_machine_report_and_returns_nonzero_for_current_alpha_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "preflight.json"
            exit_code = main(
                [
                    "--version",
                    "0.1.0",
                    "--source-sha",
                    SOURCE_SHA,
                    "--checked-out-sha",
                    SOURCE_SHA,
                    "--control-sha",
                    CONTROL_SHA,
                    "--control-ref",
                    "refs/heads/main",
                    "--draft-only",
                    "true",
                    "--image-manifest",
                    str(ECHO),
                    "--image-bundle",
                    str(CLI / "shared" / "image" / "embedded" / "current.bundle.bin"),
                    "--image-checksum",
                    str(CLI / "shared" / "image" / "embedded" / "current.bundle.sha256"),
                    "--canonical-profile",
                    str(root / "missing-canonical.json"),
                    "--release-evidence",
                    str(root / "missing-evidence.json"),
                    "--authority-run-metadata",
                    str(root / "missing-authority-run.json"),
                    "--authority-provenance",
                    str(root / "missing-authority-provenance.json"),
                    "--authority-artifact-id",
                    AUTHORITY_ARTIFACT_ID,
                    "--authority-repository",
                    AUTHORITY_REPOSITORY,
                    "--authority-workflow-path",
                    AUTHORITY_WORKFLOW,
                    "--authority-environment",
                    AUTHORITY_ENVIRONMENT,
                    "--rust-manifest",
                    str(RUST_MANIFEST),
                    "--bun-package",
                    str(BUN_PACKAGE),
                    "--out",
                    str(output),
                ]
            )
            report = json.loads(output.read_text("utf-8"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "fail")

    def test_symlink_non_regular_and_oversized_inputs_fail_before_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, bundle, checksum, canonical, evidence, metadata, provenance = valid_inputs(root)
            symlink = root / "canonical-link.json"
            try:
                symlink.symlink_to(canonical)
            except OSError:
                self.skipTest("symlinks are unavailable")
            evidence.write_bytes(b"{" + b" " * (64 * 1024))
            report = assess(
                version="1.2.3",
                source_sha=SOURCE_SHA,
                checked_out_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                control_ref="refs/heads/main",
                draft_only="true",
                image_manifest=image,
                image_bundle=bundle,
                image_checksum=checksum,
                canonical_profile=symlink,
                release_evidence=evidence,
                **authority_args(metadata, provenance),
                rust_manifest=RUST_MANIFEST,
                bun_package=BUN_PACKAGE,
            )
        failures = "\n".join(report["failures"])
        self.assertIn("canonical profile must be a non-symlink regular file", failures)
        self.assertIn("release evidence must contain 1-65536 bytes", failures)

    def test_producer_identity_artifact_binding_and_closed_files_fail_on_drift(self) -> None:
        mutations = ("run", "producer", "subject", "digest", "extra", "artifact-id")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                image, bundle, checksum, canonical, evidence, metadata, provenance = valid_inputs(root)
                args = authority_args(metadata, provenance)
                if mutation == "run":
                    value = json.loads(metadata.read_text("utf-8"))
                    value["workflowPath"] = ".github/workflows/candidate.yml"
                    write_json(metadata, value)
                elif mutation == "producer":
                    value = json.loads(provenance.read_text("utf-8"))
                    value["producer"]["environment"] = "unprotected"
                    write_json(provenance, value)
                elif mutation == "subject":
                    value = json.loads(provenance.read_text("utf-8"))
                    value["subject"]["sourceSha"] = CONTROL_SHA
                    write_json(provenance, value)
                elif mutation == "digest":
                    value = json.loads(provenance.read_text("utf-8"))
                    value["attestations"]["canonicalProfile"]["sha256"] = "0" * 64
                    write_json(provenance, value)
                elif mutation == "extra":
                    (provenance.parent / "candidate-authored-pass.json").write_text("{}", "utf-8")
                else:
                    args["authority_artifact_id"] = "0"
                report = assess(
                    version="0.1.0",
                    source_sha=SOURCE_SHA,
                    checked_out_sha=SOURCE_SHA,
                    control_sha=CONTROL_SHA,
                    control_ref="refs/heads/main",
                    draft_only="true",
                    image_manifest=image,
                    image_bundle=bundle,
                    image_checksum=checksum,
                    canonical_profile=canonical,
                    release_evidence=evidence,
                    **args,
                    rust_manifest=RUST_MANIFEST,
                    bun_package=BUN_PACKAGE,
                )
                self.assertEqual(report["status"], "fail")
                self.assertEqual(report["protectedAuthority"]["status"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
