#!/usr/bin/env python3
"""Verify one published functional alpha without mutating public state.

This module is the controller core for the post-public verification stage.  It
authenticates the exact GitHub prerelease and npm cohort, verifies GitHub
artifact attestations, and consumes one injected provider-free package journey
result for each shipped surface.  It never interprets OpenProse programs and it
has no public-state mutation operation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
from typing import Any, Callable, Protocol, Sequence

from jsonschema import Draft202012Validator, FormatChecker

import create_draft_release as draft
import promote_alpha_release as promotion


SCHEMA = "openprose.alpha-public-verification/1"
REGISTRY = promotion.REGISTRY
ALPHA_TAG = promotion.ALPHA_TAG
PROVENANCE_PREDICATE = promotion.PROVENANCE_PREDICATE
WORKFLOW_PATH = ".github/workflows/openprose-cli-alpha-post-public.yml"
SIGNER_WORKFLOW_PATH = ".github/workflows/openprose-cli-alpha-release.yml"
JOURNEY_SCHEMA = "openprose.alpha-package-admission/1"
JOURNEY_STATUS = "passed-provider-free-functional-alpha"
PROVIDER_CALLS = "none-provider-free-fixture"
SURFACES = ("direct-rust", "direct-bun", "npm-launcher")
TARGET_PLATFORMS = {
    "linux-x64": "linux-x64-gnu",
    "linux-arm64": "linux-arm64-gnu",
    "darwin-arm": "darwin-arm64",
    "darwin-x64": "darwin-x64",
}
MAX_HTTP_BYTES = 16 * 1024 * 1024
MAX_ATTESTATION_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
ATTESTATION_TIMEOUT_SECONDS = 90
ALPHA = promotion.ALPHA
FULL_SHA = promotion.FULL_SHA
SHA256 = promotion.SHA256
REPOSITORY = promotion.REPOSITORY
SAFE_SUBJECT = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@/._:+-]{0,511}$")
WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.ya?ml$")
ATTEMPT_BOUNDARIES = {
    "draft-authority",
    "github-release",
    "github-tag",
    "github-inventory",
    "github-asset-download",
    "github-sha256s",
    "github-attestation",
    "npm-latest",
    "npm-package",
    "package-journeys",
}
_EVIDENCE_VALIDATOR: Draft202012Validator | None = None


class VerificationError(RuntimeError):
    """A credential-free, path-free public verification failure."""


@dataclass(frozen=True)
class VerificationInputs:
    repository: str
    release_id: int
    version: str
    source_sha: str
    target_id: str
    workflow_path: str
    workflow_run_id: int
    workflow_run_attempt: int
    confirmation: str


@dataclass(frozen=True)
class DraftAuthorityInput:
    path: Path
    sha256: str
    workflow_run_id: int
    workflow_run_attempt: int


@dataclass(frozen=True)
class ReleaseIdentity:
    release_id: int
    tag: str
    name: str
    draft: bool
    prerelease: bool
    immutable: bool
    body_byte_length: int
    body_sha256: str


@dataclass(frozen=True)
class RemoteAsset:
    name: str
    asset_id: int
    api_url: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class AttestationResult:
    repository: str
    source_sha: str
    predicate_type: str
    subject_name: str
    subject_sha256: str


@dataclass(frozen=True)
class JourneyArtifact:
    name: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class JourneySpec:
    surface: str
    target_id: str
    version: str
    source_sha: str
    artifacts: tuple[JourneyArtifact, ...]


@dataclass(frozen=True)
class JourneyRequest:
    journeys: tuple[JourneySpec, ...]


@dataclass(frozen=True)
class JourneyResult:
    surface: str
    target_id: str
    version: str
    source_sha: str
    schema: str
    status: str
    provider_calls: str
    semantic_evaluation: bool
    artifact_sha256: tuple[str, ...]


class GitHubReadBoundary(Protocol):
    def release(self, release_id: int, version: str) -> ReleaseIdentity:
        ...

    def tag_source(self, version: str) -> str:
        ...

    def assets(self, release_id: int, version: str) -> tuple[RemoteAsset, ...]:
        ...

    def download(self, asset: RemoteAsset) -> bytes:
        ...


class AttestationBoundary(Protocol):
    def verify(
        self,
        *,
        path: Path,
        name: str,
        sha256: str,
        repository: str,
        source_sha: str,
    ) -> Sequence[AttestationResult]:
        ...


class JourneyBoundary(Protocol):
    def run(self, request: JourneyRequest) -> Sequence[JourneyResult]:
        ...


class RegistryBoundary(promotion.RegistryBoundary, Protocol):
    pass


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def expected_confirmation(inputs: VerificationInputs) -> str:
    return (
        f"VERIFY PUBLIC ALPHA {inputs.repository} {inputs.version} "
        f"{inputs.source_sha} {inputs.release_id} "
        f"{inputs.target_id} {inputs.workflow_run_id}/{inputs.workflow_run_attempt}"
    )


def validate_inputs(inputs: VerificationInputs) -> None:
    if REPOSITORY.fullmatch(inputs.repository) is None:
        raise VerificationError("repository must be exact owner/name")
    if ALPHA.fullmatch(inputs.version) is None:
        raise VerificationError("version must be exact numbered alpha SemVer")
    if FULL_SHA.fullmatch(inputs.source_sha) is None:
        raise VerificationError("source SHA must be full lowercase hexadecimal")
    for value, label in (
        (inputs.release_id, "release ID"),
        (inputs.workflow_run_id, "workflow run ID"),
        (inputs.workflow_run_attempt, "workflow run attempt"),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise VerificationError(f"{label} must be a positive integer")
    if inputs.target_id not in TARGET_PLATFORMS:
        raise VerificationError("target ID is outside the released platform set")
    if (
        inputs.workflow_path != WORKFLOW_PATH
        or WORKFLOW.fullmatch(inputs.workflow_path) is None
    ):
        raise VerificationError("workflow path is not the post-public verifier")
    if inputs.confirmation != expected_confirmation(inputs):
        raise VerificationError("confirmation does not exactly identify this check")


def validate_draft_authority_input(value: DraftAuthorityInput) -> None:
    if (
        not isinstance(value, DraftAuthorityInput)
        or not isinstance(value.path, Path)
        or not value.path.is_absolute()
        or value.path != Path(os.path.abspath(value.path))
        or value.path.name != promotion.DRAFT_AUTHORITY_FILENAME
        or not isinstance(value.sha256, str)
        or SHA256.fullmatch(value.sha256) is None
    ):
        raise VerificationError("draft authority input identity is malformed")
    for number in (value.workflow_run_id, value.workflow_run_attempt):
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise VerificationError("draft authority workflow identity is malformed")


def _closed_json(encoded: bytes, label: str, maximum: int) -> Any:
    if not encoded or len(encoded) > maximum:
        raise VerificationError(f"{label} is empty or exceeds its byte limit")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerificationError(f"{label} contains a duplicate field")
            result[key] = value
        return result

    try:
        return json.loads(encoded, object_pairs_hook=reject_duplicates)
    except VerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} is not valid JSON") from error


def _write_all(descriptor: int, encoded: bytes) -> None:
    offset = 0
    while offset < len(encoded):
        written = os.write(descriptor, encoded[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _safe_regular_bytes(path: Path, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise VerificationError("local verification input is unavailable") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > maximum
    ):
        raise VerificationError("local verification input is not a bounded file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VerificationError("local verification input cannot be opened") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise VerificationError("local verification input changed before open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise VerificationError("local verification input exceeds its limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or total != opened.st_size:
        raise VerificationError("local verification input changed while read")
    return b"".join(chunks)


class EvidenceWriter:
    """Atomically retain one non-reusable verification attempt."""

    def __init__(self, path: Path) -> None:
        if path.name != "alpha-public-verification.json":
            raise VerificationError("evidence must use the canonical filename")
        self.path = path
        self._last_digest: str | None = None
        self._parent_identity: tuple[int, int] | None = None

    def _parent(self) -> Path:
        parent = self.path.parent
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = parent.lstat()
        except OSError as error:
            raise VerificationError("evidence directory is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise VerificationError("evidence directory is unsafe")
        identity = (metadata.st_dev, metadata.st_ino)
        if self._parent_identity is not None and identity != self._parent_identity:
            raise VerificationError("evidence directory changed during verification")
        self._parent_identity = identity
        try:
            os.chmod(parent, 0o700)
        except OSError as error:
            raise VerificationError("evidence directory cannot be hardened") from error
        return parent

    @staticmethod
    def _encoded(value: dict[str, Any]) -> bytes:
        validate_evidence(value)
        encoded = (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_EVIDENCE_BYTES:
            raise VerificationError("verification evidence exceeds its byte limit")
        return encoded

    def start(self, value: dict[str, Any]) -> None:
        parent = self._parent()
        if self.path.exists() or self.path.is_symlink():
            raise VerificationError("verification evidence already exists")
        encoded = self._encoded(value)
        temporary = parent / f".{self.path.name}.{os.getpid()}.start"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                _write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.link(temporary, self.path, follow_symlinks=False)
            temporary.unlink()
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError as error:
            temporary.unlink(missing_ok=True)
            raise VerificationError("verification evidence already exists") from error
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise VerificationError(
                "verification evidence cannot be created"
            ) from error
        self._last_digest = hashlib.sha256(encoded).hexdigest()

    def update(self, value: dict[str, Any]) -> None:
        if self._last_digest is None:
            raise VerificationError("verification evidence was not initialized")
        parent = self._parent()
        current = _safe_regular_bytes(self.path, MAX_EVIDENCE_BYTES)
        if hashlib.sha256(current).hexdigest() != self._last_digest:
            raise VerificationError("verification evidence changed during verification")
        encoded = self._encoded(value)
        temporary = parent / f".{self.path.name}.{os.getpid()}.update"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                _write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.path)
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise VerificationError(
                "verification evidence cannot be updated"
            ) from error
        self._last_digest = hashlib.sha256(encoded).hexdigest()


def _package_entry(name: str, version: str) -> dict[str, Any]:
    role = "meta" if name == promotion.META_PACKAGE else "platform"
    platform = name.removeprefix("@openprose/prose-cli-")
    artifact = (
        f"openprose-prose-cli-{version}.tgz"
        if role == "meta"
        else f"openprose-prose-cli-{platform}-{version}.tgz"
    )
    return {
        "name": name,
        "role": role,
        "artifact": artifact,
        "outcome": "not-attempted",
        "sha256": None,
        "shasum": None,
        "integrity": None,
        "registryIntegrity": None,
        "provenancePredicateType": None,
        "alphaTagVerified": False,
    }


def new_evidence(
    inputs: VerificationInputs,
    *,
    draft_authority: DraftAuthorityInput,
    stable_latest: str,
    clock: Callable[[], str] = _now,
) -> dict[str, Any]:
    validate_draft_authority_input(draft_authority)
    timestamp = clock()
    return {
        "schema": SCHEMA,
        "status": "initialized",
        "version": inputs.version,
        "sourceSha": inputs.source_sha,
        "repository": inputs.repository,
        "releaseId": inputs.release_id,
        "tag": f"cli-v{inputs.version}",
        "registry": REGISTRY,
        "stableLatest": stable_latest,
        "alphaTag": ALPHA_TAG,
        "targetId": inputs.target_id,
        "workflowRun": {
            "repository": inputs.repository,
            "path": inputs.workflow_path,
            "id": inputs.workflow_run_id,
            "attempt": inputs.workflow_run_attempt,
        },
        "startedAt": timestamp,
        "updatedAt": timestamp,
        "draftAuthority": {
            "sha256": draft_authority.sha256,
            "controlSha": None,
            "releaseBody": None,
            "assetCount": 0,
            "assetInventorySha256": None,
            "workflowRun": {
                "path": draft.ALPHA_DRAFT_WORKFLOW,
                "id": draft_authority.workflow_run_id,
                "attempt": draft_authority.workflow_run_attempt,
            },
            "outcome": None,
            "verified": False,
        },
        "github": {
            "releaseVerified": False,
            "releaseBodyVerified": False,
            "tagVerified": False,
            "inventoryVerified": False,
            "sha256SumsVerified": False,
            "assetCount": 0,
            "assets": [],
        },
        "npm": {
            "stableLatestVerifiedBefore": False,
            "stableLatestVerifiedAfter": False,
            "packages": [
                _package_entry(name, inputs.version) for name in promotion.PACKAGE_ORDER
            ],
        },
        "journeys": [
            {
                "surface": surface,
                "targetId": inputs.target_id,
                "outcome": "not-attempted",
                "artifactSha256": [],
                "admissionSchema": None,
                "providerCalls": None,
                "semanticEvaluation": None,
            }
            for surface in SURFACES
        ],
        "attempts": [],
        "failure": None,
    }


def validate_evidence(value: Any) -> dict[str, Any]:
    """Validate the exact schema plus controller-only cross-field invariants."""

    global _EVIDENCE_VALIDATOR
    if _EVIDENCE_VALIDATOR is None:
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "release"
            / "alpha-public-verification.schema.json"
        )
        try:
            schema = json.loads(schema_path.read_text("utf-8"))
            Draft202012Validator.check_schema(schema)
            _EVIDENCE_VALIDATOR = Draft202012Validator(
                schema, format_checker=FormatChecker()
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VerificationError(
                "verification evidence schema is unavailable"
            ) from error
    if (
        not isinstance(value, dict)
        or next(_EVIDENCE_VALIDATOR.iter_errors(value), None) is not None
    ):
        raise VerificationError("verification evidence does not match its exact schema")

    report = value
    version = report["version"]
    authority = report["draftAuthority"]
    github = report["github"]
    npm = report["npm"]
    assets = github["assets"]
    packages = npm["packages"]
    journeys = report["journeys"]
    attempts = report["attempts"]

    authority_body = authority["releaseBody"]
    authority_verified = authority["verified"]
    authority_identity_complete = (
        authority["controlSha"] == report["sourceSha"]
        and isinstance(authority_body, dict)
        and authority_body.get("byteLength", 0) > 0
        and SHA256.fullmatch(authority_body.get("sha256", "")) is not None
        and authority["assetCount"] == 38
        and SHA256.fullmatch(authority["assetInventorySha256"] or "") is not None
        and authority["outcome"] in {"created", "resumed"}
    )
    if authority_verified != authority_identity_complete:
        raise VerificationError("draft authority evidence is inconsistent")
    if (
        authority["workflowRun"]["path"] != draft.ALPHA_DRAFT_WORKFLOW
        or authority["workflowRun"]["id"] <= 0
        or authority["workflowRun"]["attempt"] <= 0
    ):
        raise VerificationError("draft authority workflow evidence differs")

    if (
        report["tag"] != f"cli-v{version}"
        or report["workflowRun"]["repository"] != report["repository"]
        or github["assetCount"] != len(assets)
        or tuple(item["name"] for item in assets)
        not in ((), promotion.expected_asset_names(version))
        or tuple(item["name"] for item in packages) != promotion.PACKAGE_ORDER
        or tuple(item["surface"] for item in journeys) != SURFACES
        or any(item["targetId"] != report["targetId"] for item in journeys)
        or (github["releaseBodyVerified"] and not authority_verified)
        or any(
            item["sequence"] != sequence
            for sequence, item in enumerate(attempts, start=1)
        )
    ):
        raise VerificationError("verification evidence cross-field identity differs")

    for item in assets:
        if (item["attestationOutcome"] == "verified") != (
            item["attestationResultCount"] == 1
        ):
            raise VerificationError("GitHub attestation evidence is inconsistent")
    for index, item in enumerate(packages):
        expected = _package_entry(promotion.PACKAGE_ORDER[index], version)
        if any(
            item[field] != expected[field] for field in ("name", "role", "artifact")
        ):
            raise VerificationError("npm package artifact identity differs")
        if item["outcome"] == "verified" and not (
            item["sha256"]
            and item["shasum"]
            and item["integrity"]
            and item["registryIntegrity"] == item["integrity"]
            and item["provenancePredicateType"] == PROVENANCE_PREDICATE
            and item["alphaTagVerified"]
        ):
            raise VerificationError("npm package evidence is inconsistent")
    for item in journeys:
        if item["outcome"] == "verified" and not (
            item["artifactSha256"]
            and item["admissionSchema"] == JOURNEY_SCHEMA
            and item["providerCalls"] == PROVIDER_CALLS
            and item["semanticEvaluation"] is False
        ):
            raise VerificationError("package journey evidence is inconsistent")
    for item in attempts:
        if (item["outcome"] in {"started", "verified"}) != (item["code"] is None):
            raise VerificationError("attempt evidence is inconsistent")

    if authority_verified and github["inventoryVerified"]:
        inventory = [
            {
                "name": item["name"],
                "sha256": item["sha256"],
                "byteLength": item["byteLength"],
            }
            for item in assets
        ]
        inventory_digest = hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (
            len(inventory) != authority["assetCount"]
            or inventory_digest != authority["assetInventorySha256"]
        ):
            raise VerificationError("public inventory differs from draft authority")

    failure = report["failure"]
    if (report["status"] == "failed") != (failure is not None):
        raise VerificationError("verification failure settlement is inconsistent")
    if report["status"] == "complete" and (
        failure is not None
        or not authority_verified
        or not github["releaseBodyVerified"]
        or tuple(item["name"] for item in assets)
        != promotion.expected_asset_names(version)
        or not all(
            github[field]
            for field in (
                "releaseVerified",
                "tagVerified",
                "inventoryVerified",
                "sha256SumsVerified",
            )
        )
        or not all(
            item["downloaded"] and item["attestationOutcome"] == "verified"
            for item in assets
        )
        or not npm["stableLatestVerifiedBefore"]
        or not npm["stableLatestVerifiedAfter"]
        or not all(item["outcome"] == "verified" for item in packages)
        or not all(item["outcome"] == "verified" for item in journeys)
        or not all(item["outcome"] == "verified" for item in attempts)
    ):
        raise VerificationError("complete verification evidence is unsettled")
    return report


class ReadOnlyHttp:
    """Restrict the release controller's proven HTTP client to GET requests."""

    def __init__(self, delegate: promotion.StrictHttp | None = None) -> None:
        self._delegate = delegate or promotion.StrictHttp()

    def _open(self, request: Any, *, timeout: int) -> Any:
        if request.get_method() != "GET" or getattr(request, "data", None) is not None:
            raise VerificationError("public verifier attempted a non-read operation")
        return self._delegate._open(request, timeout=timeout)

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        expected: frozenset[int] = frozenset({200}),
        maximum: int = MAX_HTTP_BYTES,
        allow_missing: bool = False,
        github_asset_redirect: bool = False,
    ) -> tuple[int, bytes]:
        if method != "GET" or data is not None:
            raise VerificationError("public verifier attempted a non-read operation")
        try:
            return self._delegate.request(
                method="GET",
                url=url,
                headers=headers,
                expected=expected,
                maximum=maximum,
                allow_missing=allow_missing,
                github_asset_redirect=github_asset_redirect,
            )
        except promotion.PromotionError as error:
            raise VerificationError("public read boundary failed") from error


