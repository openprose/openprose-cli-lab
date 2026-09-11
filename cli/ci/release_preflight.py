#!/usr/bin/env python3
"""Validate immutable draft-release inputs before any product compilation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DIGITS = re.compile(r"^[1-9][0-9]*$")
BUNDLE_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "shared"
    / "image"
    / "bundle"
    / "image_bundle.py"
)


def load_bundle_module() -> Any:
    """Load the language-agnostic image validator from its isolated script."""
    spec = importlib.util.spec_from_file_location(
        "openprose_release_image_bundle", BUNDLE_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the image-bundle validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_bounded(
    path: Path,
    label: str,
    failures: list[str],
    *,
    max_bytes: int,
) -> tuple[bytes, str] | None:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            failures.append(f"{label} must be a non-symlink regular file")
            return None
        if metadata.st_size <= 0 or metadata.st_size > max_bytes:
            failures.append(f"{label} must contain 1-{max_bytes} bytes")
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                failures.append(f"{label} changed while being opened")
                return None
            encoded = source.read(max_bytes + 1)
            settled = os.fstat(source.fileno())
        if len(encoded) > max_bytes or not encoded:
            failures.append(f"{label} must contain 1-{max_bytes} bytes")
            return None
        if opened.st_size != settled.st_size or opened.st_mtime_ns != settled.st_mtime_ns:
            failures.append(f"{label} changed while being read")
            return None
        return encoded, hashlib.sha256(encoded).hexdigest()
    except OSError as error:
        failures.append(f"{label} is unavailable: {error}")
        return None


def read_object(
    path: Path,
    label: str,
    failures: list[str],
    *,
    max_bytes: int,
) -> tuple[dict[str, Any], str] | None:
    loaded = read_bounded(path, label, failures, max_bytes=max_bytes)
    if loaded is None:
        return None
    encoded, input_digest = loaded
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        failures.append(f"{label} is invalid JSON: {error}")
        return None
    if not isinstance(value, dict):
        failures.append(f"{label} must contain a JSON object")
        return None
    return value, input_digest


def product_versions(
    rust_manifest: Path,
    bun_package: Path,
    failures: list[str],
) -> dict[str, str | None]:
    rust_version: str | None = None
    loaded_rust = read_bounded(
        rust_manifest,
        "Rust workspace manifest",
        failures,
        max_bytes=64 * 1024,
    )
    if loaded_rust is not None:
        try:
            rust_text = loaded_rust[0].decode("utf-8")
        except UnicodeDecodeError as error:
            failures.append(f"Rust workspace manifest is not UTF-8: {error}")
        else:
            section = re.search(
                r"(?ms)^\[workspace\.package\]\s*(.*?)(?=^\[|\Z)",
                rust_text,
            )
            match = None if section is None else re.search(
                r'^version\s*=\s*"([^"]+)"\s*$',
                section.group(1),
                re.MULTILINE,
            )
            if match is None:
                failures.append("Rust workspace manifest has no closed workspace package version")
            else:
                rust_version = match.group(1)

    bun_version: str | None = None
    loaded_bun = read_object(
        bun_package,
        "Bun package manifest",
        failures,
        max_bytes=64 * 1024,
    )
    if loaded_bun is not None:
        candidate = loaded_bun[0].get("version")
        if not isinstance(candidate, str) or SEMVER.fullmatch(candidate) is None:
            failures.append("Bun package manifest has no exact SemVer version")
        else:
            bun_version = candidate
    return {"rust": rust_version, "bun": bun_version}


def validate_gate(
    path: Path,
    *,
    label: str,
    schema: str,
    authority: str,
    source_sha: str,
    image_sha256: str | None,
    image_manifest_sha256: str | None,
    checks: tuple[str, ...] = (),
    failures: list[str],
) -> dict[str, Any]:
    loaded = read_object(path, label, failures, max_bytes=64 * 1024)
    result: dict[str, Any] = {"status": "unavailable", "sha256": None}
    if loaded is None:
        return result
    gate, gate_digest = loaded
    result["sha256"] = gate_digest
    expected_keys = {
        "schema",
        "authority",
        "sourceSha",
        "imageSha256",
        "imageManifestSha256",
        "status",
    }
    if checks:
        expected_keys.add("checks")
    if set(gate) != expected_keys:
        failures.append(f"{label} has unknown or missing fields")
    expected_values = {
        "schema": schema,
        "authority": authority,
        "sourceSha": source_sha,
        "imageSha256": image_sha256,
        "imageManifestSha256": image_manifest_sha256,
        "status": "pass",
    }
    for key, expected in expected_values.items():
        if gate.get(key) != expected:
            failures.append(f"{label} {key} does not match the protected release input")
    if checks:
        observed_checks = gate.get("checks")
        if not isinstance(observed_checks, dict) or set(observed_checks) != set(checks):
            failures.append(f"{label} checks have unknown or missing fields")
        elif any(observed_checks[name] != "pass" for name in checks):
            failures.append(f"{label} checks must all be pass")
    result["status"] = "pass" if not any(message.startswith(label) for message in failures) else "fail"
    return result


def validate_protected_authority(
    *,
    authority_run_metadata: Path,
    authority_provenance: Path,
    authority_artifact_id: str,
    authority_repository: str,
    authority_workflow_path: str,
    authority_environment: str,
    source_sha: str,
    image_sha256: str | None,
    image_manifest_sha256: str | None,
    canonical_profile: Path,
    release_evidence: Path,
    failures: list[str],
) -> dict[str, Any]:
    """Bind attestations to one exact independently protected GitHub artifact."""

    result: dict[str, Any] = {
        "status": "unavailable",
        "artifactId": authority_artifact_id,
        "producerRunId": None,
        "producerRunAttempt": None,
        "producerSha": None,
        "runMetadataSha256": None,
        "provenanceSha256": None,
        "attestations": None,
    }
    if DIGITS.fullmatch(authority_artifact_id) is None:
        failures.append("protected authority artifact ID must be a positive decimal integer")

    metadata_loaded = read_object(
        authority_run_metadata,
        "protected authority run metadata",
        failures,
        max_bytes=32 * 1024,
    )
    provenance_loaded = read_object(
        authority_provenance,
        "protected authority provenance",
        failures,
        max_bytes=32 * 1024,
    )
    canonical_loaded = read_bounded(
        canonical_profile,
        "canonical profile",
        failures,
        max_bytes=64 * 1024,
    )
    evidence_loaded = read_bounded(
        release_evidence,
        "release evidence",
        failures,
        max_bytes=64 * 1024,
    )
    if metadata_loaded is None or provenance_loaded is None:
        return result

    metadata, metadata_digest = metadata_loaded
    provenance, provenance_digest = provenance_loaded
    result["runMetadataSha256"] = metadata_digest
    result["provenanceSha256"] = provenance_digest
    expected_metadata_keys = {
        "schema",
        "repository",
        "runId",
        "runAttempt",
        "headSha",
        "headBranch",
        "workflowPath",
        "event",
        "conclusion",
        "artifactId",
        "artifactName",
    }
    if set(metadata) != expected_metadata_keys:
        failures.append("protected authority run metadata has unknown or missing fields")
    expected_metadata = {
        "schema": "openprose.github-protected-authority-run/1",
        "repository": authority_repository,
        "headBranch": "main",
        "workflowPath": authority_workflow_path,
        "event": "workflow_dispatch",
        "conclusion": "success",
        "artifactId": (
            int(authority_artifact_id)
            if DIGITS.fullmatch(authority_artifact_id)
            else None
        ),
        "artifactName": "openprose-cli-protected-release-authority",
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            failures.append(
                f"protected authority run metadata {key} does not match the protected producer"
            )
    run_id = metadata.get("runId")
    run_attempt = metadata.get("runAttempt")
    producer_sha = metadata.get("headSha")
    if not isinstance(run_id, int) or run_id <= 0:
        failures.append("protected authority run metadata runId must be a positive integer")
    if not isinstance(run_attempt, int) or run_attempt <= 0:
        failures.append("protected authority run metadata runAttempt must be a positive integer")
    if not isinstance(producer_sha, str) or FULL_SHA.fullmatch(producer_sha) is None:
        failures.append("protected authority run metadata headSha must be a full commit SHA")
    result["producerRunId"] = run_id
    result["producerRunAttempt"] = run_attempt
    result["producerSha"] = producer_sha

    expected_provenance_keys = {
        "schema",
        "producer",
        "subject",
        "attestations",
        "publicationAuthorized",
    }
    if set(provenance) != expected_provenance_keys:
        failures.append("protected authority provenance has unknown or missing fields")
    producer = provenance.get("producer")
    expected_producer = {
        "repository": authority_repository,
        "runId": run_id,
        "runAttempt": run_attempt,
        "headSha": producer_sha,
        "workflowPath": authority_workflow_path,
        "environment": authority_environment,
    }
    if not isinstance(producer, dict) or producer != expected_producer:
        failures.append("protected authority provenance producer does not match the verified run")
    if provenance.get("schema") != "openprose.protected-release-authority-provenance/1":
        failures.append("protected authority provenance schema is unsupported")
    if provenance.get("publicationAuthorized") is not False:
        failures.append("protected authority provenance must retain publicationAuthorized=false")
    expected_subject = {
        "sourceSha": source_sha,
        "imageSha256": image_sha256,
        "imageManifestSha256": image_manifest_sha256,
    }
    if provenance.get("subject") != expected_subject:
        failures.append("protected authority provenance subject does not match source and image")

    def attestation_record(
        path: Path,
        loaded: tuple[bytes, str] | None,
    ) -> dict[str, Any] | None:
        if loaded is None:
            return None
        encoded, digest = loaded
        return {"path": path.name, "byteLength": len(encoded), "sha256": digest}

    expected_attestations = {
        "canonicalProfile": attestation_record(canonical_profile, canonical_loaded),
        "releaseEvidence": attestation_record(release_evidence, evidence_loaded),
    }
    result["attestations"] = expected_attestations
    if provenance.get("attestations") != expected_attestations:
        failures.append("protected authority provenance does not bind the exact attestations")

    authority_directory = authority_provenance.parent
    try:
        entries = {path.name for path in authority_directory.iterdir()}
    except OSError as error:
        failures.append(f"protected authority artifact directory is unavailable: {error}")
    else:
        expected_entries = {
            authority_provenance.name,
            canonical_profile.name,
            release_evidence.name,
        }
        if entries != expected_entries:
            failures.append("protected authority artifact has unknown or missing files")

    authority_failures = (
        "protected authority artifact ID",
        "protected authority run metadata",
        "protected authority provenance",
        "protected authority artifact",
        "canonical profile",
        "release evidence",
    )
    result["status"] = (
        "fail"
        if any(message.startswith(authority_failures) for message in failures)
        else "pass"
    )
    return result


def assess(
    *,
    version: str,
    source_sha: str,
    checked_out_sha: str,
    control_sha: str,
    control_ref: str,
    draft_only: str,
    image_manifest: Path,
    image_bundle: Path,
    image_checksum: Path,
    canonical_profile: Path,
    release_evidence: Path,
    authority_run_metadata: Path,
    authority_provenance: Path,
    authority_artifact_id: str,
    authority_repository: str,
    authority_workflow_path: str,
    authority_environment: str,
    rust_manifest: Path,
    bun_package: Path,
) -> dict[str, Any]:
    failures: list[str] = []
    if SEMVER.fullmatch(version) is None:
        failures.append("version must be exact SemVer without a leading v")
    if FULL_SHA.fullmatch(source_sha) is None:
        failures.append("source SHA must be 40 lowercase hexadecimal characters")
    if checked_out_sha != source_sha:
        failures.append("checked-out SHA does not match the requested source SHA")
    if FULL_SHA.fullmatch(control_sha) is None:
        failures.append("control SHA must be 40 lowercase hexadecimal characters")
    if control_ref != "refs/heads/main":
        failures.append("release control ref must be refs/heads/main")
    if draft_only != "true":
        failures.append("draft-only must be true")
    versions = product_versions(rust_manifest, bun_package, failures)
    for product, observed in versions.items():
        if observed is not None and observed != version:
            failures.append(
                f"{product} product version {observed} does not match requested version {version}"
            )

    image_sha256: str | None = None
    image_manifest_sha256: str | None = None
    image_record: dict[str, Any] = {
        "manifestSha256": None,
        "bundleSha256": None,
        "checksumSha256": None,
        "imageSha256": None,
        "version": None,
        "purpose": None,
        "releaseEligible": None,
    }
    image: dict[str, Any] | None = None
    if image_manifest.name != "manifest.json":
        failures.append("image manifest must be named manifest.json inside its image root")
    else:
        try:
            bundle = load_bundle_module()
            parsed = bundle.load_image_directory(
                image_manifest.parent,
                require_release_eligible=False,
            )
            bundle_bytes = bundle.read_bounded(image_bundle, bundle.MAX_BUNDLE_BYTES)
            bundle.check_bundle(
                image_manifest.parent,
                bundle_bytes,
                require_release_eligible=False,
            )
            bundle.check_checksum(image_checksum, bundle_bytes)
            image = parsed.manifest
            image_manifest_sha256 = hashlib.sha256(parsed.manifest_bytes).hexdigest()
            image_record["bundleSha256"] = hashlib.sha256(bundle_bytes).hexdigest()
            checksum_bytes = bundle.read_bounded(image_checksum, 65)
            image_record["checksumSha256"] = hashlib.sha256(checksum_bytes).hexdigest()
        except OSError as error:
            failures.append(f"image bundle is unavailable: {error}")
        except Exception as error:  # The isolated validator owns its error type.
            failures.append(f"image bundle failed closed validation: {error}")
    if image is not None:
        image_record["manifestSha256"] = image_manifest_sha256
        image_record["version"] = image.get("imageVersion")
        image_record["purpose"] = image.get("purpose")
        image_record["releaseEligible"] = image.get("releaseEligible")
        aggregate = image.get("aggregateSha256")
        if image.get("schema") != "openprose.skill-runtime-image-manifest/1":
            failures.append("image manifest schema is unsupported")
        if image.get("releaseEligible") is not True:
            failures.append("image manifest releaseEligible must be true")
        if image.get("purpose") != "canonical-language-runtime":
            failures.append(
                "image manifest purpose must be canonical-language-runtime for a full release"
            )
        if (
            not isinstance(aggregate, dict)
            or aggregate.get("algorithm") != "sha256-path-length-nul-v1"
            or not isinstance(aggregate.get("sha256"), str)
            or SHA256.fullmatch(aggregate["sha256"]) is None
        ):
            failures.append("image manifest aggregate SHA-256 is invalid")
        else:
            image_sha256 = aggregate["sha256"]
            image_record["imageSha256"] = image_sha256

    authority_record = validate_protected_authority(
        authority_run_metadata=authority_run_metadata,
        authority_provenance=authority_provenance,
        authority_artifact_id=authority_artifact_id,
        authority_repository=authority_repository,
        authority_workflow_path=authority_workflow_path,
        authority_environment=authority_environment,
        source_sha=source_sha,
        image_sha256=image_sha256,
        image_manifest_sha256=image_manifest_sha256,
        canonical_profile=canonical_profile,
        release_evidence=release_evidence,
        failures=failures,
    )

    canonical = validate_gate(
        canonical_profile,
        label="canonical profile",
        schema="openprose.canonical-profile-attestation/1",
        authority="protected-canonical-profile",
        source_sha=source_sha,
        image_sha256=image_sha256,
        image_manifest_sha256=image_manifest_sha256,
        failures=failures,
    )
    evidence = validate_gate(
        release_evidence,
        label="release evidence",
        schema="openprose.release-evidence-attestation/1",
        authority="protected-release-evidence",
        source_sha=source_sha,
        image_sha256=image_sha256,
        image_manifest_sha256=image_manifest_sha256,
        checks=("benchmarkTrust", "conformance", "portability", "vulnerabilityReview"),
        failures=failures,
    )
    bound_attestations = authority_record.get("attestations")
    if isinstance(bound_attestations, dict):
        for gate_label, gate_record, attestation_name in (
            ("canonical profile", canonical, "canonicalProfile"),
            ("release evidence", evidence, "releaseEvidence"),
        ):
            attestation = bound_attestations.get(attestation_name)
            if not isinstance(attestation, dict) or gate_record.get(
                "sha256"
            ) != attestation.get("sha256"):
                failures.append(
                    f"{gate_label} changed after protected authority validation"
                )
                authority_record["status"] = "fail"
    return {
        "schema": "openprose.release-preflight-report/1",
        "status": "pass" if not failures else "fail",
        "releaseKind": "draft-only" if draft_only == "true" else "invalid",
        "publicationAuthorized": False,
        "version": version,
        "sourceSha": source_sha,
        "checkedOutSha": checked_out_sha,
        "controlSha": control_sha,
        "controlRef": control_ref,
        "productVersions": versions,
        "image": image_record,
        "protectedAuthority": authority_record,
        "gates": {
            "canonicalProfile": canonical,
            "releaseEvidence": evidence,
        },
        "failures": failures,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--version", required=True)
    result.add_argument("--source-sha", required=True)
    result.add_argument("--checked-out-sha", required=True)
    result.add_argument("--control-sha", required=True)
    result.add_argument("--control-ref", required=True)
    result.add_argument("--draft-only", required=True)
    result.add_argument("--image-manifest", type=Path, required=True)
    result.add_argument("--image-bundle", type=Path, required=True)
    result.add_argument("--image-checksum", type=Path, required=True)
    result.add_argument("--canonical-profile", type=Path, required=True)
    result.add_argument("--release-evidence", type=Path, required=True)
    result.add_argument("--authority-run-metadata", type=Path, required=True)
    result.add_argument("--authority-provenance", type=Path, required=True)
    result.add_argument("--authority-artifact-id", required=True)
    result.add_argument("--authority-repository", required=True)
    result.add_argument("--authority-workflow-path", required=True)
    result.add_argument("--authority-environment", required=True)
    result.add_argument("--rust-manifest", type=Path, required=True)
    result.add_argument("--bun-package", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = assess(
        version=args.version,
        source_sha=args.source_sha,
        checked_out_sha=args.checked_out_sha,
        control_sha=args.control_sha,
        control_ref=args.control_ref,
        draft_only=args.draft_only,
        image_manifest=args.image_manifest,
        image_bundle=args.image_bundle,
        image_checksum=args.image_checksum,
        canonical_profile=args.canonical_profile,
        release_evidence=args.release_evidence,
        authority_run_metadata=args.authority_run_metadata,
        authority_provenance=args.authority_provenance,
        authority_artifact_id=args.authority_artifact_id,
        authority_repository=args.authority_repository,
        authority_workflow_path=args.authority_workflow_path,
        authority_environment=args.authority_environment,
        rust_manifest=args.rust_manifest,
        bun_package=args.bun_package,
    )
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(encoded, "utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["status"] == "pass":
        return 0
    for failure in report["failures"]:
        print(f"release-preflight: {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
