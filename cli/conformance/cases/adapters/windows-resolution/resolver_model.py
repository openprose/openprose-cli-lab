"""Provider-free reference model for the frozen Windows launch oracle.

This is intentionally not a product resolver.  It exists to make the shared
contract executable without starting any process or interpreting any language
input.  In particular, this module does not import a process-launch API and
never opens command shims.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable


PROFILE_KEYS = {
    "schema",
    "profileId",
    "adapterId",
    "status",
    "platform",
    "harnessVersion",
    "versionPattern",
    "resolution",
    "artifacts",
    "manifest",
    "argvPrefix",
    "fixedEnvironment",
    "inheritedEnvironmentNames",
    "strippedEnvironmentNames",
    "claims",
}
CLAIM_KEYS = {"productAdmission", "semanticConformance", "releaseEligibility"}
ARTIFACT_KEYS = {"role", "path", "size", "sha256", "format"}
MANIFEST_KEYS = {"artifactRole", "match", "expected"}
FIXED_ENV_KEYS = {"name", "value"}
NATIVE_KEYS = {
    "kind",
    "commandNames",
    "application",
    "packageName",
    "packageRootLayouts",
    "manifestPath",
}
RUNTIME_KEYS = {
    "kind",
    "commandNames",
    "runtimeApplication",
    "packageName",
    "packageRootLayouts",
    "entrypoint",
    "manifestPath",
}
ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@-]*$")
COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_NAME = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$")
FORMATS = {"pe-x64", "pe-arm64", "utf8-lf", "json-utf8", "canonical-json-lf"}
STATUSES = {"provider-free-fixture", "research-feasible-non-admitted"}
SHELL_SUFFIXES = (".cmd", ".bat", ".ps1")
MAX_ARTIFACT_BYTES = 1_073_741_824
MAX_ARTIFACTS = 64
MAX_SEARCH_DIRECTORIES = 32


class ResolutionError(ValueError):
    pass


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ResolutionError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json_strict(data: bytes) -> Any:
    if data.startswith(b"\xef\xbb\xbf") or b"\x00" in data:
        raise ResolutionError("JSON contains a forbidden encoding marker")
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_reject_duplicate)
    except ResolutionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as caught:
        raise ResolutionError("JSON is not strict UTF-8") from caught


def load_oracle(path: Path) -> dict[str, Any]:
    value = load_json_strict(path.read_bytes())
    if not isinstance(value, dict):
        raise ResolutionError("oracle is not an object")
    expected = {
        "schema",
        "oracleVersion",
        "frozenAt",
        "scope",
        "invariants",
        "claims",
        "researchProfiles",
        "providerFreeProfiles",
        "blockedAdapters",
        "sources",
    }
    _closed(value, expected, "oracle")
    if value["schema"] != "openprose.windows-resolution-oracle/1":
        raise ResolutionError("oracle schema is unsupported")
    _false_claims(value["claims"], {"productAdmission", "strictWrapperAdmission", "semanticConformance", "releaseEligibility"})
    profiles = value["researchProfiles"] + value["providerFreeProfiles"]
    ids: set[str] = set()
    for profile in profiles:
        validate_profile(profile)
        if profile["profileId"] in ids:
            raise ResolutionError("profileId is duplicated")
        ids.add(profile["profileId"])
    for blocker in value["blockedAdapters"]:
        _closed(blocker, {"adapterId", "frozenIdentity", "windowsLaunchFeasible", "reasons", "futureSafeRoute"}, "blocked adapter")
        if blocker["windowsLaunchFeasible"] is not False or not blocker["reasons"]:
            raise ResolutionError("a blocked adapter overstates Windows feasibility")
    return value


def validate_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise ResolutionError("profile is not an object")
    _closed(profile, PROFILE_KEYS, "profile")
    if profile["schema"] != "openprose.windows-launch-profile/1":
        raise ResolutionError("launch profile schema is unsupported")
    if not isinstance(profile["profileId"], str) or not IDENTIFIER.fullmatch(profile["profileId"]):
        raise ResolutionError("profileId is invalid")
    if not isinstance(profile["adapterId"], str) or "/" not in profile["adapterId"]:
        raise ResolutionError("adapterId is invalid")
    if profile["status"] not in STATUSES:
        raise ResolutionError("profile status is not non-admitting")
    if profile["platform"] not in {"win32-x64", "win32-arm64"}:
        raise ResolutionError("profile platform is unsupported")
    if not isinstance(profile["harnessVersion"], str) or not profile["harnessVersion"]:
        raise ResolutionError("harnessVersion is invalid")
    if not isinstance(profile["versionPattern"], str):
        raise ResolutionError("versionPattern is invalid")
    try:
        re.compile(profile["versionPattern"])
    except re.error as caught:
        raise ResolutionError("versionPattern is invalid") from caught
    _false_claims(profile["claims"], CLAIM_KEYS)

    artifacts = profile["artifacts"]
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ResolutionError("artifact inventory is invalid")
    roles: set[str] = set()
    paths: set[str] = set()
    artifact_by_role: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        _closed(artifact, ARTIFACT_KEYS, "artifact")
        if not isinstance(artifact["role"], str) or not IDENTIFIER.fullmatch(artifact["role"]):
            raise ResolutionError("artifact role is invalid")
        _safe_relative(artifact["path"])
        if artifact["role"] in roles or artifact["path"].casefold() in paths:
            raise ResolutionError("artifact role or path is duplicated")
        roles.add(artifact["role"])
        paths.add(artifact["path"].casefold())
        if not isinstance(artifact["size"], int) or not 1 <= artifact["size"] <= MAX_ARTIFACT_BYTES:
            raise ResolutionError("artifact size is invalid")
        if not isinstance(artifact["sha256"], str) or not SHA256.fullmatch(artifact["sha256"]):
            raise ResolutionError("artifact digest is invalid")
        if artifact["format"] not in FORMATS:
            raise ResolutionError("artifact format is invalid")
        artifact_by_role[artifact["role"]] = artifact
    application_artifact = artifact_by_role.get("application")
    if application_artifact is None or not application_artifact["format"].startswith("pe-"):
        raise ResolutionError("application artifact is missing or not PE")

    resolution = profile["resolution"]
    if not isinstance(resolution, dict):
        raise ResolutionError("resolution is not an object")
    kind = resolution.get("kind")
    if kind == "native-executable":
        if not set(resolution).issubset(NATIVE_KEYS) or not {"kind", "commandNames", "application"}.issubset(resolution):
            raise ResolutionError("native resolution shape is invalid")
        _command_names(resolution["commandNames"])
        _safe_relative(resolution["application"])
        if not resolution["application"].lower().endswith(".exe"):
            raise ResolutionError("native application is not an .exe")
        if application_artifact["path"] != resolution["application"]:
            raise ResolutionError("native application is not bound to its artifact")
        if profile["argvPrefix"] != []:
            raise ResolutionError("native argvPrefix must be empty")
        _validate_optional_package_resolution(resolution, profile, artifact_by_role)
    elif kind == "runtime-entrypoint":
        _closed(resolution, RUNTIME_KEYS, "runtime resolution")
        _command_names(resolution["commandNames"])
        if not isinstance(resolution["runtimeApplication"], str) or "/" in resolution["runtimeApplication"] or "\\" in resolution["runtimeApplication"]:
            raise ResolutionError("runtime application must be one executable name")
        if not resolution["runtimeApplication"].lower().endswith(".exe"):
            raise ResolutionError("runtime application is not an .exe")
        if application_artifact["path"] != resolution["runtimeApplication"]:
            raise ResolutionError("runtime application is not bound to its artifact")
        _required_package_resolution(resolution)
        _safe_relative(resolution["entrypoint"])
        entrypoint = artifact_by_role.get("entrypoint")
        if entrypoint is None or entrypoint["path"] != resolution["entrypoint"]:
            raise ResolutionError("entrypoint is not bound to its artifact")
        if profile["argvPrefix"] != [{"artifactRole": "entrypoint"}]:
            raise ResolutionError("runtime argvPrefix must bind exactly one entrypoint")
        _validate_manifest(profile["manifest"], artifact_by_role)
        if artifact_by_role["package-manifest"]["path"] != resolution["manifestPath"]:
            raise ResolutionError("manifest path is not bound to its artifact")
    else:
        raise ResolutionError("resolution kind is unsupported")

    _validate_environment(profile)


def resolve_profile(
    profile: dict[str, Any],
    search_directories: Iterable[Path],
    _forbidden_marker: Path,
) -> dict[str, Any]:
    """Resolve and authenticate a profile without executing any file."""

    validate_profile(profile)
    directories = list(search_directories)
    if not 1 <= len(directories) <= MAX_SEARCH_DIRECTORIES:
        raise ResolutionError("search directory count is invalid")
    candidates: list[dict[str, Any]] = []
    for directory in directories:
        if not directory.is_absolute():
            raise ResolutionError("search directory is not absolute")
        if profile["resolution"]["kind"] == "native-executable":
            candidate = _resolve_native(profile, directory)
            if candidate is not None:
                candidates.append(candidate)
        else:
            candidates.extend(_resolve_runtime(profile, directory))
    if not candidates:
        raise ResolutionError("no authenticated launch candidate")
    if len(candidates) != 1:
        raise ResolutionError("authenticated launch candidate is ambiguous")
    return candidates[0]


def build_command(resolved: dict[str, Any], arguments: list[str]) -> list[str]:
    prefix = [resolved["application"], *resolved["argvPrefix"]]
    command = [*prefix, *arguments]
    if len(command) > 64 or any(not isinstance(item, str) or "\x00" in item for item in command):
        raise ResolutionError("command exceeds the closed argv contract")
    return command


def validate_version_output(profile: dict[str, Any], output: str) -> bool:
    validate_profile(profile)
    if not isinstance(output, str) or "\x00" in output or len(output.encode("utf-8")) > 65_536:
        return False
    return re.fullmatch(profile["versionPattern"], output.strip()) is not None


def _resolve_native(profile: dict[str, Any], directory: Path) -> dict[str, Any] | None:
    resolution = profile["resolution"]
    package_name = resolution.get("packageName")
    roots: list[Path]
    if package_name is None:
        roots = [directory]
    else:
        roots = _package_roots(directory, resolution["packageRootLayouts"], package_name)
    results: list[dict[str, Any]] = []
    for root in roots:
        application = root / _relative_parts(resolution["application"])
        if not os.path.lexists(application):
            continue
        reads: list[str] = []
        _verify_inventory(profile, directory, root, reads)
        fixed = _materialize_fixed_environment(profile, root)
        results.append(_resolved(profile, application.resolve(), [], fixed, reads))
    if len(results) > 1:
        raise ResolutionError("native package layout is ambiguous")
    return results[0] if results else None


def _resolve_runtime(profile: dict[str, Any], directory: Path) -> list[dict[str, Any]]:
    resolution = profile["resolution"]
    runtime = directory / resolution["runtimeApplication"]
    if not os.path.lexists(runtime):
        return []
    results: list[dict[str, Any]] = []
    for package_root in _package_roots(directory, resolution["packageRootLayouts"], resolution["packageName"]):
        entrypoint = package_root / _relative_parts(resolution["entrypoint"])
        manifest = package_root / _relative_parts(resolution["manifestPath"])
        if not os.path.lexists(entrypoint) or not os.path.lexists(manifest):
            continue
        reads: list[str] = []
        _verify_inventory(profile, directory, package_root, reads)
        fixed = _materialize_fixed_environment(profile, package_root)
        results.append(_resolved(profile, runtime.resolve(), [str(entrypoint.resolve())], fixed, reads))
    return results


def _verify_inventory(profile: dict[str, Any], application_root: Path, package_root: Path, reads: list[str]) -> None:
    verified: dict[str, bytes] = {}
    for artifact in profile["artifacts"]:
        runtime_application = (
            profile["resolution"]["kind"] == "runtime-entrypoint"
            and artifact["role"] == "application"
        )
        base = application_root if runtime_application else package_root
        relative_path = _relative_parts(artifact["path"])
        artifact_path = base / relative_path
        _reject_linked_descendants(base, relative_path)
        data = _read_final_regular_file(artifact_path, artifact["size"], reads)
        if len(data) != artifact["size"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ResolutionError("artifact digest or size does not match the profile")
        _validate_format(data, artifact["format"])
        verified[artifact["role"]] = data
    if profile["manifest"] is not None:
        manifest_bytes = verified[profile["manifest"]["artifactRole"]]
        if load_json_strict(manifest_bytes) != profile["manifest"]["expected"]:
            raise ResolutionError("manifest does not match the exact profiled object")


def _reject_linked_descendants(root: Path, relative_path: Path) -> None:
    """Reject links/reparse points below an already-selected artifact root.

    A package root may itself be the one explicitly authorized junction selected
    by ``_package_roots``.  That root is canonicalized before this function is
    called.  No artifact path below it may redirect resolution again.
    """

    current = root
    for part in relative_path.parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as caught:
            raise ResolutionError("artifact parent is unavailable") from caught
        if _is_link_or_reparse(metadata):
            raise ResolutionError("intermediate artifact link is forbidden")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ResolutionError("artifact parent is not a directory")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_attribute)


def _read_final_regular_file(path: Path, expected_size: int, reads: list[str]) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as caught:
        raise ResolutionError("artifact is unavailable") from caught
    if _is_link_or_reparse(metadata):
        raise ResolutionError("final artifact link is forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise ResolutionError("artifact is not a regular file")
    if expected_size > MAX_ARTIFACT_BYTES:
        raise ResolutionError("artifact exceeds the read bound")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as caught:
        raise ResolutionError("artifact could not be opened without following links") from caught
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ResolutionError("artifact is not a regular file")
        if opened.st_size != expected_size:
            raise ResolutionError("artifact digest or size does not match the profile")
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) > expected_size:
        raise ResolutionError("artifact grew beyond its profiled size")
    reads.append(str(path.resolve()))
    return data


def _validate_format(data: bytes, artifact_format: str) -> None:
    if artifact_format.startswith("pe-"):
        if len(data) < 70 or data[0:2] != b"MZ":
            raise ResolutionError("application is not a shaped PE image")
        offset = int.from_bytes(data[0x3C:0x40], "little")
        if offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0":
            raise ResolutionError("application is not a shaped PE image")
        expected_machine = b"\x64\x86" if artifact_format == "pe-x64" else b"\x64\xaa"
        if data[offset + 4:offset + 6] != expected_machine:
            raise ResolutionError("PE machine does not match the profile")
    elif artifact_format in {"utf8-lf", "canonical-json-lf"}:
        if data.startswith(b"\xef\xbb\xbf") or b"\x00" in data or b"\r" in data or not data.endswith(b"\n"):
            raise ResolutionError("text artifact normalization is invalid")
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as caught:
            raise ResolutionError("text artifact is not UTF-8") from caught
        if artifact_format == "canonical-json-lf":
            value = load_json_strict(data)
            canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if canonical != data:
                raise ResolutionError("JSON artifact is not canonical")
    elif artifact_format == "json-utf8":
        load_json_strict(data)


def _validate_optional_package_resolution(
    resolution: dict[str, Any],
    profile: dict[str, Any],
    artifact_by_role: dict[str, dict[str, Any]],
) -> None:
    package_fields = {"packageName", "packageRootLayouts", "manifestPath"}
    present = package_fields.intersection(resolution)
    if present and present != package_fields:
        raise ResolutionError("native package resolution is incomplete")
    if present:
        _required_package_resolution(resolution)
        _validate_manifest(profile["manifest"], artifact_by_role)
        if artifact_by_role["package-manifest"]["path"] != resolution["manifestPath"]:
            raise ResolutionError("manifest path is not bound to its artifact")
    elif profile["manifest"] is not None:
        raise ResolutionError("direct native profile cannot carry a package manifest")


def _required_package_resolution(resolution: dict[str, Any]) -> None:
    if not isinstance(resolution["packageName"], str) or not PACKAGE_NAME.fullmatch(resolution["packageName"]):
        raise ResolutionError("package name is invalid")
    layouts = resolution["packageRootLayouts"]
    if not isinstance(layouts, list) or not 1 <= len(layouts) <= 8 or len(layouts) != len(set(layouts)):
        raise ResolutionError("package root layouts are invalid")
    for layout in layouts:
        if not isinstance(layout, str) or layout.count("{packageName}") != 1:
            raise ResolutionError("package layout token is invalid")
        _safe_relative(layout.replace("{packageName}", "package"))
    _safe_relative(resolution["manifestPath"])


def _validate_manifest(manifest: Any, artifact_by_role: dict[str, dict[str, Any]]) -> None:
    if not isinstance(manifest, dict):
        raise ResolutionError("package manifest binding is missing")
    _closed(manifest, MANIFEST_KEYS, "manifest binding")
    if manifest["artifactRole"] != "package-manifest" or manifest["artifactRole"] not in artifact_by_role:
        raise ResolutionError("package manifest artifact is not bound")
    if manifest["match"] != "exact-object" or not isinstance(manifest["expected"], dict):
        raise ResolutionError("package manifest match is not exact")


def _validate_environment(profile: dict[str, Any]) -> None:
    fixed_names: set[str] = set()
    for entry in profile["fixedEnvironment"]:
        _closed(entry, FIXED_ENV_KEYS, "fixed environment")
        name = entry["name"]
        value = entry["value"]
        if not isinstance(name, str) or not ENVIRONMENT_NAME.fullmatch(name) or name in fixed_names:
            raise ResolutionError("fixed environment name is invalid or duplicated")
        if not isinstance(value, str) or "\x00" in value or ("{" in value and value != "{packageRoot}"):
            raise ResolutionError("fixed environment value is invalid")
        fixed_names.add(name)
    inherited = _environment_names(profile["inheritedEnvironmentNames"], "inherited")
    stripped = _environment_names(profile["strippedEnvironmentNames"], "stripped")
    if fixed_names.intersection(inherited | stripped) or inherited.intersection(stripped):
        raise ResolutionError("environment policies overlap")


def _environment_names(value: Any, label: str) -> set[str]:
    if not isinstance(value, list) or len(value) != len(set(value)):
        raise ResolutionError(f"{label} environment names are invalid")
    result: set[str] = set()
    for name in value:
        if not isinstance(name, str) or not ENVIRONMENT_NAME.fullmatch(name):
            raise ResolutionError(f"{label} environment name is invalid")
        result.add(name)
    return result


def _materialize_fixed_environment(profile: dict[str, Any], package_root: Path) -> dict[str, str]:
    return {
        item["name"]: str(package_root.resolve()) if item["value"] == "{packageRoot}" else item["value"]
        for item in profile["fixedEnvironment"]
    }


def _resolved(
    profile: dict[str, Any],
    application: Path,
    argv_prefix: list[str],
    fixed: dict[str, str],
    reads: list[str],
) -> dict[str, Any]:
    return {
        "schema": "openprose.resolved-windows-launch/1",
        "profileId": profile["profileId"],
        "adapterId": profile["adapterId"],
        "application": str(application),
        "argvPrefix": argv_prefix,
        "fixedEnvironment": fixed,
        "inheritedEnvironmentNames": list(profile["inheritedEnvironmentNames"]),
        "strippedEnvironmentNames": list(profile["strippedEnvironmentNames"]),
        "readPaths": reads,
        "claims": dict(profile["claims"]),
    }


def _package_roots(directory: Path, layouts: list[str], package_name: str) -> list[Path]:
    roots: list[Path] = []
    for layout in layouts:
        expanded = layout.replace("{packageName}", package_name)
        root = directory / _relative_parts(expanded)
        if os.path.lexists(root) and root.is_dir():
            roots.append(root.resolve())
    return roots


def _relative_parts(value: str) -> Path:
    _safe_relative(value)
    return Path(*value.split("/"))


def _safe_relative(value: Any) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise ResolutionError("relative path is invalid")
    if "\\" in value or "\x00" in value or value.startswith("/") or ":" in value:
        raise ResolutionError("relative path is invalid")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ResolutionError("relative path contains traversal")
    if any(not re.fullmatch(r"[A-Za-z0-9@._{}-]+", part) for part in parts):
        raise ResolutionError("relative path contains a forbidden character")


def _command_names(value: Any) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 8 or len(value) != len(set(value)):
        raise ResolutionError("command names are invalid")
    for name in value:
        if not isinstance(name, str) or not COMMAND_NAME.fullmatch(name) or name.lower().endswith(SHELL_SUFFIXES):
            raise ResolutionError("command name is invalid")


def _false_claims(value: Any, keys: set[str]) -> None:
    if not isinstance(value, dict):
        raise ResolutionError("claims are not an object")
    _closed(value, keys, "claims")
    if any(item is not False for item in value.values()):
        raise ResolutionError("an oracle claim is overstated")


def _closed(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ResolutionError(f"{label} is not a closed object")