class PublicGitHub:
    """Read-only projection of the exact release identity already admitted."""

    def __init__(
        self,
        *,
        repository: str,
        source_sha: str,
        token: str,
        http: ReadOnlyHttp,
    ) -> None:
        self.repository = repository
        self.source_sha = source_sha
        self.http = http
        try:
            self._release = promotion.GitHubRelease(
                repository=repository, token=token, http=http  # type: ignore[arg-type]
            )
        except promotion.PromotionError as error:
            raise VerificationError("GitHub read boundary is malformed") from error

    def release(self, release_id: int, version: str) -> ReleaseIdentity:
        try:
            value = self._release._json(
                "GET",
                f"https://api.github.com/repos/{self.repository}/releases/{release_id}",
            )
            body = value.get("body")
            if not isinstance(body, str):
                raise VerificationError("GitHub public release body is malformed")
            encoded_body = body.encode("utf-8")
            if not 1 <= len(encoded_body) <= draft.MAX_RELEASE_NOTES_BYTES:
                raise VerificationError("GitHub public release body is malformed")
            if (
                value.get("id") != release_id
                or value.get("url")
                != f"https://api.github.com/repos/{self.repository}/releases/{release_id}"
                or value.get("assets_url")
                != (
                    f"https://api.github.com/repos/{self.repository}/releases/"
                    f"{release_id}/assets"
                )
                or value.get("tag_name") != f"cli-v{version}"
                or value.get("name") != f"OpenProse CLI v{version} functional alpha"
                or value.get("draft") is not False
                or value.get("prerelease") is not True
                or value.get("immutable") is not False
            ):
                raise VerificationError(
                    "GitHub public prerelease identity is not exact"
                )
        except (promotion.PromotionError, VerificationError) as error:
            raise VerificationError(
                "GitHub public prerelease identity is not exact"
            ) from error
        return ReleaseIdentity(
            release_id=release_id,
            tag=f"cli-v{version}",
            name=f"OpenProse CLI v{version} functional alpha",
            draft=False,
            prerelease=True,
            immutable=False,
            body_byte_length=len(encoded_body),
            body_sha256=hashlib.sha256(encoded_body).hexdigest(),
        )

    def tag_source(self, version: str) -> str:
        try:
            self._release._resolve_tag(version, self.source_sha)
        except promotion.PromotionError as error:
            raise VerificationError(
                "GitHub release tag could not be authenticated"
            ) from error
        return self.source_sha

    def assets(self, release_id: int, version: str) -> tuple[RemoteAsset, ...]:
        try:
            identities, urls = self._release._asset_identities(
                release_id=release_id, version=version
            )
        except promotion.PromotionError as error:
            raise VerificationError(
                "GitHub release asset inventory is not exact"
            ) from error
        return tuple(
            RemoteAsset(
                name=item.name,
                asset_id=int(urls[item.name].rsplit("/", 1)[1]),
                api_url=urls[item.name],
                byte_length=item.byte_length,
                sha256=item.sha256,
            )
            for item in identities
        )

    def download(self, asset: RemoteAsset) -> bytes:
        try:
            _, body = self.http.request(
                method="GET",
                url=asset.api_url,
                headers=promotion._github_headers(
                    self._release.token, content_type="application/octet-stream"
                ),
                maximum=draft.MAX_ALPHA_ASSET_BYTES,
                github_asset_redirect=True,
            )
        except (promotion.PromotionError, VerificationError) as error:
            raise VerificationError(
                "GitHub release asset could not be downloaded"
            ) from error
        return body


