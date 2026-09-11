#!/usr/bin/env python3
"""Preverify or publish an admitted functional-alpha draft.

The command never builds or repacks a candidate. Its read-only mode verifies
the closed draft's artifact attestations and writes one immutable same-run
handoff record. Its disjoint transition mode consumes that exact record,
reauthenticates candidate bytes before each mutation, and performs one
explicitly selected transition without GitHub CLI verifier custody.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import time
from typing import Any, Callable, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import create_draft_release as draft
import check_registry_lineage as lineage_check


SCHEMA = "openprose.alpha-promotion-settlement/1"
ATTESTATION_EVIDENCE_SCHEMA = "openprose.alpha-promotion-attestation-evidence/1"
MODES = ("verify-attestations", "execute-transition")
ATTESTATION_EVIDENCE_FILENAME = "github-attestation-evidence.json"
DRAFT_AUTHORITY_FILENAME = "alpha-draft-authority.json"
DRAFT_AUTHORITY_ARTIFACT_PREFIX = "openprose-cli-alpha-draft-authority"
REGISTRY = "https://registry.npmjs.org"
ALPHA_TAG = "alpha"
OPERATIONS = ("bootstrap", "stage-platforms", "stage-meta", "settle-and-promote")
ALPHA = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)" r"-alpha\.(0|[1-9][0-9]*)$"
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,254}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
INTEGRITY = re.compile(r"^sha512-[A-Za-z0-9+/]+={0,2}$")
PROVENANCE_PREDICATE = "https://slsa.dev/provenance/v1"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 45
PROCESS_TIMEOUT_SECONDS = 180
ATTESTATION_TIMEOUT_SECONDS = 90
MAX_ATTESTATION_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_ATTESTATION_EVIDENCE_BYTES = 1024 * 1024
MAX_DRAFT_AUTHORITY_BYTES = 1024 * 1024
MAX_GH_BYTES = 256 * 1024 * 1024
SIGNER_WORKFLOW_PATH = ".github/workflows/openprose-cli-alpha-release.yml"
GH_TOOL_AUTHORITY = "externally-provisioned-github-cli"
NPM_VERSION = "11.15.0"
NODE_VERSION = "24.20.0"
NPM_TARBALL_URL = "https://registry.npmjs.org/npm/-/npm-11.15.0.tgz"
NPM_ENTRY = "package/bin/npm-cli.js"
MAX_NPM_ARCHIVE_MEMBERS = 2048
MAX_NPM_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_NPM_MEMBER_BYTES = 1024 * 1024
MAX_NODE_BYTES = 256 * 1024 * 1024
PLATFORMS = (
    "darwin-arm64",
    "darwin-x64",
    "linux-arm64-gnu",
    "linux-x64-gnu",
)
PLATFORM_PACKAGES = tuple(f"@openprose/prose-cli-{value}" for value in PLATFORMS)
META_PACKAGE = "@openprose/prose-cli"
PACKAGE_ORDER = (*PLATFORM_PACKAGES, META_PACKAGE)
TARGETS = {
    "linux-x64": "linux-x64-gnu",
    "linux-arm64": "linux-arm64-gnu",
    "darwin-arm": "darwin-arm64",
    "darwin-x64": "darwin-x64",
}
EVIDENCE_SUFFIXES = (
    "release-manifest.json",
    "sbom.cdx.json",
    "provenance.json",
    "dependency-evidence.json",
    "SHA256SUMS",
    "alpha-admission.json",
)
SETTLEMENT_FIELDS = {
    "schema",
    "operation",
    "status",
    "version",
    "sourceSha",
    "repository",
    "releaseId",
    "tag",
    "registry",
    "stableLatest",
    "alphaTag",
    "assemblySha256",
    "draftAuthority",
    "startedAt",
    "updatedAt",
    "githubArtifactAttestations",
    "npmMutationToolchain",
    "packages",
    "github",
}
NPM_TOOLCHAIN_FIELDS = {
    "npmVersion",
    "npmTarballUrl",
    "npmTarballByteLength",
    "npmTarballSha1",
    "npmTarballSha256",
    "npmTarballSha512",
    "npmTarballIntegrity",
    "npmTreeSha256",
    "npmEntrySha256",
    "nodeVersion",
    "nodeExecutableSha256",
}
PACKAGE_SETTLEMENT_FIELDS = {
    "name",
    "role",
    "artifact",
    "sha256",
    "shasum",
    "integrity",
    "attempted",
    "action",
    "outcome",
    "registryIntegrity",
    "provenancePredicateType",
}
GITHUB_SETTLEMENT_FIELDS = {
    "draftVerified",
    "promotionAttempted",
    "promoted",
    "outcome",
}
ATTESTATION_SETTLEMENT_FIELDS = {
    "status",
    "repository",
    "sourceSha",
    "sourceRef",
    "predicateType",
    "signerWorkflow",
    "signerDigest",
    "expectedAssetCount",
    "verifiedAssetCount",
    "bytesReauthenticated",
    "workflowRunId",
    "workflowRunAttempt",
    "evidenceSha256",
    "tool",
    "assets",
}
ATTESTATION_TOOL_FIELDS = {
    "authority",
    "version",
    "executableByteLength",
    "executableSha256",
}
ATTESTATION_ASSET_FIELDS = {"name", "byteLength", "sha256", "outcome"}
ATTESTATION_EVIDENCE_FIELDS = {
    "schema",
    "status",
    "repository",
    "releaseId",
    "version",
    "sourceSha",
    "tag",
    "assemblySha256",
    "draftAuthority",
    "sourceRef",
    "predicateType",
    "signerWorkflow",
    "signerDigest",
    "workflowRunId",
    "workflowRunAttempt",
    "expectedAssetCount",
    "verifiedAssetCount",
    "bytesReauthenticated",
    "tool",
    "assets",
}
DRAFT_AUTHORITY_IDENTITY_FIELDS = {
    "sha256",
    "artifact",
    "controlSha",
    "releaseBody",
    "assetInventorySha256",
    "workflowRun",
    "outcome",
}
ATTESTATION_EVIDENCE_ASSET_FIELDS = {
    "name",
    "byteLength",
    "sha256",
    "outcome",
}


class PromotionError(RuntimeError):
    """A sanitized, operator-actionable promotion failure."""


@dataclass(frozen=True)
class NpmArchiveAuthority:
    url: str
    byte_length: int
    sha1: str
    sha256: str
    sha512: str
    integrity: str
    tree_sha256: str
    entry_sha256: str


PINNED_NPM_AUTHORITY = NpmArchiveAuthority(
    url=NPM_TARBALL_URL,
    byte_length=2_901_197,
    sha1="d1a5bc920f2068774ae4d802916379fad266ba0d",
    sha256="c15ed81d98f5f4c45e30f71e5dcf83ae24e9af5beb5db8b1d58becea97ba38cc",
    sha512=(
        "fa4d2d93b9519e93143e70bb913b94ff2ad5fe69c5a0f84943be557cbb59e9fc"
        "1bcce55768fb1313f225f4f9f50c78b8f366f2332aa41effd7b10efa98d8d72f"
    ),
    integrity=(
        "sha512-+k0tk7lRnpMUPnC7kTuU/yrV/mnFoPhJQ75VfLtZ6fwbzOVXaPsTE/"
        "Il9Pn1DHi482byMyqkHv/XsQ76mNjXLw=="
    ),
    tree_sha256="a625dc0d20bcb3c26ed2e607376b0b723dea713881e3d63a31e871526103e9f6",
    entry_sha256="8e5f6f3429f8cdbe693cdc29904e9d5a7b127a494bd15c804bd54c7403bfcbe7",
)


@dataclass(frozen=True)
class PackageArtifact:
    name: str
    role: str
    artifact: str
    path: Path
    body: bytes
    sha256: str
    shasum: str
    integrity: str


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class Candidate:
    repository: str
    release_id: int
    version: str
    source_sha: str
    tag: str
    assembly_sha256: str
    assets: tuple[ReleaseAsset, ...]
    packages: tuple[PackageArtifact, ...]


@dataclass(frozen=True)
class DraftAuthority:
    repository: str
    release_id: int
    version: str
    source_sha: str
    control_sha: str
    tag: str
    body_byte_length: int
    body_sha256: str
    assets: tuple[ReleaseAsset, ...]
    asset_inventory_sha256: str
    workflow_run_id: int
    workflow_run_attempt: int
    outcome: str
    sha256: str


@dataclass(frozen=True)
class RegistryVersion:
    name: str
    version: str
    shasum: str
    integrity: str
    tarball_url: str
    optional_dependencies: dict[str, str] | None
    provenance_predicate_type: str | None


@dataclass(frozen=True)
class RegistryPackage:
    name: str
    owners: tuple[str, ...]
    tags: dict[str, str]
    versions: dict[str, RegistryVersion]


@dataclass(frozen=True)
class Lineage:
    latest: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class AttestationResult:
    repository: str
    source_sha: str
    source_ref: str
    predicate_type: str
    signer_workflow: str
    signer_digest: str
    subject_name: str
    subject_sha256: str


class RegistryBoundary(Protocol):
    def package(self, name: str) -> RegistryPackage | None:
        ...

    def tarball(self, url: str) -> bytes:
        ...


class NpmBoundary(Protocol):
    def require_toolchain(self) -> dict[str, Any]:
        ...

    def mutate(self, operation: str, package: PackageArtifact) -> int:
        ...


class GitHubBoundary(Protocol):
    def promote(self, candidate: Candidate) -> None:
        ...


class AttestationBoundary(Protocol):
    def require_toolchain(self) -> dict[str, Any]:
        ...

    def verify(
        self, *, candidate: Candidate, asset: ReleaseAsset
    ) -> Sequence[AttestationResult]:
        ...

    def reauthenticate(self, candidate: Candidate) -> None:
        ...


class CandidateAssetBoundary(Protocol):
    def reauthenticate(self, candidate: Candidate) -> None:
        ...


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _closed_json(encoded: bytes, label: str, maximum: int = MAX_JSON_BYTES) -> Any:
    if not encoded or len(encoded) > maximum:
        raise PromotionError(f"{label} is empty or exceeds its byte limit")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PromotionError(f"{label} contains a duplicate field")
            result[key] = value
        return result

    try:
        return json.loads(encoded, object_pairs_hook=reject_duplicates)
    except PromotionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromotionError(f"{label} is not valid JSON") from error


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PromotionError(f"{label} has unknown or missing fields")
    return value


def _validate_attestation_tool(value: Any) -> dict[str, Any]:
    tool = _exact_object(
        value, ATTESTATION_TOOL_FIELDS, "GitHub attestation tool identity"
    )
    if (
        tool.get("authority") != GH_TOOL_AUTHORITY
        or not isinstance(tool.get("version"), str)
        or re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
            tool["version"],
        )
        is None
        or not isinstance(tool.get("executableByteLength"), int)
        or isinstance(tool["executableByteLength"], bool)
        or tool["executableByteLength"] <= 0
        or tool["executableByteLength"] > MAX_GH_BYTES
        or not isinstance(tool.get("executableSha256"), str)
        or SHA256.fullmatch(tool["executableSha256"]) is None
    ):
        raise PromotionError("GitHub attestation tool identity is malformed")
    return dict(tool)


def _regular_bytes(path: Path, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise PromotionError("candidate asset is unavailable") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > maximum
    ):
        raise PromotionError("candidate asset is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PromotionError("candidate asset cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PromotionError("candidate asset changed before open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise PromotionError("candidate asset exceeds its byte limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or total != opened.st_size:
        raise PromotionError("candidate asset changed while being read")
    return b"".join(chunks)


def _write_all(descriptor: int, encoded: bytes) -> None:
    offset = 0
    while offset < len(encoded):
        written = os.write(descriptor, encoded[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def expected_asset_names(version: str) -> tuple[str, ...]:
    if ALPHA.fullmatch(version) is None:
        raise PromotionError("version must be exact numbered alpha SemVer")
    evidence = {
        f"{target}-{suffix}" for target in TARGETS for suffix in EVIDENCE_SUFFIXES
    }
    artifacts = {f"openprose-prose-cli-{version}.tgz"}
    for platform in PLATFORMS:
        artifacts.update(
            {
                f"openprose-prose-cli-{platform}-{version}.tgz",
                f"openprose-prose-cli-rust-{version}-{platform}.tar.gz",
                f"openprose-prose-cli-bun-{version}-{platform}.tar.gz",
            }
        )
    return tuple(sorted({"SHA256SUMS", *evidence, *artifacts}))


def _encode_draft_authority(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def load_draft_authority(
    path: Path,
    *,
    expected_sha256: str,
    repository: str,
    release_id: int,
    version: str,
    source_sha: str,
    producer_run_id: int,
    producer_run_attempt: int,
) -> DraftAuthority:
    """Load one exact immutable draft handoff from its producing workflow run."""
    if path.name != DRAFT_AUTHORITY_FILENAME:
        raise PromotionError("draft authority must use the canonical filename")
    if not isinstance(expected_sha256, str) or SHA256.fullmatch(expected_sha256) is None:
        raise PromotionError("draft authority digest is malformed")
    if (
        not isinstance(producer_run_id, int)
        or isinstance(producer_run_id, bool)
        or producer_run_id <= 0
        or not isinstance(producer_run_attempt, int)
        or isinstance(producer_run_attempt, bool)
        or producer_run_attempt <= 0
    ):
        raise PromotionError("draft authority producer run identity is malformed")
    encoded = _tool_regular_bytes(path, MAX_DRAFT_AUTHORITY_BYTES, "draft authority")
    if hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise PromotionError("draft authority digest does not match")
    value = _closed_json(encoded, "draft authority", MAX_DRAFT_AUTHORITY_BYTES)
    try:
        authority = draft.validate_alpha_draft_authority(value)
    except draft.DraftReleaseError as error:
        raise PromotionError("draft authority is invalid") from error
    if encoded != _encode_draft_authority(authority):
        raise PromotionError("draft authority is not canonical")
    workflow = authority["workflowRun"]
    if (
        authority["repository"] != repository
        or authority["releaseId"] != release_id
        or authority["version"] != version
        or authority["sourceSha"] != source_sha
        or authority["controlSha"] != source_sha
        or authority["tag"] != f"cli-v{version}"
        or workflow["id"] != producer_run_id
        or workflow["attempt"] != producer_run_attempt
    ):
        raise PromotionError("draft authority identity does not match this promotion")
    expected_names = expected_asset_names(version)
    assets = tuple(
        ReleaseAsset(
            name=item["name"],
            byte_length=item["byteLength"],
            sha256=item["sha256"],
        )
        for item in authority["assets"]
    )
    if tuple(item.name for item in assets) != expected_names:
        raise PromotionError("draft authority asset inventory is not closed")
    return DraftAuthority(
        repository=authority["repository"],
        release_id=authority["releaseId"],
        version=authority["version"],
        source_sha=authority["sourceSha"],
        control_sha=authority["controlSha"],
        tag=authority["tag"],
        body_byte_length=authority["releaseBody"]["byteLength"],
        body_sha256=authority["releaseBody"]["sha256"],
        assets=assets,
        asset_inventory_sha256=authority["assetInventorySha256"],
        workflow_run_id=workflow["id"],
        workflow_run_attempt=workflow["attempt"],
        outcome=authority["outcome"],
        sha256=expected_sha256,
    )


def require_draft_authority_candidate(
    authority: DraftAuthority, candidate: Candidate
) -> None:
    candidate_identity = (
        candidate.repository,
        candidate.release_id,
        candidate.version,
        candidate.source_sha,
        candidate.tag,
        candidate.assets,
    )
    authority_identity = (
        authority.repository,
        authority.release_id,
        authority.version,
        authority.source_sha,
        authority.tag,
        authority.assets,
    )
    if candidate_identity != authority_identity:
        raise PromotionError("candidate differs from immutable draft authority inventory")


def _draft_authority_identity(authority: DraftAuthority) -> dict[str, Any]:
    return {
        "sha256": authority.sha256,
        "artifact": (
            f"{DRAFT_AUTHORITY_ARTIFACT_PREFIX}-run-"
            f"{authority.workflow_run_id}-attempt-{authority.workflow_run_attempt}"
        ),
        "controlSha": authority.control_sha,
        "releaseBody": {
            "byteLength": authority.body_byte_length,
            "sha256": authority.body_sha256,
        },
        "assetInventorySha256": authority.asset_inventory_sha256,
        "workflowRun": {
            "path": draft.ALPHA_DRAFT_WORKFLOW,
            "id": authority.workflow_run_id,
            "attempt": authority.workflow_run_attempt,
        },
        "outcome": authority.outcome,
    }


def _validate_draft_authority_identity(
    value: Any,
    *,
    source_sha: str,
    assets: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    identity = _exact_object(
        value, DRAFT_AUTHORITY_IDENTITY_FIELDS, "draft authority identity"
    )
    body = identity.get("releaseBody")
    workflow = identity.get("workflowRun")
    if (
        not isinstance(identity.get("sha256"), str)
        or SHA256.fullmatch(identity["sha256"]) is None
        or identity.get("controlSha") != source_sha
        or not isinstance(body, dict)
        or set(body) != {"byteLength", "sha256"}
        or not isinstance(body.get("byteLength"), int)
        or isinstance(body["byteLength"], bool)
        or not 1 <= body["byteLength"] <= draft.MAX_RELEASE_NOTES_BYTES
        or not isinstance(body.get("sha256"), str)
        or SHA256.fullmatch(body["sha256"]) is None
        or not isinstance(identity.get("assetInventorySha256"), str)
        or SHA256.fullmatch(identity["assetInventorySha256"]) is None
        or not isinstance(workflow, dict)
        or set(workflow) != {"path", "id", "attempt"}
        or workflow.get("path") != draft.ALPHA_DRAFT_WORKFLOW
        or not isinstance(workflow.get("id"), int)
        or isinstance(workflow["id"], bool)
        or workflow["id"] <= 0
        or not isinstance(workflow.get("attempt"), int)
        or isinstance(workflow["attempt"], bool)
        or workflow["attempt"] <= 0
        or identity.get("artifact")
        != (
            f"{DRAFT_AUTHORITY_ARTIFACT_PREFIX}-run-{workflow.get('id')}"
            f"-attempt-{workflow.get('attempt')}"
        )
        or identity.get("outcome") not in {"created", "resumed"}
    ):
        raise PromotionError("draft authority identity is malformed")
    inventory = [
        {
            "name": item.get("name"),
            "sha256": item.get("sha256"),
            "byteLength": item.get("byteLength"),
        }
        for item in assets
    ]
    inventory_sha256 = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if identity["assetInventorySha256"] != inventory_sha256:
        raise PromotionError("draft authority asset inventory digest is inconsistent")
    return identity


def _encode_attestation_evidence(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def validate_attestation_evidence(
    value: Any,
    *,
    candidate: Candidate | None = None,
    workflow_run_id: int | None = None,
    workflow_run_attempt: int | None = None,
    draft_authority: DraftAuthority | None = None,
) -> dict[str, Any]:
    """Validate one final, closed attestation handoff record."""
    evidence = _exact_object(value, ATTESTATION_EVIDENCE_FIELDS, "attestation evidence")
    version = evidence.get("version")
    repository = evidence.get("repository")
    source_sha = evidence.get("sourceSha")
    release_id = evidence.get("releaseId")
    run_id = evidence.get("workflowRunId")
    run_attempt = evidence.get("workflowRunAttempt")
    assets = evidence.get("assets")
    expected_names = expected_asset_names(version) if isinstance(version, str) else ()
    if (
        evidence.get("schema") != ATTESTATION_EVIDENCE_SCHEMA
        or evidence.get("status") != "verified"
        or not isinstance(repository, str)
        or REPOSITORY.fullmatch(repository) is None
        or not isinstance(release_id, int)
        or isinstance(release_id, bool)
        or release_id <= 0
        or not isinstance(version, str)
        or ALPHA.fullmatch(version) is None
        or not isinstance(source_sha, str)
        or FULL_SHA.fullmatch(source_sha) is None
        or evidence.get("tag") != f"cli-v{version}"
        or not isinstance(evidence.get("assemblySha256"), str)
        or SHA256.fullmatch(evidence["assemblySha256"]) is None
        or evidence.get("sourceRef") != "refs/heads/main"
        or evidence.get("predicateType") != PROVENANCE_PREDICATE
        or evidence.get("signerWorkflow") != f"{repository}/{SIGNER_WORKFLOW_PATH}"
        or evidence.get("signerDigest") != source_sha
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id <= 0
        or not isinstance(run_attempt, int)
        or isinstance(run_attempt, bool)
        or run_attempt <= 0
        or evidence.get("expectedAssetCount") != len(expected_names)
        or evidence.get("verifiedAssetCount") != len(expected_names)
        or evidence.get("bytesReauthenticated") is not True
        or not isinstance(assets, list)
        or len(assets) != len(expected_names)
    ):
        raise PromotionError("attestation evidence identity is malformed")
    _validate_attestation_tool(evidence.get("tool"))
    normalized_assets: list[tuple[str, int, str]] = []
    for index, item in enumerate(assets):
        asset = _exact_object(
            item, ATTESTATION_EVIDENCE_ASSET_FIELDS, "attestation evidence asset"
        )
        if (
            asset.get("name") != expected_names[index]
            or not isinstance(asset.get("byteLength"), int)
            or isinstance(asset["byteLength"], bool)
            or asset["byteLength"] <= 0
            or asset["byteLength"] > draft.MAX_ALPHA_ASSET_BYTES
            or not isinstance(asset.get("sha256"), str)
            or SHA256.fullmatch(asset["sha256"]) is None
            or asset.get("outcome") != "verified"
        ):
            raise PromotionError("attestation evidence asset is malformed")
        normalized_assets.append((asset["name"], asset["byteLength"], asset["sha256"]))
    authority_identity = _validate_draft_authority_identity(
        evidence.get("draftAuthority"), source_sha=source_sha, assets=assets
    )
    if (
        draft_authority is not None
        and authority_identity != _draft_authority_identity(draft_authority)
    ):
        raise PromotionError("attestation evidence binds another draft authority")
    assembly = next(item for item in assets if item["name"] == "SHA256SUMS")
    if assembly["sha256"] != evidence["assemblySha256"]:
        raise PromotionError("attestation evidence assembly digest is inconsistent")
    if workflow_run_id is not None and run_id != workflow_run_id:
        raise PromotionError("attestation evidence belongs to another workflow run")
    if workflow_run_attempt is not None and run_attempt != workflow_run_attempt:
        raise PromotionError("attestation evidence belongs to another workflow attempt")
    if candidate is not None:
        candidate_identity = (
            candidate.repository,
            candidate.release_id,
            candidate.version,
            candidate.source_sha,
            candidate.tag,
            candidate.assembly_sha256,
        )
        evidence_identity = (
            repository,
            release_id,
            version,
            source_sha,
            evidence["tag"],
            evidence["assemblySha256"],
        )
        candidate_assets = tuple(
            (asset.name, asset.byte_length, asset.sha256) for asset in candidate.assets
        )
        if (
            evidence_identity != candidate_identity
            or tuple(normalized_assets) != candidate_assets
        ):
            raise PromotionError(
                "attestation evidence does not identify the current candidate"
            )
    return evidence


def attestation_evidence_digest(value: dict[str, Any]) -> str:
    validate_attestation_evidence(value)
    return hashlib.sha256(_encode_attestation_evidence(value)).hexdigest()


def load_attestation_evidence(
    path: Path,
    *,
    expected_sha256: str,
    candidate: Candidate,
    workflow_run_id: int,
    workflow_run_attempt: int,
    draft_authority: DraftAuthority | None = None,
) -> dict[str, Any]:
    if SHA256.fullmatch(expected_sha256) is None:
        raise PromotionError("attestation evidence digest is malformed")
    encoded = _tool_regular_bytes(
        path, MAX_ATTESTATION_EVIDENCE_BYTES, "attestation evidence"
    )
    if hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise PromotionError("attestation evidence digest does not match")
    value = _closed_json(
        encoded, "attestation evidence", MAX_ATTESTATION_EVIDENCE_BYTES
    )
    evidence = validate_attestation_evidence(
        value,
        candidate=candidate,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
        draft_authority=draft_authority,
    )
    if encoded != _encode_attestation_evidence(evidence):
        raise PromotionError("attestation evidence is not canonical")
    return evidence


def _npm_package_json(encoded: bytes, artifact: str) -> dict[str, Any]:
    try:
        with tarfile.open(fileobj=io.BytesIO(encoded), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > 32:
                raise PromotionError("npm package archive membership is not closed")
            names: set[str] = set()
            package_json: bytes | None = None
            for member in members:
                if (
                    member.name in names
                    or member.name.startswith("/")
                    or ".." in Path(member.name).parts
                    or not member.name.startswith("package/")
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise PromotionError("npm package archive has an unsafe member")
                names.add(member.name)
                if member.name == "package/package.json":
                    if (
                        not member.isfile()
                        or member.size <= 0
                        or member.size > 128 * 1024
                    ):
                        raise PromotionError(
                            "npm package manifest is not a bounded regular member"
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise PromotionError("npm package manifest cannot be read")
                    package_json = stream.read(128 * 1024 + 1)
            if package_json is None:
                raise PromotionError("npm package archive is missing package.json")
    except PromotionError:
        raise
    except (tarfile.TarError, OSError) as error:
        raise PromotionError(f"npm package archive is malformed: {artifact}") from error
    value = _closed_json(package_json, "npm package manifest", 128 * 1024)
    if not isinstance(value, dict):
        raise PromotionError("npm package manifest must be an object")
    return value


def _validate_package_manifest(
    manifest: dict[str, Any], *, name: str, version: str, source_sha: str, role: str
) -> None:
    if manifest.get("name") != name or manifest.get("version") != version:
        raise PromotionError("npm package manifest identity is not exact")
    if manifest.get("publishConfig") not in (None, {"access": "public"}):
        raise PromotionError("npm package publish configuration is incompatible")
    if "scripts" in manifest or manifest.get("private") is True:
        raise PromotionError(
            "npm package contains a publishing lifecycle or is private"
        )
    cohort = manifest.get("openproseCohort")
    cohort_fields = {
        "schema",
        "version",
        "sourceRevision",
        "releaseChannel",
        "purpose",
        "image",
        "admittedPlatforms",
        "semanticStatus",
        "releaseEligible",
        "publicationAuthorized",
    }
    if not isinstance(cohort, dict) or set(cohort) != cohort_fields:
        raise PromotionError("npm package cohort identity is not closed")
    if (
        cohort.get("schema") != "openprose.npm-cohort/1"
        or cohort.get("version") != version
        or cohort.get("sourceRevision") != source_sha
        or cohort.get("releaseChannel") != "functional-alpha"
        or cohort.get("admittedPlatforms") != sorted(PLATFORMS)
        or cohort.get("semanticStatus") != "not-applicable"
        or cohort.get("releaseEligible") is not False
        or cohort.get("publicationAuthorized") is not False
    ):
        raise PromotionError("npm package cohort is not exact and draft-safe")
    if role == "meta":
        expected = {package: version for package in PLATFORM_PACKAGES}
        if manifest.get("optionalDependencies") != expected:
            raise PromotionError(
                "npm meta package does not select the exact platform cohort"
            )
    else:
        platform = name.removeprefix("@openprose/prose-cli-")
        if manifest.get("openprosePlatform") != platform:
            raise PromotionError("npm platform package identity is inconsistent")


def authenticate_assembly(
    *, root: Path, repository: str, release_id: int, version: str, source_sha: str
) -> Candidate:
    """Reauthenticate a downloaded draft assembly without changing any byte."""
    try:
        draft.load_alpha_assembly(root, source_sha, version)
    except draft.DraftReleaseError as error:
        raise PromotionError(
            "downloaded draft assembly failed alpha admission"
        ) from error
    packages: list[PackageArtifact] = []
    for name in PACKAGE_ORDER:
        role = "meta" if name == META_PACKAGE else "platform"
        platform = name.removeprefix("@openprose/prose-cli-")
        artifact = (
            f"openprose-prose-cli-{version}.tgz"
            if role == "meta"
            else f"openprose-prose-cli-{platform}-{version}.tgz"
        )
        body = _regular_bytes(root / artifact, MAX_PACKAGE_BYTES)
        manifest = _npm_package_json(body, artifact)
        _validate_package_manifest(
            manifest, name=name, version=version, source_sha=source_sha, role=role
        )
        packages.append(
            PackageArtifact(
                name=name,
                role=role,
                artifact=artifact,
                path=root / artifact,
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                shasum=hashlib.sha1(
                    body
                ).hexdigest(),  # noqa: S324 - npm registry identity
                integrity="sha512-"
                + base64.b64encode(hashlib.sha512(body).digest()).decode("ascii"),
            )
        )
    aggregate = _regular_bytes(root / "SHA256SUMS", draft.MAX_ALPHA_CHECKSUM_BYTES)
    assets = []
    for name in expected_asset_names(version):
        body = _regular_bytes(root / name, draft.MAX_ALPHA_ASSET_BYTES)
        assets.append(
            ReleaseAsset(
                name=name,
                byte_length=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        )
    return Candidate(
        repository=repository,
        release_id=release_id,
        version=version,
        source_sha=source_sha,
        tag=f"cli-v{version}",
        assembly_sha256=hashlib.sha256(aggregate).hexdigest(),
        assets=tuple(assets),
        packages=tuple(packages),
    )


def load_lineage(path: Path, version: str) -> Lineage:
    try:
        value = lineage_check.load_authority(path)
        lineage_check.validate_authority(value)
    except lineage_check.LineageError as error:
        raise PromotionError("registry lineage authority is invalid") from error
    match = ALPHA.fullmatch(version)
    successor = value["successor"]
    core = tuple(int(part) for part in successor["functionalAlphaCore"].split("."))
    requested = tuple(int(part) for part in match.groups()) if match else ()
    if (
        requested[:3] != core
        or not requested
        or requested[3] < successor["minimumSequence"]
    ):
        raise PromotionError("version is outside the pinned functional-alpha lineage")
    meta = value["metaPackage"]
    return Lineage(latest=meta["latest"], owners=tuple(meta["owners"]))


def _settlement_package(package: PackageArtifact) -> dict[str, Any]:
    return {
        "name": package.name,
        "role": package.role,
        "artifact": package.artifact,
        "sha256": package.sha256,
        "shasum": package.shasum,
        "integrity": package.integrity,
        "attempted": False,
        "action": "none",
        "outcome": "not-attempted",
        "registryIntegrity": None,
        "provenancePredicateType": None,
    }


def _attestation_settlement(candidate: Candidate) -> dict[str, Any]:
    return {
        "status": "not-attempted",
        "repository": candidate.repository,
        "sourceSha": candidate.source_sha,
        "sourceRef": "refs/heads/main",
        "predicateType": PROVENANCE_PREDICATE,
        "signerWorkflow": f"{candidate.repository}/{SIGNER_WORKFLOW_PATH}",
        "signerDigest": candidate.source_sha,
        "expectedAssetCount": len(candidate.assets),
        "verifiedAssetCount": 0,
        "bytesReauthenticated": False,
        "workflowRunId": None,
        "workflowRunAttempt": None,
        "evidenceSha256": None,
        "tool": None,
        "assets": [
            {
                "name": asset.name,
                "byteLength": asset.byte_length,
                "sha256": asset.sha256,
                "outcome": "not-attempted",
            }
            for asset in candidate.assets
        ],
    }


def new_settlement(
    *,
    operation: str,
    candidate: Candidate,
    lineage: Lineage,
    draft_authority: dict[str, Any],
) -> dict[str, Any]:
    timestamp = _now()
    return {
        "schema": SCHEMA,
        "operation": operation,
        "status": "initialized",
        "version": candidate.version,
        "sourceSha": candidate.source_sha,
        "repository": candidate.repository,
        "releaseId": candidate.release_id,
        "tag": candidate.tag,
        "registry": REGISTRY,
        "stableLatest": lineage.latest,
        "alphaTag": ALPHA_TAG,
        "assemblySha256": candidate.assembly_sha256,
        "draftAuthority": dict(draft_authority),
        "startedAt": timestamp,
        "updatedAt": timestamp,
        "githubArtifactAttestations": _attestation_settlement(candidate),
        "npmMutationToolchain": None,
        "packages": [_settlement_package(package) for package in candidate.packages],
        "github": {
            "draftVerified": True,
            "promotionAttempted": False,
            "promoted": False,
            "outcome": "not-attempted",
        },
    }


def validate_settlement(value: Any) -> dict[str, Any]:
    value = _exact_object(value, SETTLEMENT_FIELDS, "publication settlement")
    if (
        value.get("schema") != SCHEMA
        or value.get("operation") not in OPERATIONS
        or value.get("status")
        not in {
            "initialized",
            "in-progress",
            "awaiting-platform-approval",
            "awaiting-meta-approval",
            "npm-settled",
            "complete",
            "failed",
            "ambiguous",
            "partial-publication",
        }
        or not isinstance(value.get("version"), str)
        or ALPHA.fullmatch(value["version"]) is None
        or not isinstance(value.get("sourceSha"), str)
        or FULL_SHA.fullmatch(value["sourceSha"]) is None
        or not isinstance(value.get("repository"), str)
        or REPOSITORY.fullmatch(value["repository"]) is None
        or not isinstance(value.get("releaseId"), int)
        or isinstance(value["releaseId"], bool)
        or value["releaseId"] <= 0
        or value.get("tag") != f"cli-v{value['version']}"
        or value.get("registry") != REGISTRY
        or value.get("alphaTag") != ALPHA_TAG
        or not isinstance(value.get("stableLatest"), str)
        or not isinstance(value.get("assemblySha256"), str)
        or SHA256.fullmatch(value["assemblySha256"]) is None
        or not isinstance(value.get("startedAt"), str)
        or not isinstance(value.get("updatedAt"), str)
    ):
        raise PromotionError("publication settlement identity is malformed")
    attestation = _exact_object(
        value.get("githubArtifactAttestations"),
        ATTESTATION_SETTLEMENT_FIELDS,
        "settlement GitHub artifact attestations",
    )
    expected_assets = expected_asset_names(value["version"])
    attestation_assets = attestation.get("assets")
    if (
        attestation.get("status") not in {"not-attempted", "verified", "failed"}
        or attestation.get("repository") != value["repository"]
        or attestation.get("sourceSha") != value["sourceSha"]
        or attestation.get("sourceRef") != "refs/heads/main"
        or attestation.get("predicateType") != PROVENANCE_PREDICATE
        or attestation.get("signerWorkflow")
        != f"{value['repository']}/{SIGNER_WORKFLOW_PATH}"
        or attestation.get("signerDigest") != value["sourceSha"]
        or attestation.get("expectedAssetCount") != len(expected_assets)
        or not isinstance(attestation.get("verifiedAssetCount"), int)
        or isinstance(attestation["verifiedAssetCount"], bool)
        or not isinstance(attestation.get("bytesReauthenticated"), bool)
        or (
            attestation.get("workflowRunId") is not None
            and (
                not isinstance(attestation["workflowRunId"], int)
                or isinstance(attestation["workflowRunId"], bool)
                or attestation["workflowRunId"] <= 0
            )
        )
        or (
            attestation.get("workflowRunAttempt") is not None
            and (
                not isinstance(attestation["workflowRunAttempt"], int)
                or isinstance(attestation["workflowRunAttempt"], bool)
                or attestation["workflowRunAttempt"] <= 0
            )
        )
        or (
            attestation.get("evidenceSha256") is not None
            and (
                not isinstance(attestation["evidenceSha256"], str)
                or SHA256.fullmatch(attestation["evidenceSha256"]) is None
            )
        )
        or sum(
            value is not None
            for value in (
                attestation.get("workflowRunId"),
                attestation.get("workflowRunAttempt"),
                attestation.get("evidenceSha256"),
            )
        )
        not in {0, 3}
        or not isinstance(attestation_assets, list)
        or len(attestation_assets) != len(expected_assets)
    ):
        raise PromotionError("settlement GitHub attestation identity is malformed")
    verified_assets = 0
    for index, item in enumerate(attestation_assets):
        item = _exact_object(
            item, ATTESTATION_ASSET_FIELDS, "settlement GitHub attestation asset"
        )
        if (
            item.get("name") != expected_assets[index]
            or not isinstance(item.get("byteLength"), int)
            or isinstance(item["byteLength"], bool)
            or not 1 <= item["byteLength"] <= draft.MAX_ALPHA_ASSET_BYTES
            or not isinstance(item.get("sha256"), str)
            or SHA256.fullmatch(item["sha256"]) is None
            or item.get("outcome") not in {"not-attempted", "verified", "failed"}
        ):
            raise PromotionError("settlement GitHub attestation asset is malformed")
        verified_assets += int(item["outcome"] == "verified")
    _validate_draft_authority_identity(
        value.get("draftAuthority"),
        source_sha=value["sourceSha"],
        assets=attestation_assets,
    )
    if attestation["verifiedAssetCount"] != verified_assets:
        raise PromotionError("settlement GitHub attestation count is inconsistent")
    attestation_by_name = {item["name"]: item for item in attestation_assets}
    if attestation_by_name["SHA256SUMS"]["sha256"] != value["assemblySha256"]:
        raise PromotionError("settlement assembly and attestation digests differ")
    attestation_tool = attestation.get("tool")
    if attestation_tool is not None:
        _validate_attestation_tool(attestation_tool)
    attestation_status = attestation["status"]
    failed_assets = sum(item["outcome"] == "failed" for item in attestation_assets)
    has_evidence_identity = (
        attestation["workflowRunId"] is not None
        and attestation["workflowRunAttempt"] is not None
        and attestation["evidenceSha256"] is not None
    )
    if (
        (
            attestation_status == "not-attempted"
            and (
                attestation_tool is not None
                or verified_assets
                or failed_assets
                or attestation["bytesReauthenticated"]
                or has_evidence_identity
            )
        )
        or (
            attestation_status == "verified"
            and (
                attestation_tool is None
                or not has_evidence_identity
                or verified_assets != len(expected_assets)
                or failed_assets
                or not attestation["bytesReauthenticated"]
            )
        )
        or (
            attestation_status == "failed"
            and (
                attestation_tool is None
                or not has_evidence_identity
                or verified_assets != len(expected_assets)
                or failed_assets
                or attestation["bytesReauthenticated"]
            )
        )
    ):
        raise PromotionError("settlement GitHub attestation state is inconsistent")
    packages = value.get("packages")
    if not isinstance(packages, list) or len(packages) != len(PACKAGE_ORDER):
        raise PromotionError("publication settlement package inventory is not closed")
    for index, package in enumerate(packages):
        item = _exact_object(package, PACKAGE_SETTLEMENT_FIELDS, "settlement package")
        expected_name = PACKAGE_ORDER[index]
        expected_role = "meta" if expected_name == META_PACKAGE else "platform"
        if (
            item.get("name") != expected_name
            or item.get("role") != expected_role
            or not isinstance(item.get("artifact"), str)
            or SAFE_ARTIFACT.fullmatch(item["artifact"]) is None
            or not isinstance(item.get("sha256"), str)
            or SHA256.fullmatch(item["sha256"]) is None
            or not isinstance(item.get("shasum"), str)
            or SHA1.fullmatch(item["shasum"]) is None
            or not isinstance(item.get("integrity"), str)
            or INTEGRITY.fullmatch(item["integrity"]) is None
            or not isinstance(item.get("attempted"), bool)
            or item.get("action") not in {"none", "publish", "stage", "verify"}
            or item.get("outcome")
            not in {
                "not-attempted",
                "published",
                "staged",
                "verified",
                "failed",
                "ambiguous",
            }
            or (
                item.get("registryIntegrity") is not None
                and (
                    not isinstance(item["registryIntegrity"], str)
                    or INTEGRITY.fullmatch(item["registryIntegrity"]) is None
                )
            )
            or item.get("provenancePredicateType") not in {None, PROVENANCE_PREDICATE}
        ):
            raise PromotionError("publication settlement package identity is malformed")
        platform = expected_name.removeprefix("@openprose/prose-cli-")
        expected_artifact = (
            f"openprose-prose-cli-{value['version']}.tgz"
            if expected_role == "meta"
            else f"openprose-prose-cli-{platform}-{value['version']}.tgz"
        )
        if item["artifact"] != expected_artifact:
            raise PromotionError(
                "publication settlement artifact identity is not exact"
            )
        if attestation_by_name[item["artifact"]]["sha256"] != item["sha256"]:
            raise PromotionError(
                "publication settlement package and attestation digests differ"
            )
        action = item["action"]
        outcome = item["outcome"]
        attempted = item["attempted"]
        registry_settled = (
            item["registryIntegrity"] == item["integrity"]
            and item["provenancePredicateType"] == PROVENANCE_PREDICATE
        )
        if (
            (
                action == "none"
                and (
                    attempted
                    or outcome != "not-attempted"
                    or item["registryIntegrity"] is not None
                    or item["provenancePredicateType"] is not None
                )
            )
            or (
                action == "verify"
                and (attempted or outcome != "verified" or not registry_settled)
            )
            or (
                action == "publish"
                and (
                    not attempted
                    or outcome not in {"published", "failed", "ambiguous"}
                    or (outcome == "published" and not registry_settled)
                    or (
                        outcome != "published"
                        and (
                            item["registryIntegrity"] is not None
                            or item["provenancePredicateType"] is not None
                        )
                    )
                )
            )
            or (
                action == "stage"
                and (
                    not attempted
                    or outcome not in {"staged", "failed", "ambiguous"}
                    or item["registryIntegrity"] is not None
                    or item["provenancePredicateType"] is not None
                )
            )
        ):
            raise PromotionError("publication settlement package state is inconsistent")
    toolchain = value.get("npmMutationToolchain")
    if toolchain is not None:
        toolchain = _exact_object(
            toolchain, NPM_TOOLCHAIN_FIELDS, "settlement npm mutation toolchain"
        )
        if (
            toolchain.get("npmVersion") != NPM_VERSION
            or toolchain.get("npmTarballUrl") != PINNED_NPM_AUTHORITY.url
            or toolchain.get("npmTarballByteLength") != PINNED_NPM_AUTHORITY.byte_length
            or toolchain.get("npmTarballSha1") != PINNED_NPM_AUTHORITY.sha1
            or toolchain.get("npmTarballSha256") != PINNED_NPM_AUTHORITY.sha256
            or toolchain.get("npmTarballSha512") != PINNED_NPM_AUTHORITY.sha512
            or toolchain.get("npmTarballIntegrity") != PINNED_NPM_AUTHORITY.integrity
            or toolchain.get("npmTreeSha256") != PINNED_NPM_AUTHORITY.tree_sha256
            or toolchain.get("npmEntrySha256") != PINNED_NPM_AUTHORITY.entry_sha256
            or toolchain.get("nodeVersion") != NODE_VERSION
            or not isinstance(toolchain.get("nodeExecutableSha256"), str)
            or SHA256.fullmatch(toolchain["nodeExecutableSha256"]) is None
        ):
            raise PromotionError("settlement npm mutation toolchain is malformed")
    attempted_npm = any(package["attempted"] for package in packages)
    if (value["operation"] == "settle-and-promote" and toolchain is not None) or (
        attempted_npm and toolchain is None
    ):
        raise PromotionError("settlement npm mutation toolchain state is inconsistent")
    github = _exact_object(
        value.get("github"), GITHUB_SETTLEMENT_FIELDS, "settlement GitHub state"
    )
    if (
        not isinstance(github.get("draftVerified"), bool)
        or not isinstance(github.get("promoted"), bool)
        or not isinstance(github.get("promotionAttempted"), bool)
        or github.get("outcome") not in {"not-attempted", "promoted", "ambiguous"}
    ):
        raise PromotionError("publication settlement GitHub state is malformed")
    expected_github_state = {
        "not-attempted": (False, False),
        "ambiguous": (True, False),
        "promoted": (True, True),
    }[github["outcome"]]
    if (
        github["promotionAttempted"],
        github["promoted"],
    ) != expected_github_state:
        raise PromotionError("publication settlement GitHub state is inconsistent")
    _validate_settlement_coherence(
        value=value,
        attestation=attestation,
        packages=packages,
        toolchain=toolchain,
        github=github,
    )
    return value


def _validate_settlement_coherence(
    *,
    value: dict[str, Any],
    attestation: dict[str, Any],
    packages: list[dict[str, Any]],
    toolchain: dict[str, Any] | None,
    github: dict[str, Any],
) -> None:
    """Bind each persisted status to one operation-specific evidence state."""

    operation = value["operation"]
    status = value["status"]
    attestation_status = attestation["status"]

    def package_is(
        item: dict[str, Any],
        *,
        action: str,
        attempted: bool,
        outcome: str,
    ) -> bool:
        return (
            item["action"] == action
            and item["attempted"] is attempted
            and item["outcome"] == outcome
        )

    def all_packages(
        selected: Sequence[dict[str, Any]],
        *,
        action: str,
        attempted: bool,
        outcome: str,
    ) -> bool:
        return all(
            package_is(item, action=action, attempted=attempted, outcome=outcome)
            for item in selected
        )

    github_idle = (
        github["draftVerified"] is True
        and github["promotionAttempted"] is False
        and github["promoted"] is False
        and github["outcome"] == "not-attempted"
    )
    github_ambiguous = (
        github["draftVerified"] is True
        and github["promotionAttempted"] is True
        and github["promoted"] is False
        and github["outcome"] == "ambiguous"
    )
    github_promoted = (
        github["draftVerified"] is True
        and github["promotionAttempted"] is True
        and github["promoted"] is True
        and github["outcome"] == "promoted"
    )
    if not github["draftVerified"]:
        raise PromotionError("publication settlement status is inconsistent")

    actions = tuple(item["action"] for item in packages)
    if operation == "bootstrap":
        operation_exact = (
            all(action in {"none", "publish"} for action in actions) and github_idle
        )
    elif operation == "stage-platforms":
        operation_exact = (
            all(action in {"none", "stage"} for action in actions[:-1])
            and actions[-1] == "none"
            and github_idle
        )
    elif operation == "stage-meta":
        operation_exact = (
            all(action in {"none", "verify"} for action in actions[:-1])
            and actions[-1] in {"none", "stage"}
            and github_idle
        )
    else:
        operation_exact = (
            all(action in {"none", "verify"} for action in actions)
            and toolchain is None
        )
    if not operation_exact:
        raise PromotionError("publication settlement operation is inconsistent")

    none_pending = all_packages(
        packages, action="none", attempted=False, outcome="not-attempted"
    )
    platforms_staged = all_packages(
        packages[:-1], action="stage", attempted=True, outcome="staged"
    )
    platforms_verified = all_packages(
        packages[:-1], action="verify", attempted=False, outcome="verified"
    )
    meta_pending = package_is(
        packages[-1], action="none", attempted=False, outcome="not-attempted"
    )
    meta_staged = package_is(
        packages[-1], action="stage", attempted=True, outcome="staged"
    )
    all_published = all_packages(
        packages, action="publish", attempted=True, outcome="published"
    )
    all_verified = all_packages(
        packages, action="verify", attempted=False, outcome="verified"
    )

    def ordered_progress(
        selected: Sequence[dict[str, Any]],
        *,
        completed_action: str,
        completed_attempted: bool,
        completed_outcome: str,
        ambiguous_action: str | None,
    ) -> bool:
        index = 0
        while index < len(selected) and package_is(
            selected[index],
            action=completed_action,
            attempted=completed_attempted,
            outcome=completed_outcome,
        ):
            index += 1
        if (
            ambiguous_action is not None
            and index < len(selected)
            and package_is(
                selected[index],
                action=ambiguous_action,
                attempted=True,
                outcome="ambiguous",
            )
        ):
            index += 1
        return all_packages(
            selected[index:],
            action="none",
            attempted=False,
            outcome="not-attempted",
        )

    def operation_progress(*, allow_ambiguous: bool) -> bool:
        ambiguous = (
            {
                "bootstrap": "publish",
                "stage-platforms": "stage",
            }[operation]
            if allow_ambiguous
            and operation
            in {
                "bootstrap",
                "stage-platforms",
            }
            else None
        )
        if operation == "bootstrap":
            return ordered_progress(
                packages,
                completed_action="publish",
                completed_attempted=True,
                completed_outcome="published",
                ambiguous_action=ambiguous,
            )
        if operation == "stage-platforms":
            return meta_pending and ordered_progress(
                packages[:-1],
                completed_action="stage",
                completed_attempted=True,
                completed_outcome="staged",
                ambiguous_action=ambiguous,
            )
        if operation == "stage-meta":
            platforms = ordered_progress(
                packages[:-1],
                completed_action="verify",
                completed_attempted=False,
                completed_outcome="verified",
                ambiguous_action=None,
            )
            meta_progress = meta_pending or (
                allow_ambiguous
                and package_is(
                    packages[-1],
                    action="stage",
                    attempted=True,
                    outcome="ambiguous",
                )
                and platforms_verified
            )
            return platforms and meta_progress
        return ordered_progress(
            packages,
            completed_action="verify",
            completed_attempted=False,
            completed_outcome="verified",
            ambiguous_action=None,
        )

    if status == "initialized":
        coherent = (
            attestation_status == "not-attempted"
            and toolchain is None
            and none_pending
            and github_idle
        )
    elif status == "in-progress":
        coherent = (
            attestation_status == "verified"
            and github["outcome"] in {"not-attempted", "ambiguous"}
            and (
                github["outcome"] != "ambiguous"
                or (operation == "settle-and-promote" and all_verified)
            )
            and (toolchain is None or attestation_status == "verified")
            and (
                not any(action != "none" for action in actions)
                or attestation_status == "verified"
            )
            and operation_progress(allow_ambiguous=True)
        )
    elif status == "awaiting-platform-approval":
        coherent = (
            operation == "stage-platforms"
            and attestation_status == "verified"
            and toolchain is not None
            and platforms_staged
            and meta_pending
            and github_idle
        )
    elif status == "awaiting-meta-approval":
        coherent = (
            operation == "stage-meta"
            and attestation_status == "verified"
            and toolchain is not None
            and platforms_verified
            and meta_staged
            and github_idle
        )
    elif status == "npm-settled":
        coherent = (
            operation == "bootstrap"
            and attestation_status == "verified"
            and toolchain is not None
            and all_published
            and github_idle
        )
    elif status == "complete":
        coherent = (
            operation == "settle-and-promote"
            and attestation_status == "verified"
            and toolchain is None
            and all_verified
            and github_promoted
        )
    elif status == "failed":
        coherent = (
            attestation_status in {"failed", "verified"}
            and github_idle
            and not any(item["outcome"] == "ambiguous" for item in packages)
            and operation_progress(allow_ambiguous=False)
            and (operation != "bootstrap" or none_pending)
        )
    elif status == "ambiguous":
        staged_ambiguity = (
            operation in {"stage-platforms", "stage-meta"}
            and attestation_status == "verified"
            and toolchain is not None
            and github_idle
            and any(item["outcome"] == "ambiguous" for item in packages)
            and operation_progress(allow_ambiguous=True)
        )
        promotion_ambiguity = (
            operation == "settle-and-promote"
            and attestation_status == "verified"
            and toolchain is None
            and all_verified
            and github_ambiguous
        )
        coherent = staged_ambiguity or promotion_ambiguity
    else:
        coherent = (
            status == "partial-publication"
            and operation == "bootstrap"
            and attestation_status in {"failed", "verified"}
            and toolchain is not None
            and github_idle
            and any(
                item["attempted"] and item["action"] == "publish" for item in packages
            )
            and operation_progress(allow_ambiguous=True)
        )
    if not coherent:
        raise PromotionError("publication settlement status is inconsistent")


class SettlementWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def refuse_attempted_retry(self) -> None:
        if not self.path.exists() and not self.path.is_symlink():
            return
        encoded = _regular_bytes(self.path, 1024 * 1024)
        value = validate_settlement(
            _closed_json(encoded, "publication settlement", 1024 * 1024)
        )
        if (
            any(package["attempted"] for package in value["packages"])
            or value["github"]["promotionAttempted"]
        ):
            raise PromotionError(
                "this settlement records a prior mutation attempt; "
                "use a new alpha version"
            )
        raise PromotionError(
            "a settlement already exists; refuse ambiguous replacement"
        )

    def write(self, value: dict[str, Any]) -> None:
        validate_settlement(value)
        value["updatedAt"] = _now()
        encoded = (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise PromotionError("settlement directory is unsafe")
        try:
            os.chmod(parent, 0o700)
        except OSError as error:
            raise PromotionError("settlement directory cannot be hardened") from error
        if self.path.is_symlink():
            raise PromotionError("settlement path must not be a symlink")
        temporary = parent / f".{self.path.name}.{os.getpid()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                _write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.path)
            directory_descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise PromotionError(
                "publication settlement cannot be written atomically"
            ) from error


class AttestationEvidenceWriter:
    """Write one immutable, canonical attestation handoff without overwrite."""

    def __init__(self, path: Path) -> None:
        if path.name != ATTESTATION_EVIDENCE_FILENAME:
            raise PromotionError("attestation evidence must use the canonical filename")
        self.path = path

    def refuse_existing(self) -> None:
        if self.path.exists() or self.path.is_symlink():
            raise PromotionError(
                "attestation evidence already exists; refuse ambiguous replacement"
            )

    def write(self, value: dict[str, Any]) -> str:
        validate_attestation_evidence(value)
        encoded = _encode_attestation_evidence(value)
        if len(encoded) > MAX_ATTESTATION_EVIDENCE_BYTES:
            raise PromotionError("attestation evidence exceeds its byte limit")
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise PromotionError("attestation evidence directory is unsafe")
        try:
            os.chmod(parent, 0o700)
        except OSError as error:
            raise PromotionError(
                "attestation evidence directory cannot be hardened"
            ) from error
        self.refuse_existing()
        temporary = parent / f".{self.path.name}.{os.getpid()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                _write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.link(temporary, self.path, follow_symlinks=False)
            directory_descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            temporary.unlink()
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise PromotionError(
                "attestation evidence cannot be written atomically"
            ) from error
        return hashlib.sha256(encoded).hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, fp, code, msg, headers, newurl
    ):
        return None


class StrictHttp:
    """Bounded HTTPS client with no implicit redirect or credential forwarding."""

    def __init__(self) -> None:
        self._open = build_opener(_NoRedirect()).open

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        expected: frozenset[int] = frozenset({200}),
        maximum: int = MAX_JSON_BYTES,
        allow_missing: bool = False,
        github_asset_redirect: bool = False,
    ) -> tuple[int, bytes]:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise PromotionError("remote URL is not an exact HTTPS endpoint")
        request = Request(url, data=data, headers=headers or {}, method=method)
        try:
            response = self._open(request, timeout=HTTP_TIMEOUT_SECONDS)
        except HTTPError as error:
            if allow_missing and error.code == 404:
                return 404, b""
            if github_asset_redirect and error.code in {301, 302, 303, 307, 308}:
                location = error.headers.get("Location", "")
                redirected = urlparse(location)
                if (
                    redirected.scheme != "https"
                    or redirected.hostname
                    not in {
                        "release-assets.githubusercontent.com",
                        "objects.githubusercontent.com",
                    }
                    or redirected.username
                    or redirected.password
                    or redirected.fragment
                ):
                    raise PromotionError(
                        "GitHub asset redirect is outside the closed allowlist"
                    ) from error
                return self.request(
                    method="GET",
                    url=location,
                    headers={"User-Agent": "openprose-cli-alpha-promote/1"},
                    expected=frozenset({200}),
                    maximum=maximum,
                )
            raise PromotionError(
                "remote endpoint returned an unexpected status"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise PromotionError("remote endpoint outcome is ambiguous") from error
        try:
            status = response.getcode()
            if status not in expected:
                raise PromotionError("remote endpoint returned an unexpected status")
            body = response.read(maximum + 1)
        except OSError as error:
            raise PromotionError(
                "remote response could not be read completely"
            ) from error
        finally:
            response.close()
        if len(body) > maximum:
            raise PromotionError("remote response exceeds its byte limit")
        return status, body


class PublicNpmRegistry:
    def __init__(self, http: StrictHttp) -> None:
        self.http = http

    @staticmethod
    def _package_url(name: str) -> str:
        return f"{REGISTRY}/{quote(name, safe='')}"

    def package(self, name: str) -> RegistryPackage | None:
        if name not in PACKAGE_ORDER:
            raise PromotionError("registry package is outside the closed cohort")
        status, body = self.http.request(
            method="GET",
            url=self._package_url(name),
            headers={
                "Accept": "application/json",
                "User-Agent": "openprose-cli-alpha-promote/1",
            },
            allow_missing=True,
            maximum=MAX_JSON_BYTES,
        )
        if status == 404:
            return None
        value = _closed_json(body, "npm registry package metadata")
        if not isinstance(value, dict) or value.get("name") != name:
            raise PromotionError("npm registry package identity is malformed")
        tags = value.get("dist-tags")
        versions = value.get("versions")
        maintainers = value.get("maintainers")
        if (
            not isinstance(tags, dict)
            or not isinstance(versions, dict)
            or not isinstance(maintainers, list)
        ):
            raise PromotionError("npm registry package metadata is incomplete")
        parsed_tags: dict[str, str] = {}
        for key, version in tags.items():
            if not isinstance(key, str) or not isinstance(version, str):
                raise PromotionError("npm registry distribution tags are malformed")
            parsed_tags[key] = version
        owners: list[str] = []
        for maintainer in maintainers:
            if (
                not isinstance(maintainer, dict)
                or not isinstance(maintainer.get("name"), str)
                or not maintainer["name"]
            ):
                raise PromotionError("npm registry ownership metadata is malformed")
            owners.append(maintainer["name"])
        if len(owners) != len(set(owners)):
            raise PromotionError("npm registry ownership metadata is ambiguous")
        parsed_versions: dict[str, RegistryVersion] = {}
        for version, document in versions.items():
            if not isinstance(version, str) or not isinstance(document, dict):
                raise PromotionError("npm registry version metadata is malformed")
            dist = document.get("dist")
            if (
                document.get("name") != name
                or document.get("version") != version
                or not isinstance(dist, dict)
                or not isinstance(dist.get("shasum"), str)
                or SHA1.fullmatch(dist["shasum"]) is None
                or not isinstance(dist.get("integrity"), str)
                or INTEGRITY.fullmatch(dist["integrity"]) is None
                or not isinstance(dist.get("tarball"), str)
            ):
                raise PromotionError("npm registry version identity is malformed")
            optional = document.get("optionalDependencies")
            if optional is not None and (
                not isinstance(optional, dict)
                or any(
                    not isinstance(key, str) or not isinstance(item, str)
                    for key, item in optional.items()
                )
            ):
                raise PromotionError("npm registry dependency metadata is malformed")
            attestations = dist.get("attestations")
            predicate: str | None = None
            if attestations is not None:
                if not isinstance(attestations, dict):
                    raise PromotionError(
                        "npm registry provenance metadata is malformed"
                    )
                provenance = attestations.get("provenance")
                attestation_url = attestations.get("url")
                parsed_attestation_url = (
                    urlparse(attestation_url)
                    if isinstance(attestation_url, str)
                    else None
                )
                if (
                    not isinstance(provenance, dict)
                    or not isinstance(provenance.get("predicateType"), str)
                    or parsed_attestation_url is None
                    or parsed_attestation_url.scheme != "https"
                    or parsed_attestation_url.hostname != "registry.npmjs.org"
                    or parsed_attestation_url.username
                    or parsed_attestation_url.password
                    or parsed_attestation_url.fragment
                ):
                    raise PromotionError(
                        "npm registry provenance metadata is malformed"
                    )
                predicate = provenance["predicateType"]
            parsed_versions[version] = RegistryVersion(
                name=name,
                version=version,
                shasum=dist["shasum"],
                integrity=dist["integrity"],
                tarball_url=dist["tarball"],
                optional_dependencies=optional,
                provenance_predicate_type=predicate,
            )
        return RegistryPackage(
            name=name,
            owners=tuple(owners),
            tags=parsed_tags,
            versions=parsed_versions,
        )

    def tarball(self, url: str) -> bytes:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "registry.npmjs.org"
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise PromotionError(
                "npm registry tarball URL is outside the closed origin"
            )
        _, body = self.http.request(
            method="GET",
            url=url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "openprose-cli-alpha-promote/1",
            },
            maximum=MAX_PACKAGE_BYTES,
        )
        return body


def _github_headers(
    token: str, *, content_type: str = "application/vnd.github+json"
) -> dict[str, str]:
    if not token or "\r" in token or "\n" in token:
        raise PromotionError("GitHub token is unavailable or malformed")
    return {
        "Accept": content_type,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "openprose-cli-alpha-promote/1",
        "X-GitHub-Api-Version": draft.API_VERSION,
    }


class GitHubRelease:
    def __init__(
        self,
        *,
        repository: str,
        token: str,
        http: StrictHttp,
        allow_promotion: bool = False,
        draft_authority: DraftAuthority | None = None,
    ) -> None:
        if REPOSITORY.fullmatch(repository) is None:
            raise PromotionError("GitHub repository must be owner/name")
        self.repository = repository
        self.token = token
        self.http = http
        self.headers = _github_headers(token)
        self.allow_promotion = allow_promotion
        self.draft_authority = draft_authority

    def _json(
        self, method: str, url: str, *, data: bytes | None = None
    ) -> dict[str, Any]:
        _, encoded = self.http.request(
            method=method,
            url=url,
            headers=self.headers,
            data=data,
            expected=frozenset({200}),
            maximum=MAX_JSON_BYTES,
        )
        value = _closed_json(encoded, "GitHub API response")
        if not isinstance(value, dict):
            raise PromotionError("GitHub API response must be an object")
        return value

    def _release(
        self, release_id: int, version: str, *, draft_expected: bool
    ) -> dict[str, Any]:
        authority = self.draft_authority
        if (
            authority is None
            or authority.repository != self.repository
            or authority.release_id != release_id
            or authority.version != version
            or authority.tag != f"cli-v{version}"
        ):
            raise PromotionError("immutable draft authority is unavailable or mismatched")
        value = self._json(
            "GET",
            f"https://api.github.com/repos/{self.repository}/releases/{release_id}",
        )
        if (
            value.get("id") != release_id
            or value.get("url")
            != f"https://api.github.com/repos/{self.repository}/releases/{release_id}"
            or value.get("assets_url")
            != f"https://api.github.com/repos/{self.repository}/releases/{release_id}/assets"
            or value.get("tag_name") != f"cli-v{version}"
            or value.get("name") != f"OpenProse CLI v{version} functional alpha"
            or value.get("draft") is not draft_expected
            or value.get("prerelease") is not True
            or value.get("immutable") is not False
            or not isinstance(value.get("body"), str)
        ):
            raise PromotionError("GitHub release identity is not exact")
        body = value["body"].encode("utf-8")
        if (
            len(body) != authority.body_byte_length
            or hashlib.sha256(body).hexdigest() != authority.body_sha256
        ):
            raise PromotionError("GitHub release body differs from draft authority")
        return value

    def _resolve_tag(self, version: str, source_sha: str) -> None:
        try:
            draft._resolve_existing_tag(
                repository=self.repository,
                tag=f"cli-v{version}",
                source_sha=source_sha,
                headers=self.headers,
                opener=self.http._open,
            )
        except draft.DraftReleaseError as error:
            raise PromotionError(
                "GitHub release tag does not resolve to the exact source"
            ) from error

    def _asset_identities(
        self, *, release_id: int, version: str
    ) -> tuple[tuple[ReleaseAsset, ...], dict[str, str]]:
        expected = set(expected_asset_names(version))
        assets_url = (
            f"https://api.github.com/repos/{self.repository}/releases/"
            f"{release_id}/assets?per_page=100&page=1"
        )
        _, encoded = self.http.request(
            method="GET", url=assets_url, headers=self.headers, maximum=MAX_JSON_BYTES
        )
        values = _closed_json(encoded, "GitHub release asset inventory")
        if not isinstance(values, list) or len(values) != len(expected):
            raise PromotionError(
                "GitHub draft asset inventory is not the exact admitted set"
            )
        records: dict[str, ReleaseAsset] = {}
        urls: dict[str, str] = {}
        ids: set[int] = set()
        for value in values:
            if not isinstance(value, dict):
                raise PromotionError("GitHub release asset identity is malformed")
            name = value.get("name")
            asset_id = value.get("id")
            digest = value.get("digest")
            if (
                not isinstance(name, str)
                or name not in expected
                or name in records
                or not isinstance(asset_id, int)
                or isinstance(asset_id, bool)
                or asset_id <= 0
                or asset_id in ids
                or value.get("url")
                != f"https://api.github.com/repos/{self.repository}/releases/assets/{asset_id}"
                or value.get("state") != "uploaded"
                or value.get("content_type") != "application/octet-stream"
                or not isinstance(value.get("size"), int)
                or value["size"] <= 0
                or value["size"] > draft.MAX_ALPHA_ASSET_BYTES
                or not isinstance(digest, str)
                or not digest.startswith("sha256:")
                or SHA256.fullmatch(digest.removeprefix("sha256:")) is None
            ):
                raise PromotionError("GitHub release asset identity is not exact")
            records[name] = ReleaseAsset(
                name=name,
                byte_length=value["size"],
                sha256=digest.removeprefix("sha256:"),
            )
            urls[name] = value["url"]
            ids.add(asset_id)
        if set(records) != expected:
            raise PromotionError("GitHub draft asset inventory is not closed")
        return tuple(records[name] for name in sorted(records)), urls

    def download_candidate(
        self, *, release_id: int, version: str, source_sha: str, root: Path
    ) -> Candidate:
        self._release(release_id, version, draft_expected=True)
        self._resolve_tag(version, source_sha)
        identities, urls = self._asset_identities(
            release_id=release_id, version=version
        )
        if identities != self.draft_authority.assets:
            raise PromotionError("GitHub draft assets differ from draft authority")
        records = {asset.name: asset for asset in identities}
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chmod(root, 0o700)
        aggregate = 0
        for name in sorted(records):
            record = records[name]
            _, body = self.http.request(
                method="GET",
                url=urls[name],
                headers=_github_headers(
                    self.token, content_type="application/octet-stream"
                ),
                maximum=draft.MAX_ALPHA_ASSET_BYTES,
                github_asset_redirect=True,
            )
            aggregate += len(body)
            if (
                len(body) != record.byte_length
                or hashlib.sha256(body).hexdigest() != record.sha256
                or aggregate > draft.MAX_ASSEMBLY_BYTES
            ):
                raise PromotionError(
                    "downloaded GitHub release asset bytes do not match metadata"
                )
            destination = root / name
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                _write_all(descriptor, body)
            finally:
                os.close(descriptor)
        candidate = authenticate_assembly(
            root=root,
            repository=self.repository,
            release_id=release_id,
            version=version,
            source_sha=source_sha,
        )
        require_draft_authority_candidate(self.draft_authority, candidate)
        self._release(release_id, version, draft_expected=True)
        self._resolve_tag(version, source_sha)
        return candidate

    def promote(self, candidate: Candidate) -> None:
        if not self.allow_promotion:
            raise PromotionError("GitHub release mutation authority is unavailable")
        self._release(candidate.release_id, candidate.version, draft_expected=True)
        self._resolve_tag(candidate.version, candidate.source_sha)
        identities, _ = self._asset_identities(
            release_id=candidate.release_id, version=candidate.version
        )
        if identities != candidate.assets or identities != self.draft_authority.assets:
            raise PromotionError("GitHub draft assets changed after authentication")
        payload = json.dumps(
            {"draft": False, "prerelease": True}, separators=(",", ":")
        ).encode("ascii")
        url = (
            f"https://api.github.com/repos/{self.repository}/releases/"
            f"{candidate.release_id}"
        )
        try:
            self._json("PATCH", url, data=payload)
        except PromotionError:
            # A lost response is reconciled by the exact read below. Anything other
            # than the exact public prerelease remains an ambiguous hard failure.
            pass
        self._release(candidate.release_id, candidate.version, draft_expected=False)
        self._resolve_tag(candidate.version, candidate.source_sha)
        identities, _ = self._asset_identities(
            release_id=candidate.release_id, version=candidate.version
        )
        if identities != candidate.assets or identities != self.draft_authority.assets:
            raise PromotionError("GitHub prerelease assets changed during promotion")


def _parse_numeric_version(value: str, label: str) -> tuple[int, ...]:
    normalized = value.removeprefix("v")
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", normalized) is None:
        raise PromotionError(f"{label} version output is malformed")
    return tuple(int(part) for part in normalized.split("."))


def _tool_regular_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise PromotionError(f"{label} is unavailable") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size < 0
        or before.st_size > maximum
    ):
        raise PromotionError(f"{label} is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PromotionError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PromotionError(f"{label} changed before open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise PromotionError(f"{label} exceeds its byte limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or total != opened.st_size:
        raise PromotionError(f"{label} changed while being read")
    return b"".join(chunks)


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: bytes


class ProcessBoundary(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        environment: dict[str, str],
        timeout_seconds: int,
        maximum_output: int,
    ) -> ProcessResult:
        ...


class BoundedProcess:
    """Run one direct argv with bounded output, time, and group cleanup."""

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: dict[str, str],
        timeout_seconds: int,
        maximum_output: int,
    ) -> ProcessResult:
        if not argv or timeout_seconds <= 0 or maximum_output <= 0:
            raise PromotionError("attestation process inputs are malformed")
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            raise PromotionError("attestation verifier could not start") from error
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout = bytearray()
        stderr_length = 0
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        oversized = False
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                events = selector.select(min(remaining, 0.25))
                if not events and process.poll() is not None:
                    events = [
                        (key, selectors.EVENT_READ)
                        for key in selector.get_map().values()
                    ]
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout.extend(chunk)
                    else:
                        stderr_length += len(chunk)
                    if len(stdout) > maximum_output or stderr_length > maximum_output:
                        oversized = True
                        break
                if oversized:
                    break
            if timed_out or oversized:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
                raise PromotionError(
                    "attestation verifier outcome is ambiguous"
                ) from error
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        if timed_out:
            raise PromotionError("attestation verifier outcome is ambiguous")
        if oversized:
            raise PromotionError("attestation verifier output exceeded its limit")
        return ProcessResult(exit_code=exit_code, stdout=bytes(stdout))


class GitHubAttestationVerifier:
    """Authenticate the closed draft assets with the externally supplied gh."""

    def __init__(
        self,
        *,
        asset_root: Path,
        executable: str,
        token: str,
        process: ProcessBoundary | None = None,
    ) -> None:
        if not os.path.isabs(executable):
            raise PromotionError("GitHub CLI path must be absolute")
        if not token or "\r" in token or "\n" in token:
            raise PromotionError("GitHub token is unavailable or malformed")
        try:
            root = asset_root.resolve(strict=True)
            root_metadata = root.lstat()
        except OSError as error:
            raise PromotionError("attestation asset root is unavailable") from error
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
            root_metadata.st_mode
        ):
            raise PromotionError("attestation asset root is unsafe")
        self.asset_root = root
        self.executable = Path(executable)
        self.token = token
        self.process = process or BoundedProcess()
        self._tool_identity: dict[str, Any] | None = None
        self._temporary = TemporaryDirectory(prefix="openprose-alpha-gh-")
        self.config_root = Path(self._temporary.name)

    def close(self) -> None:
        self._temporary.cleanup()

    def _environment(self) -> dict[str, str]:
        return {
            "GH_TOKEN": self.token,
            "GH_HOST": "github.com",
            "GH_CONFIG_DIR": str(self.config_root),
            "HOME": str(self.config_root),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
        }

    def _material_identity(self) -> dict[str, Any]:
        try:
            resolved = self.executable.resolve(strict=True)
        except OSError as error:
            raise PromotionError("GitHub CLI is unavailable") from error
        if resolved != self.executable:
            raise PromotionError("GitHub CLI path is not canonical")
        encoded = _tool_regular_bytes(
            self.executable, MAX_GH_BYTES, "GitHub CLI executable"
        )
        if not encoded or not os.access(self.executable, os.X_OK):
            raise PromotionError("GitHub CLI is not a regular executable")
        return {
            "authority": GH_TOOL_AUTHORITY,
            "executableByteLength": len(encoded),
            "executableSha256": hashlib.sha256(encoded).hexdigest(),
        }

    def _run(self, argv: Sequence[str], *, maximum: int) -> ProcessResult:
        try:
            return self.process.run(
                list(argv),
                environment=self._environment(),
                timeout_seconds=ATTESTATION_TIMEOUT_SECONDS,
                maximum_output=maximum,
            )
        except PromotionError:
            raise
        except (OSError, subprocess.SubprocessError, TimeoutError) as error:
            raise PromotionError("attestation verifier outcome is ambiguous") from error

    def require_toolchain(self) -> dict[str, Any]:
        if self._tool_identity is not None:
            raise PromotionError("GitHub CLI identity was already authenticated")
        before = self._material_identity()
        result = self._run([str(self.executable), "--version"], maximum=4096)
        if result.exit_code != 0:
            raise PromotionError("GitHub CLI version probe failed")
        try:
            first = result.stdout.decode("ascii").splitlines()[0]
        except (UnicodeDecodeError, IndexError) as error:
            raise PromotionError("GitHub CLI version output is malformed") from error
        match = re.fullmatch(
            r"gh version ((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
            r"(?:0|[1-9][0-9]*))(?: \([^\r\n]{1,128}\))?",
            first,
        )
        if match is None:
            raise PromotionError("GitHub CLI version output is malformed")
        after = self._material_identity()
        if before != after:
            raise PromotionError("GitHub CLI changed during authentication")
        identity = {**after, "version": match.group(1)}
        self._tool_identity = identity
        return dict(identity)

    def _require_tool_identity(self) -> dict[str, Any]:
        if self._tool_identity is None:
            raise PromotionError("GitHub CLI identity is not authenticated")
        observed = self._material_identity()
        expected = {
            key: value for key, value in self._tool_identity.items() if key != "version"
        }
        if observed != expected:
            raise PromotionError("GitHub CLI identity changed after authentication")
        return observed

    def _path(self, asset: ReleaseAsset) -> Path:
        if SAFE_ARTIFACT.fullmatch(asset.name) is None:
            raise PromotionError("attestation subject name is unsafe")
        path = self.asset_root / asset.name
        body = _regular_bytes(path, draft.MAX_ALPHA_ASSET_BYTES)
        if (
            len(body) != asset.byte_length
            or hashlib.sha256(body).hexdigest() != asset.sha256
        ):
            raise PromotionError("attestation subject bytes changed")
        return path

    def reauthenticate(self, candidate: Candidate) -> None:
        expected = expected_asset_names(candidate.version)
        if tuple(asset.name for asset in candidate.assets) != expected:
            raise PromotionError("attestation candidate inventory is not closed")
        try:
            observed = tuple(sorted(path.name for path in self.asset_root.iterdir()))
        except OSError as error:
            raise PromotionError(
                "attestation asset inventory is unavailable"
            ) from error
        if observed != expected:
            raise PromotionError("attestation asset inventory changed")
        for asset in candidate.assets:
            self._path(asset)

    def verify(
        self, *, candidate: Candidate, asset: ReleaseAsset
    ) -> Sequence[AttestationResult]:
        path = self._path(asset)
        before = self._require_tool_identity()
        signer_workflow = f"{candidate.repository}/{SIGNER_WORKFLOW_PATH}"
        result = self._run(
            [
                str(self.executable),
                "attestation",
                "verify",
                str(path),
                "--repo",
                candidate.repository,
                "--source-digest",
                candidate.source_sha,
                "--source-ref",
                "refs/heads/main",
                "--signer-workflow",
                signer_workflow,
                "--signer-digest",
                candidate.source_sha,
                "--predicate-type",
                PROVENANCE_PREDICATE,
                "--cert-oidc-issuer",
                "https://token.actions.githubusercontent.com",
                "--deny-self-hosted-runners",
                "--format",
                "json",
                "--limit",
                "2",
            ],
            maximum=MAX_ATTESTATION_OUTPUT_BYTES,
        )
        after = self._require_tool_identity()
        if before != after:
            raise PromotionError("GitHub CLI changed during verification")
        if result.exit_code != 0:
            raise PromotionError("GitHub artifact attestation did not verify")
        value = _closed_json(
            result.stdout, "GitHub attestation result", MAX_ATTESTATION_OUTPUT_BYTES
        )
        if not isinstance(value, list):
            raise PromotionError("GitHub attestation result must be an array")
        normalized: list[AttestationResult] = []
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {
                "attestation",
                "verificationResult",
            }:
                raise PromotionError("GitHub attestation result is not closed")
            verification = entry.get("verificationResult")
            statement = (
                verification.get("statement")
                if isinstance(verification, dict)
                else None
            )
            subjects = statement.get("subject") if isinstance(statement, dict) else None
            if (
                not isinstance(statement, dict)
                or statement.get("predicateType") != PROVENANCE_PREDICATE
                or not isinstance(subjects, list)
            ):
                raise PromotionError("GitHub attestation statement is malformed")
            matching = []
            for subject in subjects:
                if not isinstance(subject, dict):
                    continue
                digest = subject.get("digest")
                if (
                    subject.get("name") in {asset.name, f"release-assets/{asset.name}"}
                    and isinstance(digest, dict)
                    and set(digest) == {"sha256"}
                    and digest["sha256"] == asset.sha256
                ):
                    matching.append(subject)
            if len(matching) != 1:
                raise PromotionError("GitHub attestation subject is not exact")
            normalized.append(
                AttestationResult(
                    repository=candidate.repository,
                    source_sha=candidate.source_sha,
                    source_ref="refs/heads/main",
                    predicate_type=PROVENANCE_PREDICATE,
                    signer_workflow=signer_workflow,
                    signer_digest=candidate.source_sha,
                    subject_name=asset.name,
                    subject_sha256=asset.sha256,
                )
            )
        if len(normalized) != 1:
            raise PromotionError("GitHub attestation result count is not exact")
        return tuple(normalized)


class CandidateAssetAuthenticator:
    """Reauthenticate candidate bytes without GitHub CLI or credential custody."""

    def __init__(self, asset_root: Path) -> None:
        try:
            root = asset_root.resolve(strict=True)
            root_metadata = root.lstat()
        except OSError as error:
            raise PromotionError("candidate asset root is unavailable") from error
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
            root_metadata.st_mode
        ):
            raise PromotionError("candidate asset root is unsafe")
        self.asset_root = root

    def reauthenticate(self, candidate: Candidate) -> None:
        expected = expected_asset_names(candidate.version)
        if tuple(asset.name for asset in candidate.assets) != expected:
            raise PromotionError("attestation candidate inventory is not closed")
        try:
            observed = tuple(sorted(path.name for path in self.asset_root.iterdir()))
        except OSError as error:
            raise PromotionError(
                "attestation asset inventory is unavailable"
            ) from error
        if observed != expected:
            raise PromotionError("attestation asset inventory changed")
        for asset in candidate.assets:
            path = self.asset_root / asset.name
            body = _regular_bytes(path, draft.MAX_ALPHA_ASSET_BYTES)
            if (
                len(body) != asset.byte_length
                or hashlib.sha256(body).hexdigest() != asset.sha256
            ):
                raise PromotionError("attestation subject bytes changed")


def _tree_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, body in sorted(files.items()):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def _authenticate_npm_tarball(path: Path, authority: NpmArchiveAuthority) -> bytes:
    encoded = _tool_regular_bytes(
        path, max(authority.byte_length, 1), "npm client tarball"
    )
    if (
        len(encoded) != authority.byte_length
        or hashlib.sha1(encoded).hexdigest() != authority.sha1
        or hashlib.sha256(encoded).hexdigest() != authority.sha256
        or hashlib.sha512(encoded).hexdigest() != authority.sha512
        or "sha512-"
        + base64.b64encode(hashlib.sha512(encoded).digest()).decode("ascii")
        != authority.integrity
    ):
        raise PromotionError("npm client tarball identity is not exact")
    return encoded


def _safe_extract_npm(
    encoded: bytes, root: Path, authority: NpmArchiveAuthority
) -> tuple[frozenset[str], frozenset[str]]:
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(encoded), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_NPM_ARCHIVE_MEMBERS:
                raise PromotionError("npm client archive membership is not closed")
            total = 0
            for member in members:
                name = member.name
                parts = name.split("/")
                if (
                    not member.isfile()
                    or not name.startswith("package/")
                    or name.startswith("/")
                    or "\\" in name
                    or "\x00" in name
                    or len(name.encode("utf-8")) > 255
                    or any(part in {"", ".", ".."} for part in parts)
                    or name in files
                    or member.size < 0
                    or member.size > MAX_NPM_MEMBER_BYTES
                ):
                    raise PromotionError("npm client archive has an unsafe member")
                total += member.size
                if total > MAX_NPM_EXPANDED_BYTES:
                    raise PromotionError(
                        "npm client archive exceeds its expanded limit"
                    )
                stream = archive.extractfile(member)
                if stream is None:
                    raise PromotionError("npm client archive member cannot be read")
                body = stream.read(member.size + 1)
                if len(body) != member.size:
                    raise PromotionError(
                        "npm client archive member size is inconsistent"
                    )
                files[name] = body
    except PromotionError:
        raise
    except (tarfile.TarError, OSError, UnicodeError) as error:
        raise PromotionError("npm client archive is malformed") from error
    if NPM_ENTRY not in files:
        raise PromotionError("npm client archive is missing its exact entry point")
    if hashlib.sha256(files[NPM_ENTRY]).hexdigest() != authority.entry_sha256:
        raise PromotionError("npm client entry point identity is not exact")
    if _tree_digest(files) != authority.tree_sha256:
        raise PromotionError("npm client extracted tree identity is not exact")
    try:
        root.mkdir(mode=0o700)
        for name, body in sorted(files.items()):
            target = root.joinpath(*name.split("/"))
            relative_parent = target.parent.relative_to(root)
            current = root
            for part in relative_parent.parts:
                current /= part
                directories.add(current.relative_to(root).as_posix())
                if not current.exists():
                    current.mkdir(mode=0o700)
                metadata = current.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise PromotionError("npm client extraction directory is unsafe")
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            try:
                _write_all(descriptor, body)
            finally:
                os.close(descriptor)
    except PromotionError:
        raise
    except (OSError, ValueError) as error:
        raise PromotionError("npm client archive cannot be extracted safely") from error
    return frozenset(files), frozenset(directories)


class NpmCli:
    def __init__(
        self,
        *,
        npm_tarball: Path,
        node: str,
        operation: str,
        executor: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        authority: NpmArchiveAuthority = PINNED_NPM_AUTHORITY,
    ) -> None:
        self.node = self._resolve(node, "Node.js")
        self.npm_tarball = npm_tarball
        self.operation = operation
        self.executor = executor
        self.authority = authority
        encoded = _authenticate_npm_tarball(self.npm_tarball, self.authority)
        self._temporary = TemporaryDirectory(prefix="openprose-alpha-npm-client-")
        self.root = Path(self._temporary.name) / "tree"
        try:
            self._files, self._directories = _safe_extract_npm(
                encoded, self.root, self.authority
            )
            self.npm_entry = self.root / NPM_ENTRY
            self._node_sha256 = hashlib.sha256(
                _tool_regular_bytes(
                    Path(self.node), MAX_NODE_BYTES, "Node.js executable"
                )
            ).hexdigest()
        except Exception:
            self._temporary.cleanup()
            raise

    @staticmethod
    def _resolve(value: str, label: str) -> str:
        resolved = shutil.which(value) if not os.path.isabs(value) else value
        if resolved is None:
            raise PromotionError(f"{label} executable is unavailable")
        try:
            path = Path(resolved).resolve(strict=True)
            metadata = path.stat()
        except OSError as error:
            raise PromotionError(f"{label} executable is unavailable") from error
        if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
            raise PromotionError(f"{label} executable is not a regular executable")
        return str(path)

    def close(self) -> None:
        self._temporary.cleanup()

    def _run(
        self,
        argv: Sequence[str],
        *,
        environment: dict[str, str],
        capture: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.executor(
                list(argv),
                cwd=environment.pop("OPENPROSE_PROMOTION_CWD"),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                timeout=PROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PromotionError("npm process outcome is ambiguous") from error

    @staticmethod
    def _write_npm_config(cwd: str, *, bootstrap_auth: bool) -> tuple[str, str]:
        root = Path(cwd)
        user_config = root / "user.npmrc"
        global_config = root / "global.npmrc"
        user_lines = [f"registry={REGISTRY}/", "provenance=true"]
        if bootstrap_auth:
            user_lines.extend(
                [
                    "//registry.npmjs.org/:_authToken=${NODE_AUTH_TOKEN}",
                    "always-auth=true",
                ]
            )
        try:
            for path, encoded in (
                (user_config, ("\n".join(user_lines) + "\n").encode("ascii")),
                (global_config, f"registry={REGISTRY}/\n".encode("ascii")),
            ):
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    _write_all(descriptor, encoded)
                finally:
                    os.close(descriptor)
        except OSError as error:
            raise PromotionError(
                "isolated npm configuration cannot be created"
            ) from error
        return str(user_config), str(global_config)

    @classmethod
    def _base_environment(
        cls, cwd: str, *, bootstrap_auth: bool = False
    ) -> dict[str, str]:
        inherited = {
            "PATH",
            "CI",
            "GITHUB_ACTIONS",
            "GITHUB_REPOSITORY",
            "GITHUB_REF",
            "GITHUB_SHA",
            "GITHUB_WORKFLOW",
            "GITHUB_JOB",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_SERVER_URL",
            "GITHUB_API_URL",
            "GITHUB_GRAPHQL_URL",
            "ACTIONS_ID_TOKEN_REQUEST_URL",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        }
        environment = {
            name: value for name, value in os.environ.items() if name in inherited
        }
        user_config, global_config = cls._write_npm_config(
            cwd, bootstrap_auth=bootstrap_auth
        )
        environment.update(
            {
                "OPENPROSE_PROMOTION_CWD": cwd,
                "HOME": cwd,
                "TMPDIR": cwd,
                "npm_config_registry": REGISTRY,
                "npm_config_ignore_scripts": "true",
                "npm_config_audit": "false",
                "npm_config_fund": "false",
                "npm_config_cache": str(Path(cwd) / "cache"),
                "npm_config_userconfig": user_config,
                "npm_config_globalconfig": global_config,
                "NO_COLOR": "1",
            }
        )
        return environment

    def _version(self, argv: Sequence[str], label: str) -> tuple[int, ...]:
        with TemporaryDirectory(prefix="openprose-alpha-tool-") as directory:
            result = self._run(
                [*argv, "--version"],
                environment=self._base_environment(directory),
                capture=True,
            )
        if result.returncode != 0 or len(result.stdout) > 128 or result.stderr:
            raise PromotionError(f"{label} version probe failed")
        try:
            value = result.stdout.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise PromotionError(f"{label} version output is malformed") from error
        return _parse_numeric_version(value, label)

    def _tree_bytes(self) -> dict[str, bytes]:
        observed_files: dict[str, bytes] = {}
        observed_directories: set[str] = set()
        try:
            for current, directory_names, file_names in os.walk(
                self.root, topdown=True, followlinks=False
            ):
                current_path = Path(current)
                for directory_name in directory_names:
                    path = current_path / directory_name
                    metadata = path.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(
                        metadata.st_mode
                    ):
                        raise PromotionError("npm client tree is not closed")
                    observed_directories.add(path.relative_to(self.root).as_posix())
                for file_name in file_names:
                    path = current_path / file_name
                    name = path.relative_to(self.root).as_posix()
                    if name in observed_files:
                        raise PromotionError("npm client tree contains a duplicate")
                    observed_files[name] = _tool_regular_bytes(
                        path, MAX_NPM_MEMBER_BYTES, "npm client tree member"
                    )
        except PromotionError:
            raise
        except OSError as error:
            raise PromotionError("npm client tree cannot be authenticated") from error
        if (
            frozenset(observed_files) != self._files
            or frozenset(observed_directories) != self._directories
        ):
            raise PromotionError("npm client tree is not closed")
        return observed_files

    def _material_identity(self) -> dict[str, Any]:
        _authenticate_npm_tarball(self.npm_tarball, self.authority)
        files = self._tree_bytes()
        tree_sha256 = _tree_digest(files)
        entry_sha256 = hashlib.sha256(files[NPM_ENTRY]).hexdigest()
        node_sha256 = hashlib.sha256(
            _tool_regular_bytes(Path(self.node), MAX_NODE_BYTES, "Node.js executable")
        ).hexdigest()
        if (
            tree_sha256 != self.authority.tree_sha256
            or entry_sha256 != self.authority.entry_sha256
            or node_sha256 != self._node_sha256
        ):
            raise PromotionError("npm mutation toolchain identity changed")
        return {
            "npmVersion": NPM_VERSION,
            "npmTarballUrl": self.authority.url,
            "npmTarballByteLength": self.authority.byte_length,
            "npmTarballSha1": self.authority.sha1,
            "npmTarballSha256": self.authority.sha256,
            "npmTarballSha512": self.authority.sha512,
            "npmTarballIntegrity": self.authority.integrity,
            "npmTreeSha256": tree_sha256,
            "npmEntrySha256": entry_sha256,
            "nodeVersion": NODE_VERSION,
            "nodeExecutableSha256": node_sha256,
        }

    def _authenticate_toolchain(self) -> dict[str, Any]:
        before = self._material_identity()
        if self._version([self.node], "Node.js") != tuple(
            int(part) for part in NODE_VERSION.split(".")
        ):
            raise PromotionError(f"exact Node.js {NODE_VERSION} is required")
        if self._version([self.node, str(self.npm_entry)], "npm") != tuple(
            int(part) for part in NPM_VERSION.split(".")
        ):
            raise PromotionError(f"exact npm {NPM_VERSION} is required")
        after = self._material_identity()
        if before != after:
            raise PromotionError("npm mutation toolchain changed during authentication")
        return after

    def require_toolchain(self) -> dict[str, Any]:
        identity = self._authenticate_toolchain()
        token_names = ("NPM_TOKEN", "NODE_AUTH_TOKEN", "NPM_CONFIG_TOKEN")
        if not os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL") or not os.environ.get(
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
        ):
            raise PromotionError("GitHub Actions OIDC identity is unavailable")
        if self.operation == "bootstrap":
            token = os.environ.get("NPM_TOKEN", "")
            if not token or "\r" in token or "\n" in token:
                raise PromotionError("the protected one-time npm token is unavailable")
        elif any(os.environ.get(name) for name in token_names):
            raise PromotionError(
                "token credentials must be absent from an OIDC-only transition"
            )
        return identity

    def mutate(self, operation: str, package: PackageArtifact) -> int:
        if operation not in {"publish", "stage"}:
            raise PromotionError("npm mutation is outside the closed operation set")
        expected_operation = "publish" if self.operation == "bootstrap" else "stage"
        if operation != expected_operation:
            raise PromotionError("npm mutation differs from the selected transition")
        self._authenticate_toolchain()
        with TemporaryDirectory(prefix="openprose-alpha-npm-") as directory:
            environment = self._base_environment(
                directory, bootstrap_auth=operation == "publish"
            )
            if operation == "publish":
                environment["NODE_AUTH_TOKEN"] = os.environ["NPM_TOKEN"]
                argv = [
                    self.node,
                    str(self.npm_entry),
                    "publish",
                    str(package.path),
                    "--ignore-scripts",
                    "--access",
                    "public",
                    "--tag",
                    ALPHA_TAG,
                    "--provenance",
                    "--registry",
                    REGISTRY,
                ]
            else:
                argv = [
                    self.node,
                    str(self.npm_entry),
                    "stage",
                    "publish",
                    str(package.path),
                    "--ignore-scripts",
                    "--access",
                    "public",
                    "--tag",
                    ALPHA_TAG,
                    "--registry",
                    REGISTRY,
                ]
            try:
                result = self._run(argv, environment=environment)
            finally:
                self._authenticate_toolchain()
        return result.returncode


def _package_map(candidate: Candidate) -> dict[str, PackageArtifact]:
    if tuple(package.name for package in candidate.packages) != PACKAGE_ORDER:
        raise PromotionError("candidate npm package inventory is not closed or ordered")
    return {package.name: package for package in candidate.packages}


def _require_owners(state: RegistryPackage, lineage: Lineage) -> None:
    if set(state.owners) != set(lineage.owners) or len(state.owners) != len(
        lineage.owners
    ):
        raise PromotionError("npm registry package ownership differs from authority")


def _require_latest(registry: RegistryBoundary, lineage: Lineage) -> RegistryPackage:
    state = registry.package(META_PACKAGE)
    if state is None:
        raise PromotionError("the stable npm meta package is missing")
    _require_owners(state, lineage)
    if state.tags.get("latest") != lineage.latest:
        raise PromotionError("the stable npm latest tag drifted from authority")
    return state


def _verify_public_package(
    *,
    registry: RegistryBoundary,
    package: PackageArtifact,
    lineage: Lineage,
    require_alpha: bool = True,
) -> str:
    state = registry.package(package.name)
    if state is None:
        raise PromotionError("an expected npm package is not public")
    _require_owners(state, lineage)
    version = state.versions.get(package_version := _manifest_version(package))
    if version is None:
        raise PromotionError("the expected npm package version is not public")
    if (
        version.name != package.name
        or version.version != package_version
        or version.shasum != package.shasum
        or version.integrity != package.integrity
        or version.provenance_predicate_type != PROVENANCE_PREDICATE
    ):
        raise PromotionError(
            "npm registry integrity or provenance differs from admitted publication"
        )
    if require_alpha and state.tags.get(ALPHA_TAG) != package_version:
        raise PromotionError("the npm alpha tag does not select the exact cohort")
    body = registry.tarball(version.tarball_url)
    if (
        body != package.body
        or hashlib.sha1(body).hexdigest() != package.shasum  # noqa: S324 - npm identity
        or "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode("ascii")
        != package.integrity
    ):
        raise PromotionError(
            "downloaded npm package bytes differ from the admitted tarball"
        )
    if package.role == "meta":
        expected = {name: package_version for name in PLATFORM_PACKAGES}
        if version.optional_dependencies != expected:
            raise PromotionError(
                "published meta dependencies differ from the exact cohort"
            )
    return version.integrity


def _manifest_version(package: PackageArtifact) -> str:
    manifest = _npm_package_json(package.body, package.artifact)
    version = manifest.get("version")
    if not isinstance(version, str) or ALPHA.fullmatch(version) is None:
        raise PromotionError("candidate package version is malformed")
    return version


def _require_unused(state: RegistryPackage | None, version: str) -> None:
    if state is not None and version in state.versions:
        raise PromotionError(
            "the exact npm version already exists; version reuse is forbidden"
        )


def _settlement_entry(settlement: dict[str, Any], name: str) -> dict[str, Any]:
    for package in settlement["packages"]:
        if package["name"] == name:
            return package
    raise PromotionError("settlement package inventory is incomplete")


def _mark_verified(
    settlement: dict[str, Any], writer: SettlementWriter, name: str, integrity: str
) -> None:
    item = _settlement_entry(settlement, name)
    item.update(
        {
            "action": "verify",
            "outcome": "verified",
            "registryIntegrity": integrity,
            "provenancePredicateType": PROVENANCE_PREDICATE,
        }
    )
    writer.write(settlement)


def _mark_published(
    settlement: dict[str, Any], writer: SettlementWriter, name: str, integrity: str
) -> None:
    item = _settlement_entry(settlement, name)
    item.update(
        {
            "outcome": "published",
            "registryIntegrity": integrity,
            "provenancePredicateType": PROVENANCE_PREDICATE,
        }
    )
    writer.write(settlement)


def _attempt(
    *,
    settlement: dict[str, Any],
    writer: SettlementWriter,
    npm: NpmBoundary,
    attestations: CandidateAssetBoundary,
    candidate: Candidate,
    operation: str,
    package: PackageArtifact,
) -> int:
    _reauthenticate_attested_bytes(
        settlement=settlement,
        writer=writer,
        attestations=attestations,
        candidate=candidate,
    )
    item = _settlement_entry(settlement, package.name)
    item.update({"attempted": True, "action": operation, "outcome": "ambiguous"})
    settlement["status"] = "in-progress"
    writer.write(settlement)
    try:
        return npm.mutate(operation, package)
    except PromotionError:
        settlement["status"] = (
            "partial-publication" if operation == "publish" else "ambiguous"
        )
        writer.write(settlement)
        raise


def _bind_npm_toolchain(
    settlement: dict[str, Any], writer: SettlementWriter, npm: NpmBoundary
) -> None:
    identity = npm.require_toolchain()
    settlement["npmMutationToolchain"] = identity
    writer.write(settlement)


def _expected_attestation(
    candidate: Candidate, asset: ReleaseAsset
) -> AttestationResult:
    return AttestationResult(
        repository=candidate.repository,
        source_sha=candidate.source_sha,
        source_ref="refs/heads/main",
        predicate_type=PROVENANCE_PREDICATE,
        signer_workflow=f"{candidate.repository}/{SIGNER_WORKFLOW_PATH}",
        signer_digest=candidate.source_sha,
        subject_name=asset.name,
        subject_sha256=asset.sha256,
    )


def preverify_attestations(
    *,
    candidate: Candidate,
    draft_authority: DraftAuthority,
    workflow_run_id: int,
    workflow_run_attempt: int,
    attestations: AttestationBoundary,
    writer: AttestationEvidenceWriter,
) -> dict[str, Any]:
    """Verify every attestation and emit one final immutable handoff record."""
    if (
        not isinstance(workflow_run_id, int)
        or isinstance(workflow_run_id, bool)
        or workflow_run_id <= 0
        or not isinstance(workflow_run_attempt, int)
        or isinstance(workflow_run_attempt, bool)
        or workflow_run_attempt <= 0
    ):
        raise PromotionError("workflow run identity is malformed")
    if tuple(asset.name for asset in candidate.assets) != expected_asset_names(
        candidate.version
    ):
        raise PromotionError("attestation candidate inventory is not closed")
    require_draft_authority_candidate(draft_authority, candidate)
    writer.refuse_existing()
    tool = _validate_attestation_tool(attestations.require_toolchain())
    for asset in candidate.assets:
        results = attestations.verify(candidate=candidate, asset=asset)
        if tuple(results) != (_expected_attestation(candidate, asset),):
            raise PromotionError("GitHub artifact attestation identity is not exact")
    attestations.reauthenticate(candidate)
    evidence = {
        "schema": ATTESTATION_EVIDENCE_SCHEMA,
        "status": "verified",
        "repository": candidate.repository,
        "releaseId": candidate.release_id,
        "version": candidate.version,
        "sourceSha": candidate.source_sha,
        "tag": candidate.tag,
        "assemblySha256": candidate.assembly_sha256,
        "draftAuthority": _draft_authority_identity(draft_authority),
        "sourceRef": "refs/heads/main",
        "predicateType": PROVENANCE_PREDICATE,
        "signerWorkflow": f"{candidate.repository}/{SIGNER_WORKFLOW_PATH}",
        "signerDigest": candidate.source_sha,
        "workflowRunId": workflow_run_id,
        "workflowRunAttempt": workflow_run_attempt,
        "expectedAssetCount": len(candidate.assets),
        "verifiedAssetCount": len(candidate.assets),
        "bytesReauthenticated": True,
        "tool": tool,
        "assets": [
            {
                "name": asset.name,
                "byteLength": asset.byte_length,
                "sha256": asset.sha256,
                "outcome": "verified",
            }
            for asset in candidate.assets
        ],
    }
    validate_attestation_evidence(
        evidence,
        candidate=candidate,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
        draft_authority=draft_authority,
    )
    writer.write(evidence)
    return evidence


def _bind_attestation_evidence(
    *,
    settlement: dict[str, Any],
    candidate: Candidate,
    evidence: dict[str, Any],
    evidence_sha256: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
) -> None:
    validate_attestation_evidence(
        evidence,
        candidate=candidate,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
    )
    if (
        SHA256.fullmatch(evidence_sha256) is None
        or attestation_evidence_digest(evidence) != evidence_sha256
    ):
        raise PromotionError("attestation evidence digest does not match")
    state = settlement["githubArtifactAttestations"]
    state.update(
        {
            "status": "verified",
            "verifiedAssetCount": len(candidate.assets),
            "bytesReauthenticated": True,
            "workflowRunId": workflow_run_id,
            "workflowRunAttempt": workflow_run_attempt,
            "evidenceSha256": evidence_sha256,
            "tool": dict(evidence["tool"]),
        }
    )
    for item in state["assets"]:
        item["outcome"] = "verified"


def _reauthenticate_attested_bytes(
    *,
    settlement: dict[str, Any],
    writer: SettlementWriter,
    attestations: CandidateAssetBoundary,
    candidate: Candidate,
) -> None:
    state = settlement["githubArtifactAttestations"]
    if state["status"] != "verified":
        raise PromotionError("GitHub artifact attestations are not settled")
    try:
        attestations.reauthenticate(candidate)
    except PromotionError:
        state["status"] = "failed"
        state["bytesReauthenticated"] = False
        settlement["status"] = (
            "partial-publication"
            if any(
                package["attempted"] and package["action"] == "publish"
                for package in settlement["packages"]
            )
            else "failed"
        )
        writer.write(settlement)
        raise


def _fail(
    settlement: dict[str, Any],
    writer: SettlementWriter,
    status: str,
    error: PromotionError,
) -> None:
    settlement["status"] = status
    writer.write(settlement)
    raise error


def execute_transition(
    *,
    operation: str,
    candidate: Candidate,
    lineage: Lineage,
    registry: RegistryBoundary,
    npm: NpmBoundary | None,
    github: GitHubBoundary,
    attestations: CandidateAssetBoundary,
    attestation_evidence: dict[str, Any],
    attestation_evidence_sha256: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    writer: SettlementWriter,
) -> dict[str, Any]:
    """Execute one closed transition and persist evidence at each mutation edge."""
    if operation not in OPERATIONS:
        raise PromotionError("promotion operation is unsupported")
    packages = _package_map(candidate)
    writer.refuse_attempted_retry()
    validate_attestation_evidence(
        attestation_evidence,
        candidate=candidate,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
    )
    settlement = new_settlement(
        operation=operation,
        candidate=candidate,
        lineage=lineage,
        draft_authority=attestation_evidence["draftAuthority"],
    )
    _bind_attestation_evidence(
        settlement=settlement,
        candidate=candidate,
        evidence=attestation_evidence,
        evidence_sha256=attestation_evidence_sha256,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
    )
    settlement["status"] = "in-progress"
    writer.write(settlement)
    try:
        meta_state = _require_latest(registry, lineage)
        if operation == "bootstrap":
            _require_unused(meta_state, candidate.version)
            for name in PLATFORM_PACKAGES:
                state = registry.package(name)
                if state is not None:
                    raise PromotionError(
                        "bootstrap requires every platform package name "
                        "to be unpublished"
                    )
            if npm is None:
                raise PromotionError("npm mutation boundary is unavailable")
            _bind_npm_toolchain(settlement, writer, npm)
            for name in PLATFORM_PACKAGES:
                package = packages[name]
                _attempt(
                    settlement=settlement,
                    writer=writer,
                    npm=npm,
                    attestations=attestations,
                    candidate=candidate,
                    operation="publish",
                    package=package,
                )
                try:
                    integrity = _verify_public_package(
                        registry=registry, package=package, lineage=lineage
                    )
                except PromotionError as error:
                    item = _settlement_entry(settlement, name)
                    item["outcome"] = "ambiguous"
                    _fail(settlement, writer, "partial-publication", error)
                _mark_published(settlement, writer, name, integrity)
                _require_latest(registry, lineage)
            for name in PLATFORM_PACKAGES:
                integrity = _verify_public_package(
                    registry=registry, package=packages[name], lineage=lineage
                )
                _mark_published(settlement, writer, name, integrity)
            meta = packages[META_PACKAGE]
            _attempt(
                settlement=settlement,
                writer=writer,
                npm=npm,
                attestations=attestations,
                candidate=candidate,
                operation="publish",
                package=meta,
            )
            try:
                integrity = _verify_public_package(
                    registry=registry, package=meta, lineage=lineage
                )
            except PromotionError as error:
                item = _settlement_entry(settlement, META_PACKAGE)
                item["outcome"] = "ambiguous"
                _fail(settlement, writer, "partial-publication", error)
            _mark_published(settlement, writer, META_PACKAGE, integrity)
            _require_latest(registry, lineage)
            settlement["status"] = "npm-settled"
            writer.write(settlement)
            return settlement

        if operation == "stage-platforms":
            _require_unused(meta_state, candidate.version)
            for name in PLATFORM_PACKAGES:
                state = registry.package(name)
                if state is None:
                    raise PromotionError(
                        "staged publication requires every platform package "
                        "to exist already"
                    )
                _require_owners(state, lineage)
                _require_unused(state, candidate.version)
            if npm is None:
                raise PromotionError("npm mutation boundary is unavailable")
            _bind_npm_toolchain(settlement, writer, npm)
            for name in PLATFORM_PACKAGES:
                package = packages[name]
                returncode = _attempt(
                    settlement=settlement,
                    writer=writer,
                    npm=npm,
                    attestations=attestations,
                    candidate=candidate,
                    operation="stage",
                    package=package,
                )
                item = _settlement_entry(settlement, name)
                if returncode != 0:
                    item["outcome"] = "ambiguous"
                    _fail(
                        settlement,
                        writer,
                        "ambiguous",
                        PromotionError(
                            "npm did not confirm staging; do not retry or "
                            "auto-approve this version"
                        ),
                    )
                item["outcome"] = "staged"
                writer.write(settlement)
            settlement["status"] = "awaiting-platform-approval"
            writer.write(settlement)
            return settlement

        if operation == "stage-meta":
            _require_unused(meta_state, candidate.version)
            for name in PLATFORM_PACKAGES:
                integrity = _verify_public_package(
                    registry=registry, package=packages[name], lineage=lineage
                )
                _mark_verified(settlement, writer, name, integrity)
            if npm is None:
                raise PromotionError("npm mutation boundary is unavailable")
            _bind_npm_toolchain(settlement, writer, npm)
            package = packages[META_PACKAGE]
            returncode = _attempt(
                settlement=settlement,
                writer=writer,
                npm=npm,
                attestations=attestations,
                candidate=candidate,
                operation="stage",
                package=package,
            )
            item = _settlement_entry(settlement, META_PACKAGE)
            if returncode != 0:
                item["outcome"] = "ambiguous"
                _fail(
                    settlement,
                    writer,
                    "ambiguous",
                    PromotionError(
                        "npm did not confirm meta staging; do not retry or "
                        "auto-approve this version"
                    ),
                )
            item["outcome"] = "staged"
            settlement["status"] = "awaiting-meta-approval"
            writer.write(settlement)
            return settlement

        for name in PACKAGE_ORDER:
            integrity = _verify_public_package(
                registry=registry, package=packages[name], lineage=lineage
            )
            _mark_verified(settlement, writer, name, integrity)
        _require_latest(registry, lineage)
        _reauthenticate_attested_bytes(
            settlement=settlement,
            writer=writer,
            attestations=attestations,
            candidate=candidate,
        )
        settlement["github"]["promotionAttempted"] = True
        settlement["github"]["outcome"] = "ambiguous"
        settlement["status"] = "in-progress"
        writer.write(settlement)
        try:
            github.promote(candidate)
        except PromotionError as error:
            _fail(settlement, writer, "ambiguous", error)
        settlement["github"]["promoted"] = True
        settlement["github"]["outcome"] = "promoted"
        settlement["status"] = "complete"
        writer.write(settlement)
        return settlement
    except PromotionError as error:
        if settlement["status"] not in {"ambiguous", "partial-publication"}:
            settlement["status"] = (
                "partial-publication"
                if any(
                    package["attempted"] and package["action"] == "publish"
                    for package in settlement["packages"]
                )
                else "failed"
            )
            writer.write(settlement)
        raise error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=MODES, required=True)
    result.add_argument("--operation")
    result.add_argument("--repository", required=True)
    result.add_argument("--release-id", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--source-sha", required=True)
    result.add_argument("--workflow-run-id", required=True)
    result.add_argument("--workflow-run-attempt", required=True)
    result.add_argument("--draft-authority", type=Path, required=True)
    result.add_argument("--draft-authority-sha256", required=True)
    result.add_argument("--draft-authority-run-id", required=True)
    result.add_argument("--draft-authority-run-attempt", required=True)
    result.add_argument("--attestation-evidence", type=Path, required=True)
    result.add_argument("--attestation-evidence-sha256")
    result.add_argument("--confirmation")
    result.add_argument("--settlement", type=Path)
    result.add_argument(
        "--lineage",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "release"
        / "npm-registry-lineage.v1.json",
    )
    result.add_argument("--npm-tarball", type=Path)
    result.add_argument("--node")
    result.add_argument("--github-cli")
    return result


def _validate_inputs(args: argparse.Namespace) -> None:
    if ALPHA.fullmatch(args.version) is None:
        raise PromotionError("version must be exact numbered alpha SemVer")
    if FULL_SHA.fullmatch(args.source_sha) is None:
        raise PromotionError("source SHA must be full lowercase hexadecimal")
    if REPOSITORY.fullmatch(args.repository) is None:
        raise PromotionError("GitHub repository must be owner/name")
    if (
        not isinstance(args.release_id, str)
        or re.fullmatch(r"[1-9][0-9]*", args.release_id) is None
    ):
        raise PromotionError("GitHub release ID must be a positive integer")
    release_id = int(args.release_id)
    if (
        not isinstance(args.workflow_run_id, str)
        or re.fullmatch(r"[1-9][0-9]*", args.workflow_run_id) is None
        or not isinstance(args.workflow_run_attempt, str)
        or re.fullmatch(r"[1-9][0-9]*", args.workflow_run_attempt) is None
    ):
        raise PromotionError("workflow run identity must be positive integers")
    workflow_run_id = int(args.workflow_run_id)
    workflow_run_attempt = int(args.workflow_run_attempt)
    if (
        not isinstance(args.draft_authority_run_id, str)
        or re.fullmatch(r"[1-9][0-9]*", args.draft_authority_run_id) is None
        or not isinstance(args.draft_authority_run_attempt, str)
        or re.fullmatch(r"[1-9][0-9]*", args.draft_authority_run_attempt) is None
    ):
        raise PromotionError("draft authority producer run must be positive integers")
    draft_authority_run_id = int(args.draft_authority_run_id)
    draft_authority_run_attempt = int(args.draft_authority_run_attempt)
    if args.draft_authority.name != DRAFT_AUTHORITY_FILENAME:
        raise PromotionError("draft authority must use the canonical filename")
    if SHA256.fullmatch(args.draft_authority_sha256) is None:
        raise PromotionError("draft authority digest is malformed")
    if args.attestation_evidence.name != ATTESTATION_EVIDENCE_FILENAME:
        raise PromotionError("attestation evidence must use the canonical filename")
    if args.mode == "verify-attestations":
        if (
            args.operation is not None
            or args.confirmation is not None
            or args.settlement is not None
            or args.npm_tarball is not None
            or args.node is not None
            or args.attestation_evidence_sha256 is not None
        ):
            raise PromotionError(
                "read-only attestation mode does not accept mutation custody"
            )
        if not isinstance(args.github_cli, str) or not os.path.isabs(args.github_cli):
            raise PromotionError(
                "read-only attestation mode requires an absolute GitHub CLI path"
            )
        args.release_id = release_id
        args.workflow_run_id = workflow_run_id
        args.workflow_run_attempt = workflow_run_attempt
        args.draft_authority_run_id = draft_authority_run_id
        args.draft_authority_run_attempt = draft_authority_run_attempt
        return
    if args.operation not in OPERATIONS:
        raise PromotionError("promotion operation is unsupported")
    expected = (
        f"PROMOTE {args.operation} {args.version} {args.source_sha} {release_id} "
        f"AUTHORITY {draft_authority_run_id}/{draft_authority_run_attempt} "
        f"{args.draft_authority_sha256}"
    )
    if args.confirmation != expected:
        raise PromotionError("confirmation does not exactly identify this transition")
    if (
        args.settlement is None
        or args.settlement.name != "npm-publication-settlement.json"
    ):
        raise PromotionError("settlement must use the canonical filename")
    if (
        not isinstance(args.attestation_evidence_sha256, str)
        or SHA256.fullmatch(args.attestation_evidence_sha256) is None
    ):
        raise PromotionError("attestation evidence digest is malformed")
    if args.github_cli is not None:
        raise PromotionError(
            "evidence-consuming mutation mode does not accept GitHub CLI custody"
        )
    if args.operation == "settle-and-promote":
        if args.npm_tarball is not None or args.node is not None:
            raise PromotionError(
                "settlement-only promotion does not accept npm custody"
            )
    elif args.npm_tarball is None:
        raise PromotionError("an exact npm client tarball is required")
    args.release_id = release_id
    args.workflow_run_id = workflow_run_id
    args.workflow_run_attempt = workflow_run_attempt
    args.draft_authority_run_id = draft_authority_run_id
    args.draft_authority_run_attempt = draft_authority_run_attempt


def run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_inputs(args)
    authority = load_draft_authority(
        args.draft_authority,
        expected_sha256=args.draft_authority_sha256,
        repository=args.repository,
        release_id=args.release_id,
        version=args.version,
        source_sha=args.source_sha,
        producer_run_id=args.draft_authority_run_id,
        producer_run_attempt=args.draft_authority_run_attempt,
    )
    evidence_writer: AttestationEvidenceWriter | None = None
    if args.mode == "verify-attestations":
        evidence_writer = AttestationEvidenceWriter(args.attestation_evidence)
        evidence_writer.refuse_existing()
    token = os.environ.get("GITHUB_TOKEN", "")
    github = GitHubRelease(
        repository=args.repository,
        token=token,
        http=StrictHttp(),
        allow_promotion=(
            args.mode == "execute-transition" and args.operation == "settle-and-promote"
        ),
        draft_authority=authority,
    )
    with TemporaryDirectory(prefix="openprose-alpha-promotion-") as directory:
        asset_root = Path(directory) / "assets"
        candidate = github.download_candidate(
            release_id=args.release_id,
            version=args.version,
            source_sha=args.source_sha,
            root=asset_root,
        )
        if args.mode == "verify-attestations":
            assert evidence_writer is not None
            attestations = GitHubAttestationVerifier(
                asset_root=asset_root,
                executable=args.github_cli,
                token=token,
            )
            try:
                return preverify_attestations(
                    candidate=candidate,
                    draft_authority=authority,
                    workflow_run_id=args.workflow_run_id,
                    workflow_run_attempt=args.workflow_run_attempt,
                    attestations=attestations,
                    writer=evidence_writer,
                )
            finally:
                attestations.close()
        evidence = load_attestation_evidence(
            args.attestation_evidence,
            expected_sha256=args.attestation_evidence_sha256,
            candidate=candidate,
            workflow_run_id=args.workflow_run_id,
            workflow_run_attempt=args.workflow_run_attempt,
            draft_authority=authority,
        )
        lineage = load_lineage(args.lineage, args.version)
        attestations = CandidateAssetAuthenticator(asset_root)
        npm: NpmBoundary | None = None
        if args.operation != "settle-and-promote":
            npm = NpmCli(
                npm_tarball=args.npm_tarball,
                node=args.node or "node",
                operation=args.operation,
            )
        try:
            result = execute_transition(
                operation=args.operation,
                candidate=candidate,
                lineage=lineage,
                registry=PublicNpmRegistry(github.http),
                npm=npm,
                github=github,
                attestations=attestations,
                attestation_evidence=evidence,
                attestation_evidence_sha256=args.attestation_evidence_sha256,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
                writer=SettlementWriter(args.settlement),
            )
        finally:
            if isinstance(npm, NpmCli):
                npm.close()
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except PromotionError as error:
        print(f"alpha promotion refused: {error}", file=sys.stderr)
        return 1
    except (KeyError, OSError, TypeError, ValueError):
        print("alpha promotion refused: an internal boundary failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
