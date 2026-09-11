#!/usr/bin/env python3
"""Validate the pinned npm lineage and one functional-alpha successor version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Sequence


SCHEMA = "openprose.npm-registry-lineage/1"
REPORT_SCHEMA = "openprose.registry-lineage-admission/1"
MAX_AUTHORITY_BYTES = 64 * 1024
STABLE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
ALPHA = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"-alpha\.(0|[1-9][0-9]*)$"
)
RFC3339_UTC = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{3})?Z$"
)
SHA512_INTEGRITY = re.compile(r"^sha512-[A-Za-z0-9+/]+={0,2}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
PACKAGE = re.compile(r"^@openprose/prose-cli(?:-[a-z0-9-]+)?$")

EXPECTED_META_VERSIONS = (
    "0.1.0",
    "0.1.1",
    "0.1.2",
    "0.1.3",
    "0.1.4",
    "0.2.5",
    "0.13.0",
    "0.13.1",
    "0.14.0",
)
EXPECTED_PLATFORMS = (
    "@openprose/prose-cli-darwin-arm64",
    "@openprose/prose-cli-darwin-x64",
    "@openprose/prose-cli-linux-arm64-gnu",
    "@openprose/prose-cli-linux-x64-gnu",
)


class LineageError(ValueError):
    """The pinned registry authority is absent, unsafe, or inconsistent."""


def _stable(value: str, label: str) -> tuple[int, int, int]:
    match = STABLE.fullmatch(value)
    if match is None:
        raise LineageError(f"{label} must be an exact stable SemVer")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _alpha(value: str, label: str) -> tuple[int, int, int, int]:
    match = ALPHA.fullmatch(value)
    if match is None:
        raise LineageError(f"{label} must be an exact numbered alpha SemVer")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _read_regular(path: Path) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise LineageError(f"registry lineage authority is unavailable: {error}") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > MAX_AUTHORITY_BYTES
    ):
        raise LineageError(
            "registry lineage authority must be a bounded non-symlink regular file"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise LineageError(f"registry lineage authority cannot be opened: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise LineageError("registry lineage authority changed before open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_AUTHORITY_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_AUTHORITY_BYTES:
                raise LineageError("registry lineage authority exceeds its byte bound")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or total != opened.st_size
    ):
        raise LineageError("registry lineage authority changed while being read")
    return b"".join(chunks)


def _json_object(encoded: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LineageError(f"registry lineage authority has duplicate field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(encoded, object_pairs_hook=reject_duplicates)
    except LineageError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LineageError(f"registry lineage authority is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise LineageError("registry lineage authority must be an object")
    return value


def load_authority(path: Path) -> dict[str, Any]:
    return _json_object(_read_regular(path))


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise LineageError(f"{label} has unknown or missing fields")
    return value


def validate_authority(value: dict[str, Any]) -> None:
    _exact_object(
        value,
        {
            "schema",
            "registry",
            "observedAt",
            "observation",
            "metaPackage",
            "platformPackages",
            "successor",
            "releaseRequirements",
        },
        "registry lineage authority",
    )
    if value["schema"] != SCHEMA:
        raise LineageError("registry lineage schema is unsupported")
    if value["registry"] != "https://registry.npmjs.org":
        raise LineageError("registry lineage origin is not the public npm registry")
    if not isinstance(value["observedAt"], str) or RFC3339_UTC.fullmatch(
        value["observedAt"]
    ) is None:
        raise LineageError("registry observation time is not exact UTC")

    observation = _exact_object(
        value["observation"],
        {"method", "metaCommand", "ownersCommand", "platformCommandPattern"},
        "registry observation",
    )
    if observation != {
        "method": "public-npm-metadata",
        "metaCommand": "npm view @openprose/prose-cli name version versions dist-tags time dist --json",
        "ownersCommand": "npm view @openprose/prose-cli maintainers --json",
        "platformCommandPattern": "npm view @openprose/prose-cli-<platform> name version dist-tags --json",
    }:
        raise LineageError("registry observation method is not the pinned public query")

    meta = _exact_object(
        value["metaPackage"],
        {
            "name",
            "status",
            "latest",
            "latestPublishedAt",
            "latestIntegrity",
            "latestShasum",
            "owners",
            "publishedVersions",
        },
        "meta-package lineage",
    )
    if meta["name"] != "@openprose/prose-cli" or PACKAGE.fullmatch(meta["name"]) is None:
        raise LineageError("meta-package identity is not @openprose/prose-cli")
    if meta["status"] != "published":
        raise LineageError("the pre-existing meta package must remain recorded as published")
    if meta["owners"] != ["jose_at_prose", "dan_openprose"]:
        raise LineageError("pre-existing meta-package owners differ from the pinned observation")
    if meta["publishedVersions"] != list(EXPECTED_META_VERSIONS):
        raise LineageError("published meta-package history differs from the pinned observation")
    parsed_versions = [_stable(version, "published version") for version in meta["publishedVersions"]]
    if parsed_versions != sorted(parsed_versions) or len(set(parsed_versions)) != len(
        parsed_versions
    ):
        raise LineageError("published meta-package history is not unique and ordered")
    latest = _stable(meta["latest"], "pre-existing latest version")
    if latest != parsed_versions[-1]:
        raise LineageError("pre-existing latest does not match the published history")
    if (
        not isinstance(meta["latestPublishedAt"], str)
        or RFC3339_UTC.fullmatch(meta["latestPublishedAt"]) is None
        or not isinstance(meta["latestIntegrity"], str)
        or SHA512_INTEGRITY.fullmatch(meta["latestIntegrity"]) is None
        or not isinstance(meta["latestShasum"], str)
        or SHA1.fullmatch(meta["latestShasum"]) is None
    ):
        raise LineageError("pre-existing latest registry identity is malformed")

    platforms = value["platformPackages"]
    if not isinstance(platforms, list) or len(platforms) != len(EXPECTED_PLATFORMS):
        raise LineageError("platform package observation set is incomplete")
    observed_names: list[str] = []
    for record in platforms:
        item = _exact_object(record, {"name", "status"}, "platform package observation")
        name = item["name"]
        if not isinstance(name, str) or PACKAGE.fullmatch(name) is None:
            raise LineageError("platform package name is invalid")
        if item["status"] != "not-found":
            raise LineageError("a proposed platform package was already published")
        observed_names.append(name)
    if tuple(observed_names) != EXPECTED_PLATFORMS:
        raise LineageError("platform package observations differ from the closed cohort")

    successor = _exact_object(
        value["successor"],
        {
            "migrationFrom",
            "functionalAlphaCore",
            "firstVersion",
            "prereleaseIdentifier",
            "minimumSequence",
            "distributionTag",
            "stableDistributionTag",
            "versionReuse",
        },
        "successor policy",
    )
    core = _stable(successor["functionalAlphaCore"], "functional-alpha core")
    first = _alpha(successor["firstVersion"], "first functional-alpha version")
    expected_core = (latest[0], latest[1] + 1, 0)
    if (
        successor["migrationFrom"] != meta["latest"]
        or core != expected_core
        or first != (*core, 1)
        or successor["prereleaseIdentifier"] != "alpha"
        or successor["minimumSequence"] != 1
        or successor["distributionTag"] != "alpha"
        or successor["stableDistributionTag"] != "latest"
        or successor["versionReuse"] != "forbidden"
    ):
        raise LineageError("successor policy is not the next numbered alpha lineage")

    requirements = _exact_object(
        value["releaseRequirements"],
        {
            "liveRegistryRevalidation",
            "requireTargetVersionUnused",
            "publishPlatformPackagesBeforeMeta",
            "verifyRegistryIntegrityBeforeMeta",
            "preserveLatestDuringAlpha",
            "partialPublicationPolicy",
        },
        "registry release requirements",
    )
    if requirements != {
        "liveRegistryRevalidation": True,
        "requireTargetVersionUnused": True,
        "publishPlatformPackagesBeforeMeta": True,
        "verifyRegistryIntegrityBeforeMeta": True,
        "preserveLatestDuringAlpha": True,
        "partialPublicationPolicy": "deprecate-and-never-reuse",
    }:
        raise LineageError("registry release requirements are weakened or unknown")


def assess(path: Path, requested_version: str | None) -> dict[str, Any]:
    failures: list[str] = []
    encoded: bytes | None = None
    authority: dict[str, Any] | None = None
    try:
        encoded = _read_regular(path)
        authority = _json_object(encoded)
        validate_authority(authority)
    except (LineageError, KeyError, OSError, TypeError) as error:
        failures.append(str(error))

    first_version = None
    preexisting_latest = None
    distribution_tag = None
    version_source = "argument" if requested_version is not None else "authority-default"
    effective_version = requested_version
    if authority is not None:
        successor = authority.get("successor")
        meta = authority.get("metaPackage")
        if isinstance(successor, dict):
            first_version = successor.get("firstVersion")
            distribution_tag = successor.get("distributionTag")
            if effective_version is None and isinstance(first_version, str):
                effective_version = first_version
        if isinstance(meta, dict):
            preexisting_latest = meta.get("latest")

    if not failures:
        assert authority is not None
        assert isinstance(effective_version, str)
        successor = authority["successor"]
        core = _stable(successor["functionalAlphaCore"], "functional-alpha core")
        try:
            candidate = _alpha(effective_version, "requested functional-alpha version")
        except LineageError as error:
            failures.append(str(error))
        else:
            if candidate[:3] != core or candidate[3] < successor["minimumSequence"]:
                failures.append(
                    "requested version is outside the pinned 0.15.0-alpha.N successor lineage"
                )

    return {
        "schema": REPORT_SCHEMA,
        "status": "pass" if not failures else "fail",
        "authority": path.as_posix(),
        "authoritySha256": (
            hashlib.sha256(encoded).hexdigest() if encoded is not None else None
        ),
        "observedAt": authority.get("observedAt") if authority is not None else None,
        "registry": authority.get("registry") if authority is not None else None,
        "preexistingLatest": preexisting_latest,
        "canonicalFirstVersion": first_version,
        "requestedVersion": effective_version,
        "versionSource": version_source,
        "distributionTag": distribution_tag,
        "networkUsed": False,
        "liveRegistryRevalidationRequired": True,
        "failures": failures,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--authority", type=Path, required=True)
    result.add_argument("--version")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    report = assess(arguments.authority, arguments.version)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