class PublicNpmRegistry(promotion.PublicNpmRegistry):
    def __init__(self, http: ReadOnlyHttp) -> None:
        super().__init__(http)  # type: ignore[arg-type]


class ProcessResult:
    def __init__(self, *, exit_code: int, stdout: bytes) -> None:
        self.exit_code = exit_code
        self.stdout = stdout


class ProcessBoundary(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        environment: dict[str, str],
        timeout_seconds: int,
        maximum_stdout: int,
    ) -> ProcessResult:
        ...


class BoundedProcess:
    """Run one argv directly with bounded output, time, and process-group cleanup."""

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: dict[str, str],
        timeout_seconds: int,
        maximum_stdout: int,
    ) -> ProcessResult:
        if not argv or timeout_seconds <= 0 or maximum_stdout <= 0:
            raise VerificationError("attestation process inputs are malformed")
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
            raise VerificationError("attestation verifier could not start") from error
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout = bytearray()
        stderr_bytes = 0
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
                        if len(stdout) > maximum_stdout:
                            oversized = True
                            break
                    else:
                        stderr_bytes += len(chunk)
                        if stderr_bytes > maximum_stdout:
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
                raise TimeoutError(
                    "bounded attestation verification did not settle"
                ) from error
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        if timed_out:
            raise TimeoutError("bounded attestation verification expired")
        if oversized:
            raise VerificationError("attestation verifier output exceeded its limit")
        return ProcessResult(exit_code=exit_code, stdout=bytes(stdout))


