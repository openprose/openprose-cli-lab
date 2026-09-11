#!/usr/bin/env python3
"""Build and validate deterministic embedded Skill Runtime Image bundles."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tempfile
import unicodedata
from typing import Any, Iterable


MAGIC = b"OPENPROSE-IMAGE-BUNDLE\x00\x01"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
MAX_PATH_BYTES = 4096
MAX_BUNDLE_BYTES = (
    MAX_IMAGE_BYTES + len(MAGIC) + 8 + (MAX_FILES * (12 + MAX_PATH_BYTES))
)
HEX_SHA256 = frozenset("0123456789abcdef")
PATH_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-/"
)
PLACEMENT_FIRST_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz")
PLACEMENT_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")


class BundleError(RuntimeError):
    """An image directory or embedded bundle failed closed validation."""


@dataclass(frozen=True)
class BundleFile:
    path: str
    bytes: bytes


@dataclass(frozen=True)
class ParsedBundle:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    files: tuple[BundleFile, ...]


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"manifest contains duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_manifest(manifest_bytes: bytes) -> dict[str, Any]:
    normalize_text(manifest_bytes, "manifest.json", MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(
            manifest_bytes,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                BundleError(f"manifest contains non-finite number {value}")
            ),
        )
    except BundleError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleError(f"manifest is not valid JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise BundleError("manifest root must be an object")
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    expect_keys(
        manifest,
        {
            "schema",
            "imageFormatVersion",
            "imageVersion",
            "languageVersion",
            "skillVersion",
            "runtimeContractVersion",
            "semanticSourceRevision",
            "purpose",
            "releaseEligible",
            "normalization",
            "payload",
            "modelVisibleBytes",
            "aggregateSha256",
            "instructionPlacements",
            "taskEnvelope",
            "oneFieldFraming",
            "terminalEnvelope",
            "minimumTransportRequirements",
        },
        "manifest",
    )
    if manifest["schema"] != "openprose.skill-runtime-image-manifest/1":
        raise BundleError("unsupported manifest schema")
    if manifest["imageFormatVersion"] != "openprose.skill-runtime-image/1":
        raise BundleError("unsupported image format")
    for key in (
        "imageVersion",
        "languageVersion",
        "skillVersion",
        "runtimeContractVersion",
        "semanticSourceRevision",
    ):
        expect_nonempty_string(manifest[key], key)
    if manifest["purpose"] not in {
        "canonical-language-runtime",
        "functional-alpha-placeholder",
        "sentinel-transport-test",
    }:
        raise BundleError("unsupported image purpose")
    if type(manifest["releaseEligible"]) is not bool:
        raise BundleError("releaseEligible must be boolean")
    if manifest["purpose"] == "sentinel-transport-test" and manifest["releaseEligible"]:
        raise BundleError("sentinel image cannot be release eligible")
    normalization = expect_object(manifest["normalization"], "normalization")
    expect_keys(
        normalization,
        {"encoding", "newlines", "byteOrderMark", "pathSeparator"},
        "normalization",
    )
    if normalization != {
        "encoding": "utf-8",
        "newlines": "lf",
        "byteOrderMark": "forbidden",
        "pathSeparator": "/",
    }:
        raise BundleError("unsupported normalization profile")

    payload = manifest["payload"]
    if not isinstance(payload, list) or not payload:
        raise BundleError("payload must be a non-empty array")
    if len(payload) > MAX_FILES - 3:
        raise BundleError("payload file count exceeds bundle limit")
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        descriptor = expect_object(raw, f"payload[{index}]")
        expect_keys(
            descriptor,
            {"path", "mediaType", "byteLength", "sha256"},
            f"payload[{index}]",
        )
        path = validate_path(descriptor["path"], "payload/")
        if path in seen:
            raise BundleError(f"duplicate declared path {path!r}")
        seen.add(path)
        if descriptor["mediaType"] != "text/markdown; charset=utf-8":
            raise BundleError("unsupported payload media type")
        byte_length = expect_size(
            descriptor["byteLength"], f"payload[{index}].byteLength"
        )
        if byte_length == 0:
            raise BundleError("payload byte length must be positive")
        expect_digest(descriptor["sha256"], f"payload[{index}].sha256")

    model = expect_object(manifest["modelVisibleBytes"], "modelVisibleBytes")
    expect_keys(model, {"serialization", "byteLength", "sha256"}, "modelVisibleBytes")
    if model["serialization"] != "ordered-raw-concatenation-v1":
        raise BundleError("unsupported model-visible serialization")
    expect_size(
        model["byteLength"],
        "modelVisibleBytes.byteLength",
        maximum=MAX_IMAGE_BYTES,
    )
    if model["byteLength"] <= 0 or model["byteLength"] > MAX_IMAGE_BYTES:
        raise BundleError("model-visible byte length exceeds image limit")
    expect_digest(model["sha256"], "modelVisibleBytes.sha256")

    aggregate = expect_object(manifest["aggregateSha256"], "aggregateSha256")
    expect_keys(aggregate, {"algorithm", "sha256"}, "aggregateSha256")
    if aggregate["algorithm"] != "sha256-path-length-nul-v1":
        raise BundleError("unsupported aggregate digest algorithm")
    expect_digest(aggregate["sha256"], "aggregateSha256.sha256")

    placements = manifest["instructionPlacements"]
    if not isinstance(placements, list) or not placements:
        raise BundleError("instructionPlacements must be a non-empty array")
    for index, raw in enumerate(placements):
        placement = expect_object(raw, f"instructionPlacements[{index}]")
        expect_keys(
            placement,
            {"id", "strictness", "preservesHarnessBasePrompt"},
            f"instructionPlacements[{index}]",
        )
        placement_id = expect_nonempty_string(
            placement["id"], f"instructionPlacements[{index}].id"
        )
        if placement_id[0] not in PLACEMENT_FIRST_CHARACTERS or any(
            character not in PLACEMENT_CHARACTERS for character in placement_id
        ):
            raise BundleError(
                "instruction placement id does not match manifest authority"
            )
        if placement["strictness"] not in {"strict", "degraded"}:
            raise BundleError("unsupported instruction placement strictness")
        if type(placement["preservesHarnessBasePrompt"]) is not bool:
            raise BundleError("preservesHarnessBasePrompt must be boolean")

    for name, identity_key in (
        ("taskEnvelope", "schemaId"),
        ("oneFieldFraming", "id"),
        ("terminalEnvelope", "schemaId"),
    ):
        artifact = expect_object(manifest[name], name)
        expect_keys(artifact, {identity_key, "path", "sha256"}, name)
        expect_nonempty_string(artifact[identity_key], f"{name}.{identity_key}")
        path = validate_path(artifact["path"], "contracts/")
        if path in seen:
            raise BundleError(f"duplicate declared path {path!r}")
        seen.add(path)
        expect_digest(artifact["sha256"], f"{name}.sha256")

    requirements = expect_object(
        manifest["minimumTransportRequirements"], "minimumTransportRequirements"
    )
    requirement_keys = {
        "nonInteractive",
        "structuredOutput",
        "ambientIsolation",
        "terminalEnvelope",
        "boundedStreaming",
    }
    expect_keys(requirements, requirement_keys, "minimumTransportRequirements")
    if any(requirements[key] != "required" for key in requirement_keys):
        raise BundleError("every minimum transport requirement must be required")


def expect_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{name} must be an object")
    return value


def expect_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise BundleError(
            f"{name} keys are not closed; missing={missing}, unknown={unknown}"
        )


def expect_nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BundleError(f"{name} must be a non-empty NUL-free string")
    return value


def expect_size(value: Any, name: str, *, maximum: int = MAX_ENTRY_BYTES) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise BundleError(f"{name} exceeds the {maximum}-byte limit")
    return value


def expect_digest(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_SHA256 for character in value)
    ):
        raise BundleError(f"{name} must be lowercase SHA-256")
    return value


def validate_path(value: Any, prefix: str) -> str:
    path = expect_nonempty_string(value, "image path")
    if unicodedata.normalize("NFC", path) != path:
        raise BundleError(f"image path is not NFC-normalized: {path!r}")
    encoded = path.encode("utf-8")
    if len(encoded) > MAX_PATH_BYTES:
        raise BundleError("image path exceeds bundle path limit")
    if (
        not path.startswith(prefix)
        or "\\" in path
        or any(character not in PATH_CHARACTERS for character in path)
    ):
        raise BundleError(f"unsafe image path {path!r}")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
        raise BundleError(f"unsafe image path {path!r}")
    return path


def normalize_text(value: bytes, name: str, maximum: int = MAX_ENTRY_BYTES) -> str:
    if len(value) > maximum:
        raise BundleError(f"{name} exceeds the {maximum}-byte limit")
    if value.startswith(b"\xef\xbb\xbf"):
        raise BundleError(f"{name} contains a forbidden byte-order mark")
    if b"\x00" in value:
        raise BundleError(f"{name} contains a forbidden NUL byte")
    if b"\r" in value:
        raise BundleError(f"{name} does not use LF-only newlines")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise BundleError(f"{name} is not canonical UTF-8: {error}") from error


def declared_paths(manifest: dict[str, Any]) -> list[str]:
    return [
        *(descriptor["path"] for descriptor in manifest["payload"]),
        manifest["taskEnvelope"]["path"],
        manifest["oneFieldFraming"]["path"],
        manifest["terminalEnvelope"]["path"],
    ]


def load_image_directory(
    image_root: Path, *, require_release_eligible: bool = False
) -> ParsedBundle:
    if image_root.is_symlink():
        raise BundleError("image root must not be a symlink")
    try:
        root = image_root.resolve(strict=True)
    except FileNotFoundError as error:
        raise BundleError(f"image root does not exist: {image_root}") from error
    if not root.is_dir():
        raise BundleError("image root must be a directory")
    actual_files: dict[str, Path] = {}
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in [*names, *files]:
            candidate = directory_path / name
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise BundleError(
                    f"image directory contains symlink: {candidate.relative_to(root)}"
                )
            if name in files and not stat.S_ISREG(metadata.st_mode):
                relative = candidate.relative_to(root)
                raise BundleError(
                    f"image directory contains non-regular file: {relative}"
                )
        for name in files:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            if relative in actual_files:
                raise BundleError(
                    f"image directory contains duplicate path {relative!r}"
                )
            actual_files[relative] = candidate
    manifest_path = actual_files.get("manifest.json")
    if manifest_path is None:
        raise BundleError("image directory is missing manifest.json")
    manifest_bytes = read_bounded(manifest_path, MAX_MANIFEST_BYTES)
    manifest = parse_manifest(manifest_bytes)
    if require_release_eligible and (
        manifest["releaseEligible"] is not True
        or manifest["purpose"] == "sentinel-transport-test"
    ):
        raise BundleError("image is not release eligible")
    paths = declared_paths(manifest)
    expected_files = {"manifest.json", *paths}
    observed_files = set(actual_files)
    missing = sorted(expected_files - observed_files)
    unlisted = sorted(observed_files - expected_files)
    if missing:
        raise BundleError(f"image directory is missing declared files: {missing}")
    if unlisted:
        raise BundleError(f"image directory contains unlisted files: {unlisted}")
    files = tuple(
        BundleFile(path, read_bounded(actual_files[path], MAX_ENTRY_BYTES))
        for path in paths
    )
    validate_image_bytes(manifest, files)
    total = len(manifest_bytes) + sum(len(item.bytes) for item in files)
    if total > MAX_IMAGE_BYTES:
        raise BundleError("image directory exceeds aggregate bundle size limit")
    return ParsedBundle(manifest, manifest_bytes, files)


def read_bounded(path: Path, maximum: int) -> bytes:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise BundleError(f"{path.name} must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise BundleError(f"{path.name} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise BundleError(f"{path.name} must be a regular file")
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise BundleError(f"{path.name} changed before it was opened")
        if opened.st_size > maximum:
            raise BundleError(f"{path.name} exceeds the {maximum}-byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            value = stream.read(maximum + 1)
        closed_over = os.fstat(descriptor)
        if len(value) > maximum:
            raise BundleError(f"{path.name} exceeds the {maximum}-byte limit")
        if opened.st_size != len(value) or closed_over.st_size != opened.st_size:
            raise BundleError(f"{path.name} changed while it was read")
        return value
    finally:
        os.close(descriptor)


def validate_image_bytes(manifest: dict[str, Any], files: Iterable[BundleFile]) -> None:
    ordered = tuple(files)
    paths = declared_paths(manifest)
    if [item.path for item in ordered] != paths:
        raise BundleError("bundle file order does not match manifest authority")
    lookup = {item.path: item.bytes for item in ordered}
    if len(lookup) != len(ordered):
        raise BundleError("bundle contains duplicate file paths")
    aggregate = hashlib.sha256()
    model_visible = bytearray()
    for descriptor in manifest["payload"]:
        path = descriptor["path"]
        body = lookup[path]
        normalize_text(body, path)
        if len(body) != descriptor["byteLength"]:
            raise BundleError(f"payload byte length mismatch: {path}")
        if sha256(body) != descriptor["sha256"]:
            raise BundleError(f"payload digest mismatch: {path}")
        model_visible.extend(body)
        aggregate.update(path.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(str(len(body)).encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(body)
        aggregate.update(b"\0")
    if len(model_visible) != manifest["modelVisibleBytes"]["byteLength"]:
        raise BundleError("model-visible byte length mismatch")
    if sha256(model_visible) != manifest["modelVisibleBytes"]["sha256"]:
        raise BundleError("model-visible digest mismatch")
    if aggregate.hexdigest() != manifest["aggregateSha256"]["sha256"]:
        raise BundleError("payload aggregate digest mismatch")
    for name in ("taskEnvelope", "oneFieldFraming", "terminalEnvelope"):
        descriptor = manifest[name]
        body = lookup[descriptor["path"]]
        normalize_text(body, descriptor["path"])
        if sha256(body) != descriptor["sha256"]:
            raise BundleError(
                f"external artifact digest mismatch: {descriptor['path']}"
            )


def encode_bundle(image: ParsedBundle) -> bytes:
    result = bytearray(MAGIC)
    result.extend(struct.pack(">I", len(image.manifest_bytes)))
    result.extend(image.manifest_bytes)
    result.extend(struct.pack(">I", len(image.files)))
    for item in image.files:
        path = item.path.encode("utf-8")
        result.extend(struct.pack(">I", len(path)))
        result.extend(path)
        result.extend(struct.pack(">Q", len(item.bytes)))
        result.extend(item.bytes)
    return bytes(result)


def build_bundle(image_root: Path, *, require_release_eligible: bool = False) -> bytes:
    return encode_bundle(
        load_image_directory(
            image_root, require_release_eligible=require_release_eligible
        )
    )


class _Reader:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.offset = 0

    def take(self, length: int, name: str) -> bytes:
        end = self.offset + length
        if length < 0 or end > len(self.value):
            raise BundleError(f"bundle is truncated at {name}")
        result = self.value[self.offset : end]
        self.offset = end
        return result

    def u32(self, name: str) -> int:
        return struct.unpack(">I", self.take(4, name))[0]

    def u64(self, name: str) -> int:
        return struct.unpack(">Q", self.take(8, name))[0]


def parse_bundle(
    bundle_bytes: bytes, *, require_release_eligible: bool = False
) -> ParsedBundle:
    if len(bundle_bytes) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle exceeds aggregate size limit")
    reader = _Reader(bundle_bytes)
    if reader.take(len(MAGIC), "magic") != MAGIC:
        raise BundleError("bundle magic/version is unsupported")
    manifest_length = reader.u32("manifest length")
    if manifest_length > MAX_MANIFEST_BYTES:
        raise BundleError("bundle manifest exceeds size limit")
    manifest_bytes = reader.take(manifest_length, "manifest bytes")
    manifest = parse_manifest(manifest_bytes)
    if require_release_eligible and (
        manifest["releaseEligible"] is not True
        or manifest["purpose"] == "sentinel-transport-test"
    ):
        raise BundleError("image is not release eligible")
    count = reader.u32("file count")
    if count > MAX_FILES:
        raise BundleError("bundle file count exceeds limit")
    files: list[BundleFile] = []
    seen: set[str] = set()
    total = len(manifest_bytes)
    for index in range(count):
        path_length = reader.u32(f"file[{index}] path length")
        if path_length == 0 or path_length > MAX_PATH_BYTES:
            raise BundleError("bundle path length is invalid")
        path_bytes = reader.take(path_length, f"file[{index}] path")
        if b"\x00" in path_bytes:
            raise BundleError("bundle path contains NUL")
        try:
            path = path_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise BundleError("bundle path is not UTF-8") from error
        prefix = "payload/" if path.startswith("payload/") else "contracts/"
        validate_path(path, prefix)
        if path in seen:
            raise BundleError(f"bundle contains duplicate path {path!r}")
        seen.add(path)
        length = reader.u64(f"file[{index}] length")
        if length > MAX_ENTRY_BYTES:
            raise BundleError("bundle file exceeds entry size limit")
        body = reader.take(length, f"file[{index}] bytes")
        normalize_text(body, path)
        total += len(body)
        if total > MAX_IMAGE_BYTES:
            raise BundleError("bundle files exceed aggregate size limit")
        files.append(BundleFile(path, body))
    if reader.offset != len(bundle_bytes):
        raise BundleError("bundle contains trailing bytes")
    parsed = ParsedBundle(manifest, manifest_bytes, tuple(files))
    validate_image_bytes(manifest, parsed.files)
    return parsed


def validate_bundle(
    bundle_bytes: bytes, *, require_release_eligible: bool = False
) -> ParsedBundle:
    return parse_bundle(bundle_bytes, require_release_eligible=require_release_eligible)


def check_bundle(
    image_root: Path,
    bundle_bytes: bytes,
    *,
    require_release_eligible: bool = False,
) -> None:
    expected = build_bundle(
        image_root, require_release_eligible=require_release_eligible
    )
    if bundle_bytes != expected:
        expected_digest = sha256(expected)
        observed_digest = sha256(bundle_bytes)
        raise BundleError(
            f"embedded bundle drift: expected {expected_digest}, "
            f"observed {observed_digest}"
        )
    validate_bundle(bundle_bytes, require_release_eligible=require_release_eligible)


def checksum_bytes(bundle_bytes: bytes) -> bytes:
    return f"{sha256(bundle_bytes)}\n".encode("ascii")


def write_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def check_checksum(path: Path, bundle_bytes: bytes) -> None:
    expected = checksum_bytes(bundle_bytes)
    try:
        observed = read_bounded(path, len(expected))
    except FileNotFoundError as error:
        raise BundleError(f"bundle checksum is missing: {path}") from error
    if observed != expected:
        raise BundleError("embedded bundle checksum drift")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("image_dir", type=Path)
    build.add_argument("bundle", type=Path)
    build.add_argument("--checksum", type=Path)
    build.add_argument("--require-release-eligible", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("bundle", type=Path)
    validate.add_argument("--checksum", type=Path)
    validate.add_argument("--require-release-eligible", action="store_true")
    check = commands.add_parser("check")
    check.add_argument("image_dir", type=Path)
    check.add_argument("bundle", type=Path)
    check.add_argument("--checksum", type=Path)
    check.add_argument("--require-release-eligible", action="store_true")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("bundle", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "build":
            value = build_bundle(
                args.image_dir,
                require_release_eligible=args.require_release_eligible,
            )
            write_atomic(args.bundle, value)
            if args.checksum is not None:
                write_atomic(args.checksum, checksum_bytes(value))
        elif args.command == "validate":
            value = read_bounded(args.bundle, MAX_BUNDLE_BYTES)
            validate_bundle(
                value, require_release_eligible=args.require_release_eligible
            )
            if args.checksum is not None:
                check_checksum(args.checksum, value)
        elif args.command == "check":
            value = read_bounded(args.bundle, MAX_BUNDLE_BYTES)
            check_bundle(
                args.image_dir,
                value,
                require_release_eligible=args.require_release_eligible,
            )
            if args.checksum is not None:
                check_checksum(args.checksum, value)
        else:
            value = read_bounded(args.bundle, MAX_BUNDLE_BYTES)
            parsed = validate_bundle(value)
            json.dump(
                {
                    "schema": "openprose.embedded-image-bundle-inspection/1",
                    "bundleSha256": sha256(value),
                    "imageVersion": parsed.manifest["imageVersion"],
                    "releaseEligible": parsed.manifest["releaseEligible"],
                    "payloadCount": len(parsed.manifest["payload"]),
                    "aggregateSha256": parsed.manifest["aggregateSha256"]["sha256"],
                    "modelVisibleSha256": parsed.manifest["modelVisibleBytes"][
                        "sha256"
                    ],
                },
                sys.stdout,
                indent=2,
                sort_keys=True,
            )
            sys.stdout.write("\n")
    except (BundleError, OSError) as error:
        print(f"image-bundle: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