class GhAttestationVerifier:
    def __init__(
        self,
        *,
        executable: str = "gh",
        token: str = "",
        process: ProcessBoundary | None = None,
    ) -> None:
        resolved = shutil.which(executable)
        if resolved is None:
            raise VerificationError("GitHub CLI is unavailable")
        try:
            path = Path(resolved).resolve(strict=True)
            metadata = path.stat()
        except OSError as error:
            raise VerificationError(
                "GitHub CLI cannot be authenticated locally"
            ) from error
        if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
            raise VerificationError("GitHub CLI is not an executable regular file")
        if "\r" in token or "\n" in token:
            raise VerificationError("GitHub token is malformed")
        self.executable = str(path)
        self.token = token
        self.process = process or BoundedProcess()

    def verify(
        self,
        *,
        path: Path,
        name: str,
        sha256: str,
        repository: str,
        source_sha: str,
    ) -> Sequence[AttestationResult]:
        if path.name != name or SHA256.fullmatch(sha256) is None:
            raise VerificationError("attestation subject is malformed")
        argv = [
            self.executable,
            "attestation",
            "verify",
            str(path),
            "--repo",
            repository,
            "--source-digest",
            source_sha,
            "--signer-workflow",
            f"{repository}/{SIGNER_WORKFLOW_PATH}",
            "--predicate-type",
            PROVENANCE_PREDICATE,
            "--deny-self-hosted-runners",
            "--format",
            "json",
            "--limit",
            "2",
        ]
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "LC_ALL": "C",
        }
        if self.token:
            environment["GH_TOKEN"] = self.token
        result = self.process.run(
            argv,
            environment=environment,
            timeout_seconds=ATTESTATION_TIMEOUT_SECONDS,
            maximum_stdout=MAX_ATTESTATION_BYTES,
        )
        if result.exit_code != 0:
            raise VerificationError("GitHub artifact attestation did not verify")
        value = _closed_json(
            result.stdout, "GitHub attestation result", MAX_ATTESTATION_BYTES
        )
        if not isinstance(value, list):
            raise VerificationError("GitHub attestation result must be an array")
        normalized: list[AttestationResult] = []
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {
                "attestation",
                "verificationResult",
            }:
                raise VerificationError("GitHub attestation result is not closed")
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
                raise VerificationError("GitHub attestation statement is malformed")
            matching = []
            for subject in subjects:
                if not isinstance(subject, dict):
                    continue
                digest = subject.get("digest")
                subject_name = subject.get("name")
                if (
                    subject_name in {name, f"release-assets/{name}"}
                    and isinstance(digest, dict)
                    and set(digest) == {"sha256"}
                    and digest["sha256"] == sha256
                ):
                    matching.append(subject)
            if len(matching) != 1:
                raise VerificationError("GitHub attestation subject is not exact")
            normalized.append(
                AttestationResult(
                    repository=repository,
                    source_sha=source_sha,
                    predicate_type=PROVENANCE_PREDICATE,
                    subject_name=name,
                    subject_sha256=sha256,
                )
            )
        return tuple(normalized)


class _Recorder:
    def __init__(
        self,
        evidence: dict[str, Any],
        writer: EvidenceWriter,
        clock: Callable[[], str],
    ) -> None:
        self.evidence = evidence
        self.writer = writer
        self.clock = clock

    def persist(self) -> None:
        self.evidence["updatedAt"] = self.clock()
        self.writer.update(self.evidence)

    def start(self, boundary: str, subject: str) -> dict[str, Any]:
        if (
            boundary not in ATTEMPT_BOUNDARIES
            or SAFE_SUBJECT.fullmatch(subject) is None
        ):
            raise VerificationError("internal attempt identity is unsafe")
        attempt = {
            "sequence": len(self.evidence["attempts"]) + 1,
            "boundary": boundary,
            "subject": subject,
            "outcome": "started",
            "code": None,
        }
        self.evidence["status"] = "in-progress"
        self.evidence["attempts"].append(attempt)
        self.persist()
        return attempt

    def finish(self, attempt: dict[str, Any]) -> None:
        attempt["outcome"] = "verified"
        self.persist()

    def fail(
        self, attempt: dict[str, Any], *, code: str, ambiguous: bool = False
    ) -> None:
        attempt["outcome"] = "ambiguous" if ambiguous else "failed"
        attempt["code"] = code
        self.evidence["status"] = "failed"
        self.evidence["failure"] = {"stage": attempt["boundary"], "code": code}
        self.persist()


def _call(
    recorder: _Recorder,
    *,
    boundary: str,
    subject: str,
    operation: Callable[[], Any],
    validate: Callable[[Any], None],
) -> Any:
    attempt = recorder.start(boundary, subject)
    try:
        result = operation()
    except (TimeoutError, subprocess.TimeoutExpired):
        recorder.fail(attempt, code="TIMEOUT", ambiguous=True)
        raise VerificationError(f"{boundary} timed out") from None
    except Exception:
        recorder.fail(attempt, code="BOUNDARY_FAILED", ambiguous=True)
        raise VerificationError(f"{boundary} could not be authenticated") from None
    try:
        validate(result)
    except Exception:
        recorder.fail(attempt, code="MISMATCH")
        raise VerificationError(
            f"{boundary} did not match the admitted release"
        ) from None
    recorder.finish(attempt)
    return result


def _release_exact(
    value: Any,
    inputs: VerificationInputs,
    authority: promotion.DraftAuthority,
) -> None:
    if value != ReleaseIdentity(
        release_id=inputs.release_id,
        tag=f"cli-v{inputs.version}",
        name=f"OpenProse CLI v{inputs.version} functional alpha",
        draft=False,
        prerelease=True,
        immutable=False,
        body_byte_length=authority.body_byte_length,
        body_sha256=authority.body_sha256,
    ):
        raise VerificationError("release identity mismatch")


def _inventory_exact(
    values: Any,
    version: str,
    authority_assets: tuple[promotion.ReleaseAsset, ...],
) -> None:
    expected = promotion.expected_asset_names(version)
    if not isinstance(values, tuple) or tuple(item.name for item in values) != expected:
        raise VerificationError("asset inventory mismatch")
    ids: set[int] = set()
    for item in values:
        if (
            not isinstance(item, RemoteAsset)
            or not isinstance(item.asset_id, int)
            or isinstance(item.asset_id, bool)
            or item.asset_id in ids
            or item.asset_id <= 0
            or item.byte_length <= 0
            or item.byte_length > draft.MAX_ALPHA_ASSET_BYTES
            or SHA256.fullmatch(item.sha256) is None
        ):
            raise VerificationError("asset identity mismatch")
        ids.add(item.asset_id)
    observed = tuple(
        promotion.ReleaseAsset(
            name=item.name,
            byte_length=item.byte_length,
            sha256=item.sha256,
        )
        for item in values
    )
    if observed != authority_assets:
        raise VerificationError("asset inventory differs from draft authority")


def _candidate_exact(
    value: Any,
    inventory: tuple[RemoteAsset, ...],
    authority: promotion.DraftAuthority,
) -> None:
    if not isinstance(value, promotion.Candidate) or value.assets != tuple(
        promotion.ReleaseAsset(
            name=item.name,
            byte_length=item.byte_length,
            sha256=item.sha256,
        )
        for item in inventory
    ):
        raise VerificationError("assembly mismatch")
    try:
        promotion.require_draft_authority_candidate(authority, value)
    except promotion.PromotionError as error:
        raise VerificationError("assembly differs from draft authority") from error


def _write_asset(root: Path, asset: RemoteAsset, body: bytes) -> Path:
    if (
        not isinstance(body, bytes)
        or len(body) != asset.byte_length
        or hashlib.sha256(body).hexdigest() != asset.sha256
    ):
        raise VerificationError("downloaded release asset bytes differ")
    destination = root / asset.name
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_all(descriptor, body)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise VerificationError(
            "downloaded release asset cannot be retained"
        ) from error
    return destination


def _journey_request(
    *, candidate: promotion.Candidate, root: Path, target_id: str
) -> JourneyRequest:
    platform = TARGET_PLATFORMS[target_id]
    names = {
        "direct-rust": (
            f"openprose-prose-cli-rust-{candidate.version}-{platform}.tar.gz",
        ),
        "direct-bun": (
            f"openprose-prose-cli-bun-{candidate.version}-{platform}.tar.gz",
        ),
        "npm-launcher": (
            f"openprose-prose-cli-{platform}-{candidate.version}.tgz",
            f"openprose-prose-cli-{candidate.version}.tgz",
        ),
    }
    assets = {asset.name: asset for asset in candidate.assets}
    journeys: list[JourneySpec] = []
    for surface in SURFACES:
        journey_assets = tuple(
            JourneyArtifact(name=name, path=root / name, sha256=assets[name].sha256)
            for name in names[surface]
        )
        journeys.append(
            JourneySpec(
                surface=surface,
                target_id=target_id,
                version=candidate.version,
                source_sha=candidate.source_sha,
                artifacts=journey_assets,
            )
        )
    return JourneyRequest(journeys=tuple(journeys))


def _journeys_exact(values: Any, request: JourneyRequest) -> tuple[JourneyResult, ...]:
    if not isinstance(values, (tuple, list)) or len(values) != len(SURFACES):
        raise VerificationError("journey result count is ambiguous")
    result = tuple(values)
    for index, item in enumerate(result):
        expected = request.journeys[index]
        if not isinstance(item, JourneyResult) or item != JourneyResult(
            surface=expected.surface,
            target_id=expected.target_id,
            version=expected.version,
            source_sha=expected.source_sha,
            schema=JOURNEY_SCHEMA,
            status=JOURNEY_STATUS,
            provider_calls=PROVIDER_CALLS,
            semantic_evaluation=False,
            artifact_sha256=tuple(artifact.sha256 for artifact in expected.artifacts),
        ):
            raise VerificationError("journey result is not an exact provider-free pass")
    return result


def _require_latest(
    registry: RegistryBoundary, lineage: promotion.Lineage
) -> promotion.RegistryPackage:
    state = registry.package(promotion.META_PACKAGE)
    if state is None:
        raise VerificationError("stable npm package is unavailable")
    if (
        set(state.owners) != set(lineage.owners)
        or len(state.owners) != len(lineage.owners)
        or state.tags.get("latest") != lineage.latest
    ):
        raise VerificationError("stable npm lineage differs")
    return state


def execute_verification(
    *,
    inputs: VerificationInputs,
    draft_authority: DraftAuthorityInput,
    lineage: promotion.Lineage,
    github: GitHubReadBoundary,
    attestations: AttestationBoundary,
    registry: RegistryBoundary,
    journeys: JourneyBoundary,
    writer: EvidenceWriter,
    workspace: Path,
    clock: Callable[[], str] = _now,
) -> dict[str, Any]:
    """Run the single-attempt, read-only post-public verification controller."""

    validate_inputs(inputs)
    validate_draft_authority_input(draft_authority)
    if (
        re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", lineage.latest
        )
        is None
        or not lineage.owners
        or len(set(lineage.owners)) != len(lineage.owners)
        or any(
            not isinstance(owner, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", owner) is None
            for owner in lineage.owners
        )
    ):
        raise VerificationError("registry lineage authority is incomplete")
    if workspace.exists() or workspace.is_symlink():
        raise VerificationError("verification workspace must not already exist")
    try:
        workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chmod(workspace, 0o700)
    except OSError as error:
        raise VerificationError("verification workspace cannot be created") from error

    evidence = new_evidence(
        inputs,
        draft_authority=draft_authority,
        stable_latest=lineage.latest,
        clock=clock,
    )
    writer.start(evidence)
    recorder = _Recorder(evidence, writer, clock)

    authority = _call(
        recorder,
        boundary="draft-authority",
        subject=promotion.DRAFT_AUTHORITY_FILENAME,
        operation=lambda: promotion.load_draft_authority(
            draft_authority.path,
            expected_sha256=draft_authority.sha256,
            repository=inputs.repository,
            release_id=inputs.release_id,
            version=inputs.version,
            source_sha=inputs.source_sha,
            producer_run_id=draft_authority.workflow_run_id,
            producer_run_attempt=draft_authority.workflow_run_attempt,
        ),
        validate=lambda value: (
            None
            if isinstance(value, promotion.DraftAuthority)
            else (_ for _ in ()).throw(
                VerificationError("draft authority type differs")
            )
        ),
    )
    evidence["draftAuthority"].update(
        {
            "controlSha": authority.control_sha,
            "releaseBody": {
                "byteLength": authority.body_byte_length,
                "sha256": authority.body_sha256,
            },
            "assetCount": len(authority.assets),
            "assetInventorySha256": authority.asset_inventory_sha256,
            "outcome": authority.outcome,
            "verified": True,
        }
    )
    recorder.persist()

    release = _call(
        recorder,
        boundary="github-release",
        subject=str(inputs.release_id),
        operation=lambda: github.release(inputs.release_id, inputs.version),
        validate=lambda value: _release_exact(value, inputs, authority),
    )
    del release
    evidence["github"]["releaseVerified"] = True
    evidence["github"]["releaseBodyVerified"] = True
    recorder.persist()

    _call(
        recorder,
        boundary="github-tag",
        subject=f"cli-v{inputs.version}",
        operation=lambda: github.tag_source(inputs.version),
        validate=lambda value: (
            None
            if value == inputs.source_sha
            else (_ for _ in ()).throw(VerificationError("tag source mismatch"))
        ),
    )
    evidence["github"]["tagVerified"] = True
    recorder.persist()

    inventory = _call(
        recorder,
        boundary="github-inventory",
        subject=str(inputs.release_id),
        operation=lambda: github.assets(inputs.release_id, inputs.version),
        validate=lambda value: _inventory_exact(
            value, inputs.version, authority.assets
        ),
    )
    evidence["github"]["inventoryVerified"] = True
    evidence["github"]["assetCount"] = len(inventory)
    evidence["github"]["assets"] = [
        {
            "name": asset.name,
            "byteLength": asset.byte_length,
            "sha256": asset.sha256,
            "downloaded": False,
            "attestationOutcome": "not-attempted",
            "attestationResultCount": 0,
        }
        for asset in inventory
    ]
    recorder.persist()

    aggregate = 0
    paths: dict[str, Path] = {}
    for index, asset in enumerate(inventory):

        def accept_body(value: Any, *, item: RemoteAsset = asset) -> None:
            if not isinstance(value, bytes):
                raise VerificationError("release asset response is not bytes")
            if (
                len(value) != item.byte_length
                or hashlib.sha256(value).hexdigest() != item.sha256
            ):
                raise VerificationError("release asset byte identity mismatch")
            if aggregate + len(value) > draft.MAX_ASSEMBLY_BYTES:
                raise VerificationError("release asset assembly exceeds its byte limit")
            paths[item.name] = _write_asset(workspace, item, value)

        body = _call(
            recorder,
            boundary="github-asset-download",
            subject=asset.name,
            operation=lambda item=asset: github.download(item),
            validate=accept_body,
        )
        aggregate += len(body)
        evidence["github"]["assets"][index]["downloaded"] = True
        recorder.persist()

    candidate = _call(
        recorder,
        boundary="github-sha256s",
        subject="SHA256SUMS",
        operation=lambda: promotion.authenticate_assembly(
            root=workspace,
            repository=inputs.repository,
            release_id=inputs.release_id,
            version=inputs.version,
            source_sha=inputs.source_sha,
        ),
        validate=lambda value: _candidate_exact(value, inventory, authority),
    )
    evidence["github"]["sha256SumsVerified"] = True
    package_map = {package.name: package for package in candidate.packages}
    for item in evidence["npm"]["packages"]:
        package = package_map[item["name"]]
        item.update(
            {
                "sha256": package.sha256,
                "shasum": package.shasum,
                "integrity": package.integrity,
            }
        )
    recorder.persist()

    # Close the public GitHub observation after the downloads.
    _call(
        recorder,
        boundary="github-release",
        subject=str(inputs.release_id),
        operation=lambda: github.release(inputs.release_id, inputs.version),
        validate=lambda value: _release_exact(value, inputs, authority),
    )
    _call(
        recorder,
        boundary="github-tag",
        subject=f"cli-v{inputs.version}",
        operation=lambda: github.tag_source(inputs.version),
        validate=lambda value: (
            None
            if value == inputs.source_sha
            else (_ for _ in ()).throw(VerificationError("tag source mismatch"))
        ),
    )
    _call(
        recorder,
        boundary="github-inventory",
        subject=str(inputs.release_id),
        operation=lambda: github.assets(inputs.release_id, inputs.version),
        validate=lambda value: (
            None
            if value == inventory
            else (_ for _ in ()).throw(VerificationError("inventory changed"))
        ),
    )

    for index, asset in enumerate(inventory):
        results = _call(
            recorder,
            boundary="github-attestation",
            subject=asset.name,
            operation=lambda item=asset: attestations.verify(
                path=paths[item.name],
                name=item.name,
                sha256=item.sha256,
                repository=inputs.repository,
                source_sha=inputs.source_sha,
            ),
            validate=lambda values, item=asset: _validate_attestations(
                values, item, inputs
            ),
        )
        evidence["github"]["assets"][index]["attestationResultCount"] = min(
            len(results), 2
        )
        evidence["github"]["assets"][index]["attestationOutcome"] = "verified"
        recorder.persist()

    _call(
        recorder,
        boundary="npm-latest",
        subject=promotion.META_PACKAGE,
        operation=lambda: _require_latest(registry, lineage),
        validate=lambda value: None,
    )
    evidence["npm"]["stableLatestVerifiedBefore"] = True
    recorder.persist()

    for index, package in enumerate(candidate.packages):
        integrity = _call(
            recorder,
            boundary="npm-package",
            subject=package.name,
            operation=lambda item=package: promotion._verify_public_package(
                registry=registry,
                package=item,
                lineage=lineage,
                require_alpha=True,
            ),
            validate=lambda value, item=package: (
                None
                if value == item.integrity
                else (_ for _ in ()).throw(
                    VerificationError("registry integrity mismatch")
                )
            ),
        )
        entry = evidence["npm"]["packages"][index]
        entry.update(
            {
                "outcome": "verified",
                "registryIntegrity": integrity,
                "provenancePredicateType": PROVENANCE_PREDICATE,
                "alphaTagVerified": True,
            }
        )
        recorder.persist()

    _call(
        recorder,
        boundary="npm-latest",
        subject=promotion.META_PACKAGE,
        operation=lambda: _require_latest(registry, lineage),
        validate=lambda value: None,
    )
    evidence["npm"]["stableLatestVerifiedAfter"] = True
    recorder.persist()

    request = _journey_request(
        candidate=candidate, root=workspace, target_id=inputs.target_id
    )
    for index, spec in enumerate(request.journeys):
        evidence["journeys"][index]["outcome"] = "not-attempted"
        evidence["journeys"][index]["artifactSha256"] = [
            artifact.sha256 for artifact in spec.artifacts
        ]
    recorder.persist()
    result = _call(
        recorder,
        boundary="package-journeys",
        subject=inputs.target_id,
        operation=lambda: journeys.run(request),
        validate=lambda value: _journeys_exact(value, request),
    )
    normalized = _journeys_exact(result, request)
    for index, item in enumerate(normalized):
        evidence["journeys"][index].update(
            {
                "outcome": "verified",
                "admissionSchema": item.schema,
                "providerCalls": item.provider_calls,
                "semanticEvaluation": item.semantic_evaluation,
            }
        )
    recorder.persist()

    evidence["status"] = "complete"
    evidence["failure"] = None
    recorder.persist()
    return validate_evidence(evidence)


def _validate_attestations(
    values: Any, asset: RemoteAsset, inputs: VerificationInputs
) -> None:
    if not isinstance(values, (tuple, list)) or len(values) != 1:
        raise VerificationError("attestation result count is ambiguous")
    expected = AttestationResult(
        repository=inputs.repository,
        source_sha=inputs.source_sha,
        predicate_type=PROVENANCE_PREDICATE,
        subject_name=asset.name,
        subject_sha256=asset.sha256,
    )
    if values[0] != expected:
        raise VerificationError("attestation result identity differs")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository", required=True)
    result.add_argument("--release-id", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--source-sha", required=True)
    result.add_argument("--target-id", required=True)
    result.add_argument("--workflow-path", required=True)
    result.add_argument("--workflow-run-id", required=True)
    result.add_argument("--workflow-run-attempt", required=True)
    result.add_argument("--confirmation", required=True)
    result.add_argument("--draft-authority", type=Path, required=True)
    result.add_argument("--draft-authority-sha256", required=True)
    result.add_argument("--draft-workflow-run-id", required=True)
    result.add_argument("--draft-workflow-run-attempt", required=True)
    result.add_argument("--evidence", type=Path, required=True)
    return result


def draft_authority_from_args(args: argparse.Namespace) -> DraftAuthorityInput:
    numeric: dict[str, int] = {}
    for attribute in ("draft_workflow_run_id", "draft_workflow_run_attempt"):
        raw = getattr(args, attribute)
        if not isinstance(raw, str) or re.fullmatch(r"[1-9][0-9]*", raw) is None:
            raise VerificationError("draft authority workflow identity is malformed")
        numeric[attribute] = int(raw)
    value = DraftAuthorityInput(
        path=args.draft_authority,
        sha256=args.draft_authority_sha256,
        workflow_run_id=numeric["draft_workflow_run_id"],
        workflow_run_attempt=numeric["draft_workflow_run_attempt"],
    )
    validate_draft_authority_input(value)
    return value


def inputs_from_args(args: argparse.Namespace) -> VerificationInputs:
    numeric: dict[str, int] = {}
    for attribute in ("release_id", "workflow_run_id", "workflow_run_attempt"):
        raw = getattr(args, attribute)
        if not isinstance(raw, str) or re.fullmatch(r"[1-9][0-9]*", raw) is None:
            raise VerificationError("numeric verification identity is malformed")
        numeric[attribute] = int(raw)
    inputs = VerificationInputs(
        repository=args.repository,
        release_id=numeric["release_id"],
        version=args.version,
        source_sha=args.source_sha,
        target_id=args.target_id,
        workflow_path=args.workflow_path,
        workflow_run_id=numeric["workflow_run_id"],
        workflow_run_attempt=numeric["workflow_run_attempt"],
        confirmation=args.confirmation,
    )
    validate_inputs(inputs)
    draft_authority_from_args(args)
    if args.evidence.name != "alpha-public-verification.json":
        raise VerificationError("evidence must use the canonical filename")
    return inputs


__all__ = [
    "AttestationBoundary",
    "AttestationResult",
    "DraftAuthorityInput",
    "EvidenceWriter",
    "GhAttestationVerifier",
    "GitHubReadBoundary",
    "JourneyArtifact",
    "JourneyBoundary",
    "JourneyRequest",
    "JourneyResult",
    "JourneySpec",
    "PublicGitHub",
    "PublicNpmRegistry",
    "ReadOnlyHttp",
    "RegistryBoundary",
    "VerificationError",
    "VerificationInputs",
    "draft_authority_from_args",
    "execute_verification",
    "expected_confirmation",
    "inputs_from_args",
    "new_evidence",
    "parser",
    "validate_evidence",
    "validate_draft_authority_input",
    "validate_inputs",
]
