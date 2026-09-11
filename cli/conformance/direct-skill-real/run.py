#!/usr/bin/env python3
"""Opt-in, bounded direct legacy-skill observations through installed Prime."""

from __future__ import annotations

import argparse
import ctypes
from copy import deepcopy
from collections import Counter
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
DEFAULT_POLICY = HERE / "policy.v1.json"
DEFAULT_MATRIX = HERE / "matrix.v1.json"
DEFAULT_EVIDENCE = HERE / "evidence/current"
EVIDENCE_SCHEMA = HERE / "evidence.schema.json"
SCHEMA = "openprose.direct-skill-real-evidence/1"
ANALYZER_VERSION = "direct-skill-effect-analyzer/3"
HISTORICAL_ANALYZER_VERSION = "direct-skill-effect-analyzer/2"
ANALYZER_OWNED_FIELDS = (
    ("toolAudit", "subagentStartsObserved"),
    ("workspace", "effectsPassed"),
    ("workspace", "subagentExecutionEvidenceCount"),
    ("workspace", "subagentExecutionProven"),
    ("observation", "analyzerVersion"),
    ("observation", "classification"),
)
TELEMETRY_DISABLED_BYTES = b"OPENPROSE_TELEMETRY=disabled\n"
CONTROLLED_PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
MAXIMUM_SKILL_FILE_BYTES = 8 * 1024 * 1024
MAXIMUM_SKILL_TREE_BYTES = 64 * 1024 * 1024
MAXIMUM_INTERPRETER_SUPPORT_BYTES = 128 * 1024 * 1024
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|key|token)-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|authorization)" r"\s*[:=]\s*[^\s,;\"']+"
    ),
)
PROSE_INVOCATION_PATTERNS = (
    re.compile(
        r"(?is)subprocess\.(?:run|Popen|call|check_call|check_output)"
        r"\s*\(\s*\[\s*['\"]prose['\"]"
    ),
    re.compile(
        r"(?im)(?:^|[;\n])\s*[!]?prose\s+(?:run|compile|help|update|examples)\b"
    ),
    re.compile(r"(?is)os\.(?:system|popen)\s*\(\s*['\"]prose\s+"),
)
NETWORK_TOOL_PATTERN = re.compile(
    r"(?i)(?:\bcurl\b|\bwget\b|requests\.(?:get|post|put|delete)\s*\(|"
    r"httpx\.|urllib\.request|socket\.(?:socket|create_connection))"
)
TELEMETRY_PATTERN = re.compile(
    r"(?i)(?:api-v2\.prose\.md/analytics|OPENPROSE_TELEMETRY\s*=\s*['\"]?enabled)"
)


class ConfigurationError(RuntimeError):
    """A frozen input or evidence invariant failed closed."""


@dataclass(frozen=True)
class SkillSnapshot:
    root: Path
    tree_sha256: str
    file_count: int
    total_bytes: int
    files: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ProcessObservation:
    exit_code: int | None
    timed_out: bool
    output_limit_exceeded: bool
    stopped_after_assistant_error: bool
    duration_ms: int
    stdout: bytes
    stderr: bytes
    stdout_bytes_observed: int
    stderr_bytes_observed: int
    leader_reaped: bool = True
    readers_settled: bool = True
    streams_closed: bool = True
    original_process_group_empty: bool | None = None
    detached_descendants_contained: bool = False


@dataclass(frozen=True)
class HarnessExecutionCustody:
    executable: Path
    launch_prefix: tuple[str, ...]
    evidence: dict[str, Any]


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ConfigurationError(f"not a regular file: {resolved}")
    return sha256_bytes(
        read_bounded_regular(resolved, MAXIMUM_SKILL_FILE_BYTES, "digest input")
    )


def read_bounded_regular(path: Path, maximum: int, label: str) -> bytes:
    """Read one stable regular file without following a final-component symlink."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ConfigurationError("this platform cannot provide no-follow file reads")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as error:
        raise ConfigurationError(f"cannot safely open {label}: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ConfigurationError(f"{label} is not a regular file: {path}")
        if before.st_size > maximum:
            raise ConfigurationError(f"{label} exceeds its byte limit: {path}")
        chunks: list[bytes] = []
        observed = 0
        while observed <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
        after = os.fstat(descriptor)
        if observed > maximum:
            raise ConfigurationError(f"{label} exceeds its byte limit: {path}")
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or observed != after.st_size
        ):
            raise ConfigurationError(f"{label} changed while read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path}: expected an object")
    return value


def _json_schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value)),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _resolve_local_schema_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ConfigurationError("evidence schema contains a non-local reference")
    current: Any = root
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise ConfigurationError("evidence schema contains an invalid reference")
        current = current[key]
    if not isinstance(current, dict):
        raise ConfigurationError("evidence schema reference is not an object")
    return current


def _validate_schema_value(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str):
            raise ConfigurationError("evidence schema reference must be a string")
        _validate_schema_value(
            value, _resolve_local_schema_ref(root, reference), root, path
        )
        return
    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, list) or not all(
            isinstance(item, str) for item in expected_types
        ):
            raise ConfigurationError("evidence schema contains an invalid type")
        if not any(_json_schema_type_matches(value, item) for item in expected_types):
            raise ConfigurationError(f"evidence schema violation at {path}: wrong type")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ConfigurationError(f"evidence schema violation at {path}: wrong constant")
    if "enum" in schema and not any(
        _json_equal(value, candidate) for candidate in schema["enum"]
    ):
        raise ConfigurationError(f"evidence schema violation at {path}: unknown value")
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ConfigurationError(f"evidence schema violation at {path}: too short")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ConfigurationError(f"evidence schema violation at {path}: too long")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise ConfigurationError(f"evidence schema violation at {path}: pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ConfigurationError(f"evidence schema violation at {path}: too small")
        if "maximum" in schema and value > schema["maximum"]:
            raise ConfigurationError(f"evidence schema violation at {path}: too large")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, root, f"{path}[{index}]")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(item, str) for item in required
        ):
            raise ConfigurationError("evidence schema contains invalid required fields")
        for name in required:
            if name not in value:
                raise ConfigurationError(
                    f"evidence schema violation at {path}: missing {name}"
                )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ConfigurationError("evidence schema properties must be an object")
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                child_schema = properties[name]
                if not isinstance(child_schema, dict):
                    raise ConfigurationError("evidence schema property is invalid")
                _validate_schema_value(item, child_schema, root, child_path)
            elif additional is False:
                raise ConfigurationError(
                    f"evidence schema violation at {path}: unknown field {name}"
                )
            elif isinstance(additional, dict):
                _validate_schema_value(item, additional, root, child_path)


def validate_against_evidence_schema(value: Any) -> None:
    schema = read_json(EVIDENCE_SCHEMA)
    _validate_schema_value(value, schema, schema)


def snapshot_skill_tree(root: Path) -> SkillSnapshot:
    if root.is_symlink():
        raise ConfigurationError("skill tree root must not be a symlink")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ConfigurationError("skill tree root must be a directory")
    digest = hashlib.sha256()
    files: list[dict[str, Any]] = []
    total = 0
    for path in sorted(resolved.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ConfigurationError(
                f"skill tree contains symlink: {path.relative_to(resolved)}"
            )
        if path.is_dir():
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationError(
                f"skill tree contains special file: {path.relative_to(resolved)}"
            )
        relative = path.relative_to(resolved).as_posix()
        body = read_bounded_regular(path, MAXIMUM_SKILL_FILE_BYTES, "skill file")
        if len(body) != metadata.st_size:
            raise ConfigurationError(f"skill file changed while read: {relative}")
        total += len(body)
        if total > MAXIMUM_SKILL_TREE_BYTES:
            raise ConfigurationError("skill tree exceeds its byte limit")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(body)).encode("ascii"))
        digest.update(b"\0")
        digest.update(body)
        digest.update(b"\0")
        files.append(
            {"path": relative, "byteLength": len(body), "sha256": sha256_bytes(body)}
        )
    return SkillSnapshot(resolved, digest.hexdigest(), len(files), total, tuple(files))


def _stable_tree_inventory(root: Path, label: str) -> list[tuple[str, os.stat_result]]:
    if root.is_symlink():
        raise ConfigurationError(f"{label} root must not be a symlink")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ConfigurationError(f"{label} root must be a directory")
    inventory: list[tuple[str, os.stat_result]] = []
    for path in sorted(resolved.rglob("*")):
        details = path.lstat()
        relative = path.relative_to(resolved).as_posix()
        if stat.S_ISLNK(details.st_mode):
            raise ConfigurationError(f"{label} contains a symlink: {relative}")
        if not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode)):
            raise ConfigurationError(f"{label} contains a special path: {relative}")
        inventory.append((relative, details))
    return inventory


def _inventory_identity(
    inventory: list[tuple[str, os.stat_result]],
) -> list[tuple[str, int, int, int, int, int]]:
    return [
        (
            relative,
            details.st_dev,
            details.st_ino,
            details.st_mode,
            details.st_size,
            details.st_mtime_ns,
        )
        for relative, details in inventory
    ]


def snapshot_tree_for_execution(
    source: Path, destination: Path, label: str
) -> SkillSnapshot:
    """Copy one stable, closed regular-file tree into owned execution custody."""

    source_root = source.resolve(strict=True)
    initial = _stable_tree_inventory(source, label)
    files = [item for item in initial if stat.S_ISREG(item[1].st_mode)]
    if len(files) > 4096:
        raise ConfigurationError(f"{label} exceeds its file-count limit")
    if destination.exists() or destination.is_symlink():
        raise ConfigurationError(f"{label} custody destination already exists")
    destination.mkdir(mode=0o700, parents=True)
    total = 0
    for relative, details in initial:
        target = destination / relative
        if stat.S_ISDIR(details.st_mode):
            target.mkdir(mode=0o700, parents=True, exist_ok=False)
            continue
        body = read_bounded_regular(
            source_root / relative, MAXIMUM_SKILL_FILE_BYTES, label
        )
        total += len(body)
        if total > MAXIMUM_SKILL_TREE_BYTES:
            raise ConfigurationError(f"{label} exceeds its total byte limit")
        atomic_write_bytes(target, body)
        target.chmod(details.st_mode & 0o777)
    final = _stable_tree_inventory(source_root, label)
    if _inventory_identity(initial) != _inventory_identity(final):
        raise ConfigurationError(f"{label} changed while entering execution custody")
    return snapshot_skill_tree(destination)


def _snapshot_interpreter(
    entry: Path, destination: Path
) -> tuple[tuple[str, ...], dict[str, Any] | None]:
    body = read_bounded_regular(entry, MAXIMUM_SKILL_FILE_BYTES, "harness entry")
    first_line = body.splitlines()[0] if body.startswith(b"#!") else b""
    if not first_line:
        return (), None
    try:
        declaration = shlex.split(first_line[2:].decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ConfigurationError(
            "unsupported harness interpreter declaration"
        ) from error
    if not declaration:
        raise ConfigurationError("empty harness interpreter declaration")
    if declaration[0] == "/usr/bin/env":
        if (
            len(declaration) != 2
            or re.fullmatch(r"[A-Za-z0-9._+-]+", declaration[1]) is None
        ):
            raise ConfigurationError("unsupported env-based harness interpreter")
        interpreter_name = declaration[1]
        resolved_text = shutil.which(interpreter_name, path=CONTROLLED_PATH)
        if resolved_text is None:
            raise ConfigurationError("harness interpreter is unavailable")
        interpreter_source = Path(resolved_text).resolve(strict=True)
    else:
        if len(declaration) != 1 or not Path(declaration[0]).is_absolute():
            raise ConfigurationError("unsupported harness interpreter declaration")
        interpreter_source = Path(declaration[0]).resolve(strict=True)
        interpreter_name = interpreter_source.name
    interpreter_body = read_bounded_regular(
        interpreter_source, MAXIMUM_SKILL_TREE_BYTES, "harness interpreter"
    )
    interpreter_copy = destination / "bin" / interpreter_name
    atomic_write_bytes(interpreter_copy, interpreter_body)
    interpreter_copy.chmod(0o700)
    support_files: list[dict[str, Any]] = []
    if sys.platform == "darwin" and interpreter_name == "node":
        candidates = sorted(
            (interpreter_source.parent.parent / "lib").glob("libnode*.dylib")
        )
        if len(candidates) != 1:
            raise ConfigurationError("cannot close the snapshotted Node launcher")
        support_body = read_bounded_regular(
            candidates[0],
            MAXIMUM_INTERPRETER_SUPPORT_BYTES,
            "Node interpreter support library",
        )
        support_copy = destination / "lib" / candidates[0].name
        atomic_write_bytes(support_copy, support_body)
        support_copy.chmod(0o500)
        support_files.append(
            {
                "path": f"lib/{candidates[0].name}",
                "sha256": sha256_bytes(support_body),
                "byteLength": len(support_body),
            }
        )
    if sha256_bytes(
        read_bounded_regular(
            interpreter_source, MAXIMUM_SKILL_TREE_BYTES, "harness interpreter"
        )
    ) != sha256_bytes(interpreter_body):
        raise ConfigurationError("harness interpreter changed during custody")
    return (str(interpreter_copy),), {
        "name": interpreter_name,
        "sha256": sha256_bytes(interpreter_body),
        "byteLength": len(interpreter_body),
        "snapshotted": True,
        "supportFiles": support_files,
    }


def prepare_harness_execution_custody(
    executable: Path, expected_sha256: str, destination: Path
) -> HarnessExecutionCustody:
    resolved = executable.resolve(strict=True)
    tree = snapshot_tree_for_execution(
        resolved.parent, destination / "tree", "harness dependency tree"
    )
    relative = resolved.relative_to(resolved.parent).as_posix()
    entry = tree.root / relative
    entry_sha256 = file_digest(entry)
    if entry_sha256 != expected_sha256:
        raise ConfigurationError("Prime executable drift")
    if not os.access(entry, os.X_OK):
        raise ConfigurationError("snapshotted Prime entry is not executable")
    prefix, interpreter = _snapshot_interpreter(
        entry, destination / "interpreter" / "runtime"
    )
    if interpreter is not None and interpreter["name"] == "node":
        package_root = resolved.parent.parent.parent
        external_modules = package_root / "node_modules"
        if not external_modules.is_dir() or external_modules.is_symlink():
            raise ConfigurationError("Prime Node dependency root is unavailable")
        package_json = read_bounded_regular(
            package_root / "package.json",
            MAXIMUM_SKILL_FILE_BYTES,
            "Prime package manifest",
        )
        atomic_write_bytes(tree.root / "package.json", package_json)
        tree = snapshot_skill_tree(tree.root)
        (tree.root / "node_modules").symlink_to(
            external_modules, target_is_directory=True
        )
    return HarnessExecutionCustody(
        entry,
        prefix,
        {
            "version": "snapshotted-launch-tree-v1",
            "entryRelativePath": relative,
            "entrySha256": entry_sha256,
            "treeSha256": tree.tree_sha256,
            "treeFileCount": tree.file_count,
            "treeTotalBytes": tree.total_bytes,
            "interpreter": interpreter,
            "exactSnapshottedBytesExecuted": True,
            "dynamicDependenciesClosed": False,
            "semanticAdmission": False,
        },
    )


def _closed_claims(value: Any) -> bool:
    return value == {
        "exploratoryOldSkillCompatibility": "observation-only",
        "interactiveTuiObserved": False,
        "currentSkillCompatibility": "unknown",
        "strictWrapperAdmission": False,
        "semanticConformance": "unknown",
        "proseComplete": "unknown",
        "releaseEligible": False,
    }


def validate_frozen_declarations(
    policy: dict[str, Any], matrix: dict[str, Any]
) -> None:
    if policy.get("schema") != "openprose.direct-skill-real-policy/1":
        raise ConfigurationError("unsupported policy schema")
    if matrix.get("schema") != "openprose.direct-skill-real-matrix/1":
        raise ConfigurationError("unsupported matrix schema")
    limits = policy.get("limits", {})
    if limits.get("retryCount") != 0:
        raise ConfigurationError("retryCount must remain zero")
    if limits.get("maximumTrialsPerCell") != 2:
        raise ConfigurationError("maximumTrialsPerCell must remain two")
    if limits.get("preferredSuccessfulTrialsPerCell") != 2:
        raise ConfigurationError("preferredSuccessfulTrialsPerCell must remain two")
    reported_cost_stop_threshold = limits.get("maximumTotalCostUsd")
    if (
        not isinstance(reported_cost_stop_threshold, (int, float))
        or isinstance(reported_cost_stop_threshold, bool)
        or not 0 < reported_cost_stop_threshold <= 2.0
    ):
        raise ConfigurationError(
            "harness-reported cost stop threshold must be in (0, 2]"
        )
    for name in (
        "timeoutSecondsPerTrial",
        "maximumCapturedProcessBytes",
        "maximumPersistedDiagnosticBytes",
        "maximumWorkspaceFiles",
        "maximumWorkspaceBytes",
        "maximumWorkspaceFileBytes",
        "maximumTotalTrials",
    ):
        if type(limits.get(name)) is not int or limits[name] <= 0:
            raise ConfigurationError(f"invalid positive limit: {name}")
    isolation = policy.get("isolation", {})
    for name in (
        "freshDisposableWorkingDirectory",
        "precreateTelemetryDisabledEnv",
        "controlledPathWithoutProse",
        "explicitSkillOnly",
    ):
        if isolation.get(name) is not True:
            raise ConfigurationError(f"required isolation is not frozen: {name}")
    for name in (
        "ambientSkills",
        "extensions",
        "contextFiles",
        "savedSession",
        "promptTemplates",
        "themes",
        "consequentialTools",
        "toolNetworkOtherThanProvider",
    ):
        if isolation.get(name) is not False:
            raise ConfigurationError(f"forbidden ambient capability enabled: {name}")
    if isolation.get("allowedBuiltinTools") != ["ipython"]:
        raise ConfigurationError("only the explicit ipython capability is allowed")
    if not _closed_claims(policy.get("claims")) or not _closed_claims(
        matrix.get("claims")
    ):
        raise ConfigurationError("claims must remain old-skill-only and non-admitting")

    skill = matrix.get("skill", {})
    if not isinstance(skill, dict):
        raise ConfigurationError("skill declaration must be an object")
    if re.fullmatch(r"[0-9a-f]{64}", str(skill.get("expectedTreeSha256"))) is None:
        raise ConfigurationError("invalid declared skill tree digest")
    for name in ("expectedFileCount", "expectedTotalBytes"):
        if type(skill.get(name)) is not int or skill[name] <= 0:
            raise ConfigurationError(f"invalid declared skill identity: {name}")
    relevant = skill.get("relevantFiles")
    if not isinstance(relevant, list) or not relevant:
        raise ConfigurationError("relevant skill files must be declared")
    for item in relevant:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "byteLength", "sha256"}
            or not isinstance(item["path"], str)
            or not item["path"]
            or type(item["byteLength"]) is not int
            or item["byteLength"] < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])) is None
        ):
            raise ConfigurationError("invalid relevant skill file declaration")
    if "treeRoot" in skill or "sourcePath" in skill:
        raise ConfigurationError("skill locations must be supplied explicitly")
    harness = matrix.get("harness")
    if not isinstance(harness, dict) or harness.get("name") != "prime-agent":
        raise ConfigurationError("invalid harness declaration")
    if "executable" in harness:
        raise ConfigurationError("harness location must be supplied explicitly")
    if (
        re.fullmatch(r"[0-9a-f]{64}", str(harness.get("expectedExecutableSha256")))
        is None
    ):
        raise ConfigurationError("invalid declared harness digest")
    retained_matrix_sha256 = matrix.get("retainedEvidenceMatrixSha256")
    if re.fullmatch(r"[0-9a-f]{64}", str(retained_matrix_sha256)) is None:
        raise ConfigurationError("invalid retained evidence matrix digest")
    if retained_matrix_sha256 == sha256_bytes(canonical_json(matrix)):
        raise ConfigurationError("retained evidence matrix digest must be historical")

    programs = matrix.get("programs")
    routes = matrix.get("routes")
    if not isinstance(programs, list) or len(programs) != 2:
        raise ConfigurationError("matrix must freeze exactly two programs")
    if not isinstance(routes, list) or len(routes) != 4:
        raise ConfigurationError("matrix must freeze exactly four routes")
    for program in programs:
        source = (HERE / program.get("path", "")).resolve(strict=True)
        if HERE not in source.parents or source.is_symlink() or not source.is_file():
            raise ConfigurationError("unsafe program path")
        if file_digest(source) != program.get("sha256"):
            raise ConfigurationError(f"program drift: {program.get('id')}")
        bindings = program.get("expectedBindings")
        if not isinstance(bindings, dict) or not bindings:
            raise ConfigurationError("expectedBindings must be non-empty")
        if type(program.get("minimumSubagentStarts")) is not int:
            raise ConfigurationError("minimumSubagentStarts must be an integer")
    ids: set[str] = set()
    planned = 0.0
    for route in routes:
        route_id = route.get("id")
        if not isinstance(route_id, str) or route_id in ids:
            raise ConfigurationError("route ids must be unique strings")
        ids.add(route_id)
        for name in ("provider", "model"):
            if not isinstance(route.get(name), str) or not route[name]:
                raise ConfigurationError(f"{route_id}: missing {name}")
        names = route.get("credentialEnvironment")
        if not isinstance(names, list) or any(
            re.fullmatch(r"[A-Z][A-Z0-9_]*", str(name)) is None for name in names
        ):
            raise ConfigurationError(f"{route_id}: invalid credential environment")
        ceiling = route.get("plannedTotalCostCeilingUsd")
        if not isinstance(ceiling, (int, float)) or ceiling <= 0:
            raise ConfigurationError(f"{route_id}: invalid planned budget")
        planned += float(ceiling)
    if abs(planned - float(reported_cost_stop_threshold)) > 1e-9:
        raise ConfigurationError(
            "route planned ceilings must equal the frozen reported-cost threshold"
        )
    projected = len(programs) * len(routes) * limits["maximumTrialsPerCell"]
    if projected > limits["maximumTotalTrials"]:
        raise ConfigurationError("trial matrix exceeds frozen trial budget")


def validate_skill_snapshot_identity(
    matrix: dict[str, Any], snapshot: SkillSnapshot
) -> None:
    skill = matrix["skill"]
    if snapshot.tree_sha256 != skill["expectedTreeSha256"]:
        raise ConfigurationError("skill tree drift")
    if snapshot.file_count != skill["expectedFileCount"]:
        raise ConfigurationError("skill tree file-count drift")
    if snapshot.total_bytes != skill["expectedTotalBytes"]:
        raise ConfigurationError("skill tree byte-count drift")
    observed_files = {item["path"]: item for item in snapshot.files}
    for expected in skill["relevantFiles"]:
        if observed_files.get(expected["path"]) != expected:
            raise ConfigurationError(f"relevant skill file drift: {expected['path']}")


def validate_live_skill_input(
    matrix: dict[str, Any], snapshot: SkillSnapshot, skill_root: Path
) -> None:
    validate_skill_snapshot_identity(matrix, snapshot)
    resolved_root = skill_root.resolve(strict=True)
    if snapshot.root != resolved_root:
        raise ConfigurationError("skill snapshot does not match explicit tree root")
    skill_path = resolved_root / "SKILL.md"
    if skill_path.is_symlink() or not skill_path.is_file():
        raise ConfigurationError("explicit skill root has no regular SKILL.md")


def validate_frozen_inputs(
    policy: dict[str, Any],
    matrix: dict[str, Any],
    snapshot: SkillSnapshot,
    skill_root: Path | None = None,
) -> None:
    validate_frozen_declarations(policy, matrix)
    validate_live_skill_input(matrix, snapshot, skill_root or snapshot.root)


def parse_dotenv_selected(path: Path | None, names: Iterable[str]) -> dict[str, str]:
    requested = set(names)
    if not requested:
        return {}
    if path is None:
        raise ConfigurationError(
            "selected routes require an explicit --env-file credential source"
        )
    if not path.is_absolute():
        raise ConfigurationError("--env-file must be an absolute path")
    if not path.is_file():
        raise ConfigurationError(f"credential file does not exist: {path}")
    found: dict[str, str] = {}
    for line in path.read_text("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        name, raw = stripped.split("=", 1)
        name = name.strip()
        if name not in requested:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or any(character in value for character in "\0\r\n"):
            raise ConfigurationError(f"{name}: unsupported credential encoding")
        found[name] = value
    missing = requested - found.keys()
    if missing:
        raise ConfigurationError(
            "missing requested credential variable(s): " + ", ".join(sorted(missing))
        )
    return found


def isolated_environment(
    credentials: dict[str, str], extra: dict[str, str] | None = None
) -> dict[str, str]:
    keep = (
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    environment = {name: os.environ[name] for name in keep if name in os.environ}
    environment.update(
        {
            "PATH": CONTROLLED_PATH,
            "NO_COLOR": "1",
            "CI": "1",
            "TERM": "dumb",
            "PYTHONUNBUFFERED": "1",
            "OPENPROSE_TELEMETRY": "disabled",
        }
    )
    environment.update(credentials)
    if extra:
        environment.update(extra)
    if shutil.which("prose", path=environment["PATH"]) is not None:
        raise ConfigurationError("controlled PATH unexpectedly resolves prose")
    return environment


def terminate_original_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate only the exact group/session created for this child."""

    if os.name == "nt":  # pragma: no cover
        if process.poll() is None:
            process.kill()
        return
    if process.pid == os.getpgrp():  # pragma: no cover - defensive invariant
        raise ConfigurationError("refusing to terminate the runner's process group")
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def wait_for_process(process: subprocess.Popen[bytes], timeout: float) -> int:
    """Small test seam around the leader wait."""

    return process.wait(timeout=timeout)


def _original_process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError as error:  # pragma: no cover
        raise ConfigurationError(
            "cannot verify original process-group cleanup"
        ) from error
    return True


def settle_original_process_group(
    process: subprocess.Popen[bytes],
    threads: tuple[threading.Thread, ...],
    streams: tuple[Any, Any],
    closing: threading.Event,
    deadline: float,
    *,
    terminate: bool,
) -> tuple[bool, bool, bool, bool | None]:
    """Settle the leader, readers, pipes, and original group by one deadline."""

    if terminate:
        terminate_original_process_group(process)
    remaining = max(0.001, deadline - time.monotonic())
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        terminate_original_process_group(process)
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as final_error:
            raise ConfigurationError(
                "harness process leader did not settle"
            ) from final_error
        raise ConfigurationError(
            "harness process exceeded its absolute deadline"
        ) from error
    leader_reaped = process.poll() is not None

    drain_deadline = min(deadline, time.monotonic() + 0.05)
    for thread in threads:
        thread.join(timeout=max(0.0, drain_deadline - time.monotonic()))
    closing.set()
    for stream in streams:
        if not stream.closed:
            stream.close()
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    readers_settled = all(not thread.is_alive() for thread in threads)
    streams_closed = all(stream.closed for stream in streams)
    if not readers_settled or not streams_closed:
        raise ConfigurationError("harness output readers did not settle by deadline")

    if os.name == "nt":  # pragma: no cover
        return leader_reaped, readers_settled, streams_closed, None
    while _original_process_group_exists(process.pid):
        if time.monotonic() >= deadline:
            raise ConfigurationError("original harness process group did not settle")
        time.sleep(0.005)
    return leader_reaped, readers_settled, streams_closed, True


def assistant_failure_line(line: bytes) -> bool:
    try:
        event = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(event, dict) or event.get("type") != "message_end":
        return False
    message = event.get("message")
    return bool(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and (message.get("stopReason") == "error" or message.get("errorMessage"))
    )


def run_bounded(
    argv: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    maximum_output_bytes: int,
) -> ProcessObservation:
    started = time.monotonic()
    deadline = started + float(timeout_seconds)
    cleanup_reserve = min(0.5, max(0.05, float(timeout_seconds) * 0.2))
    process_deadline = deadline - cleanup_reserve
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name != "nt"),
    )
    if os.name != "nt" and process.pid == os.getpgrp():  # pragma: no cover
        process.kill()
        process.wait()
        raise ConfigurationError("refusing to supervise the runner's process group")
    lock = threading.Lock()
    closing = threading.Event()
    captured_total = 0
    observed = {"stdout": 0, "stderr": 0}
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    output_limit = False
    stopped_after_error = False
    reader_failed = False
    termination_requested = False

    def request_termination() -> None:
        nonlocal termination_requested
        with lock:
            if termination_requested:
                return
            termination_requested = True
        terminate_original_process_group(process)

    def consume(name: str, stream: Any, inspect: bool) -> None:
        nonlocal captured_total, output_limit, stopped_after_error, reader_failed
        pending = bytearray()
        try:
            descriptor = stream.fileno()
            while True:
                if os.name != "nt":
                    readable, _, _ = select.select([descriptor], [], [], 0.05)
                    if closing.is_set():
                        break
                    if not readable:
                        continue
                chunk = os.read(descriptor, 4096)
                if not chunk:
                    break
                should_terminate = False
                with lock:
                    observed[name] += len(chunk)
                    remaining = max(0, maximum_output_bytes - captured_total)
                    kept = chunk[:remaining]
                    captured[name].extend(kept)
                    captured_total += len(kept)
                    if len(chunk) > remaining and not output_limit:
                        output_limit = True
                        should_terminate = True
                if should_terminate:
                    request_termination()
                if inspect and not stopped_after_error and kept:
                    pending.extend(kept)
                    while b"\n" in pending:
                        line, _, rest = pending.partition(b"\n")
                        pending = bytearray(rest)
                        if assistant_failure_line(bytes(line)):
                            with lock:
                                if not stopped_after_error:
                                    stopped_after_error = True
                                    should_terminate = True
                            if should_terminate:
                                request_termination()
                            return
                    if len(pending) > min(65536, maximum_output_bytes):
                        pending.clear()
        except (OSError, ValueError):
            if not closing.is_set():
                with lock:
                    reader_failed = True
                request_termination()

    assert process.stdout is not None and process.stderr is not None
    streams = (process.stdout, process.stderr)
    threads = (
        threading.Thread(
            target=consume,
            args=("stdout", process.stdout, True),
            name="direct-skill-real-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=consume,
            args=("stderr", process.stderr, False),
            name="direct-skill-real-stderr",
            daemon=True,
        ),
    )
    started_threads: list[threading.Thread] = []
    timed_out = False
    settlement: tuple[bool, bool, bool, bool | None] | None = None
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
        try:
            wait_for_process(process, max(0.001, process_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            request_termination()
        settlement = settle_original_process_group(
            process,
            tuple(started_threads),
            streams,
            closing,
            deadline,
            terminate=not termination_requested,
        )
        if reader_failed:
            raise ConfigurationError("failed to capture harness output safely")
    except BaseException:
        if settlement is None:
            try:
                request_termination()
                settle_original_process_group(
                    process,
                    tuple(started_threads),
                    streams,
                    closing,
                    deadline,
                    terminate=False,
                )
            except Exception as cleanup_error:
                raise ConfigurationError(
                    "failed to settle interrupted harness process"
                ) from cleanup_error
        raise
    leader_reaped, readers_settled, streams_closed, group_empty = settlement
    return ProcessObservation(
        None if timed_out else process.returncode,
        timed_out,
        output_limit,
        stopped_after_error,
        int((time.monotonic() - started) * 1000),
        bytes(captured["stdout"]),
        bytes(captured["stderr"]),
        observed["stdout"],
        observed["stderr"],
        leader_reaped,
        readers_settled,
        streams_closed,
        group_empty,
        False,
    )


def observe_version(
    executable: Path,
    environment: dict[str, str],
    launch_prefix: tuple[str, ...] = (),
) -> str:
    with tempfile.TemporaryDirectory(prefix="openprose-direct-skill-version-") as raw:
        result = run_bounded(
            [*launch_prefix, str(executable), "--version"],
            Path(raw),
            environment,
            10,
            65536,
        )
    if result.timed_out or result.output_limit_exceeded or result.exit_code != 0:
        raise ConfigurationError("Prime version probe failed")
    value = result.stdout.decode("utf-8", errors="replace").strip()
    if not value:
        value = result.stderr.decode("utf-8", errors="replace").strip()
    return value.removeprefix("prime-agent ").strip()


def build_user_message(program: dict[str, Any]) -> str:
    return (
        "prose run program.prose\n\n"
        "Evaluation constraints: use the explicitly loaded OpenProse skill directly. "
        "Do not invoke or search for a prose executable. Work only inside the current "
        "disposable directory. Do not access the network except through the selected "
        "model provider, and do not send telemetry. Use filesystem state and execute "
        f"the complete {program['id']} program."
    )


def task_digest(program: dict[str, Any], skill_snapshot: SkillSnapshot) -> str:
    return task_digest_for_tree(program, skill_snapshot.tree_sha256)


def task_digest_for_tree(program: dict[str, Any], tree_sha256: str) -> str:
    return sha256_bytes(
        canonical_json(
            {
                "generator": "direct-skill-prose-run/1",
                "programId": program["id"],
                "programSha256": program["sha256"],
                "skillTreeSha256": tree_sha256,
                "userMessage": build_user_message(program),
            }
        )
    )


def build_command(
    executable: Path,
    route: dict[str, Any],
    matrix: dict[str, Any],
    program: dict[str, Any],
    cwd: Path,
    *,
    launch_prefix: tuple[str, ...] = (),
    skill_source_path: Path | None = None,
) -> list[str]:
    if skill_source_path is None:
        raise ConfigurationError("an explicit snapshotted skill path is required")
    return [
        *launch_prefix,
        str(executable),
        "--print",
        "--mode",
        "json",
        "--cwd",
        str(cwd),
        "--offline",
        "--provider",
        route["provider"],
        "--model",
        route["model"],
        "--thinking",
        "off",
        "--no-session",
        "--no-builtin-tools",
        "--tools",
        "ipython",
        "--no-extensions",
        "--no-skills",
        "--skill",
        str(skill_source_path),
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--",
        build_user_message(program),
    ]


def parse_json_events(stdout: bytes) -> list[Any]:
    events: list[Any] = []
    text = stdout.decode("utf-8", errors="replace")
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def terminal_assistant_attempts(events: Iterable[Any]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            attempts.append(message)
    return attempts


def observed_route(events: list[Any]) -> dict[str, Any] | None:
    attempts = terminal_assistant_attempts(events)
    reported = [
        attempt
        for attempt in attempts
        if isinstance(attempt.get("provider"), str)
        and isinstance(attempt.get("model"), str)
    ]
    if not reported:
        return None
    last = reported[-1]
    return {
        "provider": last["provider"],
        "model": last["model"],
        "api": last.get("api") if isinstance(last.get("api"), str) else None,
    }


def failure_diagnostic(events: list[Any]) -> str | None:
    for attempt in reversed(terminal_assistant_attempts(events)):
        message = attempt.get("errorMessage")
        if isinstance(message, str) and message:
            return message
    return None


def observed_usage(events: list[Any]) -> dict[str, Any] | None:
    usages = [
        attempt["usage"]
        for attempt in terminal_assistant_attempts(events)
        if isinstance(attempt.get("usage"), dict)
    ]
    if not usages:
        return None
    names = ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
    return {
        "source": "harness-reported-message-usage",
        "authoritativeForBilling": False,
        "attempts": len(usages),
        "totals": {
            name: sum(
                float(usage.get(name, 0))
                for usage in usages
                if isinstance(usage.get(name, 0), (int, float))
                and not isinstance(usage.get(name, 0), bool)
            )
            for name in names
        },
    }


def observed_cost(events: list[Any]) -> dict[str, Any] | None:
    totals: list[float] = []
    for attempt in terminal_assistant_attempts(events):
        usage = attempt.get("usage")
        cost = usage.get("cost") if isinstance(usage, dict) else None
        total = cost.get("total") if isinstance(cost, dict) else None
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            totals.append(float(total))
    if not totals:
        return None
    return {
        "currency": "USD",
        "source": "harness-reported-message-usage",
        "authoritativeForBilling": False,
        "attempts": len(totals),
        "totalUsd": sum(totals),
    }


def tool_audit(events: list[Any]) -> dict[str, Any]:
    names: Counter[str] = Counter()
    tool_texts: list[str] = []
    explicit_subagents = 0
    seen_calls: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", "")).lower()
        name = event.get("toolName") or event.get("tool_name")
        if event_type == "tool_execution_start" and isinstance(name, str):
            call_id = event.get("toolCallId")
            identity = str(call_id) if isinstance(call_id, str) else f"event:{index}"
            if identity in seen_calls:
                continue
            seen_calls.add(identity)
            names[name] += 1
            args = event.get("args")
            if args is None:
                args = event.get("arguments")
            if args is None:
                args = event.get("input")
            if args is not None:
                tool_texts.append(json.dumps(args, ensure_ascii=False))
        if "subagent" in event_type and any(
            marker in event_type for marker in ("start", "spawn", "created")
        ):
            explicit_subagents += 1
    combined = "\n".join(tool_texts)
    rlm_calls = len(re.findall(r"\brlm\s*\.\s*run\s*\(", combined))
    prose = any(pattern.search(combined) for pattern in PROSE_INVOCATION_PATTERNS)
    telemetry = TELEMETRY_PATTERN.search(combined) is not None
    network = NETWORK_TOOL_PATTERN.search(combined) is not None
    consequential = (
        re.search(
            r"(?im)(?:^|[;\n])\s*(?:!|%\w+\s+)?(?:rm\s+-|git\s+(?:push|commit|reset)|sudo\b)",
            combined,
        )
        is not None
    )
    return {
        "toolNames": dict(sorted(names.items())),
        "ipythonCallsObserved": names.get("ipython", 0),
        "directSubagentStartEvents": explicit_subagents,
        "rlmRunCallsObserved": rlm_calls,
        "subagentStartsObserved": explicit_subagents,
        "proseInvocationObserved": prose,
        "legacyTelemetryAttemptObserved": telemetry,
        "nonProviderNetworkToolObserved": network,
        "consequentialToolObserved": consequential,
        "toolArgumentsRetained": False,
    }


def _read_workspace_file(path: Path, maximum: int) -> bytes:
    return read_bounded_regular(path, maximum, "workspace file")


def audit_workspace(
    workspace: Path,
    program: dict[str, Any],
    limits: dict[str, Any],
    direct_subagents: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    bodies: dict[str, bytes] = {}
    total = 0
    for directory, names, filenames in os.walk(workspace, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            target = directory_path / name
            if target.is_symlink():
                relative = target.relative_to(workspace)
                raise ConfigurationError(
                    f"workspace contains symlink directory: {relative}"
                )
        for name in filenames:
            target = directory_path / name
            relative = target.relative_to(workspace).as_posix()
            body = _read_workspace_file(target, limits["maximumWorkspaceFileBytes"])
            total += len(body)
            if total > limits["maximumWorkspaceBytes"]:
                raise ConfigurationError("workspace exceeds total byte limit")
            bodies[relative] = body
            records.append(
                {
                    "path": relative,
                    "byteLength": len(body),
                    "sha256": sha256_bytes(body),
                }
            )
            if len(records) > limits["maximumWorkspaceFiles"]:
                raise ConfigurationError("workspace exceeds file-count limit")
    records.sort(key=lambda item: item["path"])
    telemetry = bodies.get(".prose/.env")
    source = bodies.get("program.prose")
    run_roots = sorted(
        {
            path.split("/")[2]
            for path in bodies
            if path.startswith(".prose/runs/") and len(path.split("/")) >= 4
        }
    )
    bindings: dict[str, Any] = {}
    for name, token in program["expectedBindings"].items():
        candidates = [
            (path, body)
            for path, body in bodies.items()
            if path.startswith(".prose/runs/") and path.endswith(f"/bindings/{name}.md")
        ]
        token_bytes = token.encode("utf-8")
        bindings[name] = {
            "candidateCount": len(candidates),
            "expectedTokenObserved": len(candidates) == 1
            and token_bytes in candidates[0][1],
            "path": candidates[0][0] if len(candidates) == 1 else None,
            "byteLength": len(candidates[0][1]) if len(candidates) == 1 else None,
            "sha256": sha256_bytes(candidates[0][1]) if len(candidates) == 1 else None,
            "bodyRetained": False,
        }
    program_copies = [
        body
        for path, body in bodies.items()
        if path.startswith(".prose/runs/") and path.endswith("/program.prose")
    ]
    state_files = [
        path
        for path in bodies
        if path.startswith(".prose/runs/") and path.endswith("/state.md")
    ]
    unexpected = [
        path
        for path in bodies
        if path != "program.prose" and not path.startswith(".prose/")
    ]
    binding_effect_count = sum(
        item["expectedTokenObserved"] for item in bindings.values()
    )
    filesystem_effects = bool(
        telemetry == TELEMETRY_DISABLED_BYTES
        and source is not None
        and sha256_bytes(source) == program["sha256"]
        and len(run_roots) == 1
        and len(program_copies) == 1
        and sha256_bytes(program_copies[0]) == program["sha256"]
        and len(state_files) == 1
        and all(item["expectedTokenObserved"] for item in bindings.values())
        and not unexpected
    )
    subagent_proven = False
    return {
        "fileCount": len(records),
        "totalBytes": total,
        "files": records,
        "fileBodiesRetained": False,
        "telemetryDisabledEnvUnchanged": telemetry == TELEMETRY_DISABLED_BYTES,
        "rootProgramDigestExact": source is not None
        and sha256_bytes(source) == program["sha256"],
        "runDirectoryCount": len(run_roots),
        "programCopyDigestExact": len(program_copies) == 1
        and sha256_bytes(program_copies[0]) == program["sha256"],
        "stateFileCount": len(state_files),
        "bindings": bindings,
        "bindingEffectCount": binding_effect_count,
        "subagentExecutionEvidenceCount": direct_subagents,
        "subagentExecutionProven": subagent_proven,
        "unexpectedEffectPaths": unexpected,
        "effectsPassed": filesystem_effects,
    }


def bounded_sanitize(
    data: bytes,
    *,
    prompt: str,
    secrets: Iterable[str],
    workspace: Path,
    maximum_bytes: int,
    private_paths: Iterable[Path | None] = (),
) -> str:
    text = data.decode("utf-8", errors="replace")
    for variant in (prompt, json.dumps(prompt, ensure_ascii=False)[1:-1]):
        text = text.replace(variant, "[REDACTED_PROMPT]")
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED_SECRET]")
    text = text.replace(str(workspace), "[DISPOSABLE_CWD]")
    path_values = sorted(
        {str(path) for path in private_paths if path is not None},
        key=len,
        reverse=True,
    )
    for path_value in path_values:
        for variant in (path_value, json.dumps(path_value)[1:-1]):
            text = text.replace(variant, "[PRIVATE_PATH]")
    home = os.environ.get("HOME")
    if home:
        text = text.replace(home, "$HOME")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return text
    marker = b"\n...[TRUNCATED]...\n"
    if maximum_bytes <= len(marker):
        return marker[:maximum_bytes].decode("utf-8", errors="ignore")
    side = max(0, (maximum_bytes - len(marker)) // 2)
    return (encoded[:side] + marker + encoded[-side:]).decode("utf-8", errors="ignore")


def classify(
    process: ProcessObservation,
    workspace: dict[str, Any] | None,
    audit: dict[str, Any],
    route_matches: bool,
) -> str:
    if process.timed_out:
        return "timeout"
    if process.output_limit_exceeded:
        return "output-limit"
    if any(
        audit[name]
        for name in (
            "proseInvocationObserved",
            "legacyTelemetryAttemptObserved",
            "nonProviderNetworkToolObserved",
            "consequentialToolObserved",
        )
    ):
        return "policy-violation"
    if process.exit_code != 0 or process.stopped_after_assistant_error:
        return "harness-unavailable"
    if not route_matches:
        return "harness-route-mismatch"
    if workspace is not None and workspace["effectsPassed"]:
        if not workspace.get("subagentExecutionProven", False):
            return "filesystem-effect-pass-subagent-unproven"
        return "old-skill-effect-pass"
    return "old-skill-effect-fail"


def run_trial(
    *,
    executable: Path,
    executable_sha256: str,
    observed_version: str,
    skill_snapshot: SkillSnapshot,
    policy: dict[str, Any],
    matrix: dict[str, Any],
    route: dict[str, Any],
    program: dict[str, Any],
    trial: int,
    env_file: Path | None,
    credential_values: dict[str, str] | None = None,
    extra_environment: dict[str, str] | None = None,
    executable_prefix: tuple[str, ...] = (),
    skill_source_path: Path | None = None,
    execution_custody: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limits = policy["limits"]
    credential_names = set(route["credentialEnvironment"])
    if credential_values is None:
        credentials = parse_dotenv_selected(env_file, credential_names)
    else:
        missing = credential_names - credential_values.keys()
        if missing:
            raise ConfigurationError(
                "missing requested credential variable(s): "
                + ", ".join(sorted(missing))
            )
        credentials = {name: credential_values[name] for name in credential_names}
    environment = isolated_environment(credentials, extra_environment)
    prompt = build_user_message(program)
    live_skill_source = skill_source_path or (skill_snapshot.root / "SKILL.md")
    private_paths = (executable, live_skill_source, env_file)
    with tempfile.TemporaryDirectory(
        prefix=f"openprose-direct-{route['id']}-{program['id']}-{trial}-"
    ) as raw:
        workspace_path = Path(raw).resolve()
        prose_dir = workspace_path / ".prose"
        prose_dir.mkdir(mode=0o700)
        telemetry_path = prose_dir / ".env"
        telemetry_path.write_bytes(TELEMETRY_DISABLED_BYTES)
        telemetry_path.chmod(0o600)
        program_bytes = (HERE / program["path"]).read_bytes()
        (workspace_path / "program.prose").write_bytes(program_bytes)
        process = run_bounded(
            build_command(
                executable,
                route,
                matrix,
                program,
                workspace_path,
                launch_prefix=executable_prefix,
                skill_source_path=live_skill_source,
            ),
            workspace_path,
            environment,
            limits["timeoutSecondsPerTrial"],
            limits["maximumCapturedProcessBytes"],
        )
        events = parse_json_events(process.stdout)
        audit = tool_audit(events)
        reported_route = observed_route(events)
        route_matches = bool(
            reported_route is not None
            and reported_route["provider"] == route["provider"]
            and reported_route["model"] == route["model"]
        )
        workspace: dict[str, Any] | None
        try:
            workspace = audit_workspace(
                workspace_path,
                program,
                limits,
                audit["subagentStartsObserved"],
            )
        except ConfigurationError as error:
            workspace = {
                "effectsPassed": False,
                "auditError": bounded_sanitize(
                    str(error).encode("utf-8"),
                    prompt=prompt,
                    secrets=credentials.values(),
                    workspace=workspace_path,
                    maximum_bytes=limits["maximumPersistedDiagnosticBytes"],
                    private_paths=private_paths,
                ),
                "fileBodiesRetained": False,
            }
        outcome = classify(process, workspace, audit, route_matches)
        sanitized_stderr = bounded_sanitize(
            process.stderr,
            prompt=prompt,
            secrets=credentials.values(),
            workspace=workspace_path,
            maximum_bytes=limits["maximumPersistedDiagnosticBytes"],
            private_paths=private_paths,
        )
        error = failure_diagnostic(events)
        sanitized_error = (
            bounded_sanitize(
                error.encode("utf-8"),
                prompt=prompt,
                secrets=credentials.values(),
                workspace=workspace_path,
                maximum_bytes=limits["maximumPersistedDiagnosticBytes"],
                private_paths=private_paths,
            )
            if error is not None
            else None
        )
    attempts = terminal_assistant_attempts(events)
    harness_evidence = {
        "name": matrix["harness"]["name"],
        "observedVersion": observed_version,
        "executableSha256": executable_sha256,
    }
    if execution_custody is not None:
        harness_evidence["executionCustody"] = execution_custody
    return {
        "schema": SCHEMA,
        "matrixId": matrix["id"],
        "matrixSha256": sha256_bytes(canonical_json(matrix)),
        "policyId": policy["id"],
        "policySha256": sha256_bytes(canonical_json(policy)),
        "route": {
            "id": route["id"],
            "provider": route["provider"],
            "model": route["model"],
        },
        "program": {
            "id": program["id"],
            "sha256": program["sha256"],
            "trial": trial,
        },
        "harness": harness_evidence,
        "skill": {
            "classification": matrix["skill"]["classification"],
            "treeSha256": skill_snapshot.tree_sha256,
            "fileCount": skill_snapshot.file_count,
            "totalBytes": skill_snapshot.total_bytes,
            "relevantFiles": matrix["skill"]["relevantFiles"],
        },
        "taskSha256": task_digest(program, skill_snapshot),
        "isolation": {
            "freshDisposableWorkingDirectory": True,
            "workspaceRetained": False,
            "telemetryDisabledBeforeSpawn": True,
            "controlledPathSha256": sha256_bytes(CONTROLLED_PATH.encode("utf-8")),
            "proseAbsentFromPath": shutil.which("prose", path=CONTROLLED_PATH) is None,
            "explicitSkillFlag": True,
            "ambientSkillsDisabled": True,
            "extensionsDisabled": True,
            "contextFilesDisabled": True,
            "savedSessionDisabled": True,
            "promptTemplatesDisabled": True,
            "themesDisabled": True,
            "allowedBuiltinTools": ["ipython"],
        },
        "process": {
            "exitCode": process.exit_code,
            "timedOut": process.timed_out,
            "outputLimitExceeded": process.output_limit_exceeded,
            "stoppedAfterFirstAssistantError": process.stopped_after_assistant_error,
            "durationMs": process.duration_ms,
            "stdoutBytesObserved": process.stdout_bytes_observed,
            "stderrBytesObserved": process.stderr_bytes_observed,
            "capturedStdoutSha256": sha256_bytes(process.stdout),
            "capturedStderrSha256": sha256_bytes(process.stderr),
            "rawStdoutRetained": False,
            "sanitizedStderr": sanitized_stderr,
            "settlement": {
                "enforcementVersion": "bounded-original-group-v1",
                "scope": (
                    "original-posix-process-group"
                    if os.name != "nt"
                    else "direct-process-only"
                ),
                "leaderReaped": process.leader_reaped,
                "readersSettled": process.readers_settled,
                "streamsClosed": process.streams_closed,
                "originalProcessGroupEmpty": process.original_process_group_empty,
                "detachedDescendantsContained": (
                    process.detached_descendants_contained
                ),
                "strictContainmentClaimed": False,
            },
        },
        "toolAudit": audit,
        "workspace": workspace,
        "observation": {
            "classification": outcome,
            "collectorClassification": outcome,
            "analyzerVersion": ANALYZER_VERSION,
            "jsonEventsParsed": len(events),
            "assistantTerminalAttempts": len(attempts),
            "usage": observed_usage(events),
            "cost": observed_cost(events),
            "costAuthoritativeForBilling": False,
            "reportedRoute": reported_route,
            "requestedRouteReportedExactly": route_matches,
            "sanitizedFailureDiagnostic": sanitized_error,
        },
        "claims": dict(matrix["claims"]),
    }


def reanalyze_evidence(value: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    """Conservatively re-derive claims from retained metadata, never transcripts."""

    program_id = value.get("program", {}).get("id")
    programs = {
        program["id"]: program
        for program in matrix["programs"]
        if isinstance(program, dict) and isinstance(program.get("id"), str)
    }
    if program_id not in programs:
        raise ConfigurationError(f"evidence has unknown program: {program_id}")
    program = programs[program_id]
    workspace = value.get("workspace", {})
    audit = value.get("toolAudit", {})
    audit["subagentStartsObserved"] = audit.get("directSubagentStartEvents", 0)
    bindings = workspace.get("bindings", {})
    expected_names = set(program["expectedBindings"])
    filesystem_effects = bool(
        workspace.get("telemetryDisabledEnvUnchanged") is True
        and workspace.get("rootProgramDigestExact") is True
        and workspace.get("runDirectoryCount") == 1
        and workspace.get("programCopyDigestExact") is True
        and workspace.get("stateFileCount") == 1
        and isinstance(bindings, dict)
        and set(bindings) == expected_names
        and all(
            isinstance(bindings[name], dict)
            and bindings[name].get("expectedTokenObserved") is True
            for name in expected_names
        )
        and workspace.get("unexpectedEffectPaths") == []
    )
    direct_subagents = audit.get("subagentStartsObserved", 0)
    if type(direct_subagents) is not int or direct_subagents < 0:
        raise ConfigurationError("invalid retained subagent count")
    subagent_proven = False
    workspace["effectsPassed"] = filesystem_effects
    workspace["subagentExecutionEvidenceCount"] = direct_subagents
    workspace["subagentExecutionProven"] = subagent_proven

    process = value.get("process", {})
    route_matches = (
        value.get("observation", {}).get("requestedRouteReportedExactly") is True
    )
    observation = value.setdefault("observation", {})
    observation.setdefault("collectorClassification", observation.get("classification"))
    historical_fail_closed = (
        observation.get("analyzerVersion") == HISTORICAL_ANALYZER_VERSION
        and workspace.get("subagentExecutionProven") is False
        and observation.get("classification") != "old-skill-effect-pass"
    )
    observation["analyzerVersion"] = (
        HISTORICAL_ANALYZER_VERSION if historical_fail_closed else ANALYZER_VERSION
    )
    observation["classification"] = classify(
        ProcessObservation(
            process.get("exitCode"),
            process.get("timedOut") is True,
            process.get("outputLimitExceeded") is True,
            process.get("stoppedAfterFirstAssistantError") is True,
            int(process.get("durationMs", 0)),
            b"",
            b"",
            int(process.get("stdoutBytesObserved", 0)),
            int(process.get("stderrBytesObserved", 0)),
        ),
        workspace,
        audit,
        route_matches,
    )
    return value


def validate_analyzer_owned_fields(
    value: dict[str, Any], matrix: dict[str, Any]
) -> None:
    """Require retained derived facts to equal a fresh, non-mutating analysis."""

    recomputed = reanalyze_evidence(deepcopy(value), matrix)
    drift = [
        f"{section}.{field}"
        for section, field in ANALYZER_OWNED_FIELDS
        if canonical_json(value[section][field])
        != canonical_json(recomputed[section][field])
    ]
    if drift:
        raise ConfigurationError(
            "evidence analyzer-owned derived fields drift: " + ", ".join(drift)
        )


def validate_evidence(value: dict[str, Any]) -> None:
    validate_against_evidence_schema(value)
    if value.get("schema") != SCHEMA:
        raise ConfigurationError("unsupported evidence schema")
    for name in ("matrixSha256", "policySha256", "taskSha256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(value.get(name))) is None:
            raise ConfigurationError(f"invalid {name}")
    if not _closed_claims(value.get("claims")):
        raise ConfigurationError("evidence contains an inadmissible claim")
    isolation = value.get("isolation", {})
    if isolation.get("proseAbsentFromPath") is not True:
        raise ConfigurationError("evidence path resolved a prose executable")
    if isolation.get("explicitSkillFlag") is not True:
        raise ConfigurationError("evidence did not use the explicit skill")
    process = value.get("process", {})
    if process.get("rawStdoutRetained") is not False:
        raise ConfigurationError("raw transcript retention is forbidden")
    settlement = process.get("settlement")
    if settlement is not None:
        if settlement["enforcementVersion"] != "bounded-original-group-v1":
            raise ConfigurationError("unsupported process settlement evidence")
        if (
            settlement["leaderReaped"] is not True
            or settlement["readersSettled"] is not True
            or settlement["streamsClosed"] is not True
        ):
            raise ConfigurationError("bounded process settlement is incomplete")
        expected_group = (
            True if settlement["scope"] == "original-posix-process-group" else None
        )
        if settlement["originalProcessGroupEmpty"] is not expected_group:
            raise ConfigurationError("bounded process-group settlement is inconsistent")
    audit = value.get("toolAudit", {})
    if audit.get("toolArgumentsRetained") is not False:
        raise ConfigurationError("tool arguments must not be retained")
    if value.get("workspace", {}).get("fileBodiesRetained") is not False:
        raise ConfigurationError("workspace file bodies must not be retained")
    observation = value.get("observation", {})
    classification = observation.get("classification")
    if classification == "old-skill-effect-pass":
        if observation.get("requestedRouteReportedExactly") is not True:
            raise ConfigurationError("passing evidence did not report the exact route")
        if value.get("workspace", {}).get("effectsPassed") is not True:
            raise ConfigurationError("passing evidence has no exact workspace effects")
        if value.get("workspace", {}).get("subagentExecutionProven") is not True:
            raise ConfigurationError("passing evidence has no direct subagent proof")
        if any(
            audit.get(name) is not False
            for name in (
                "proseInvocationObserved",
                "legacyTelemetryAttemptObserved",
                "nonProviderNetworkToolObserved",
                "consequentialToolObserved",
            )
        ):
            raise ConfigurationError("passing evidence contains a policy violation")
    if classification == "filesystem-effect-pass-subagent-unproven":
        if value.get("workspace", {}).get("effectsPassed") is not True:
            raise ConfigurationError("filesystem-effect pass has no exact effects")
        if value.get("workspace", {}).get("subagentExecutionProven") is not False:
            raise ConfigurationError("unproven classification has subagent proof")
    serialized = json.dumps(value, ensure_ascii=False)
    if "prose run program.prose" in serialized:
        raise ConfigurationError("evidence retained the user message")


def validate_evidence_against_frozen_declarations(
    value: dict[str, Any],
    policy: dict[str, Any],
    matrix: dict[str, Any],
) -> None:
    validate_evidence(value)
    admitted_matrix_digests = {
        sha256_bytes(canonical_json(matrix)),
        matrix["retainedEvidenceMatrixSha256"],
    }
    if (
        value.get("matrixId") != matrix["id"]
        or value.get("matrixSha256") not in admitted_matrix_digests
    ):
        raise ConfigurationError("evidence matrix identity drift")
    if value.get("policyId") != policy["id"] or value.get(
        "policySha256"
    ) != sha256_bytes(canonical_json(policy)):
        raise ConfigurationError("evidence policy identity drift")
    route_id = value.get("route", {}).get("id")
    routes = {route["id"]: route for route in matrix["routes"]}
    if route_id not in routes:
        raise ConfigurationError("evidence route is outside the frozen matrix")
    route = routes[route_id]
    if value["route"] != {
        "id": route["id"],
        "provider": route["provider"],
        "model": route["model"],
    }:
        raise ConfigurationError("evidence requested route drift")
    program_id = value.get("program", {}).get("id")
    programs = {program["id"]: program for program in matrix["programs"]}
    if program_id not in programs:
        raise ConfigurationError("evidence program is outside the frozen matrix")
    program = programs[program_id]
    if value["program"].get("sha256") != program["sha256"]:
        raise ConfigurationError("evidence program digest drift")
    trial = value["program"].get("trial")
    if (
        type(trial) is not int
        or trial < 1
        or trial > policy["limits"]["maximumTrialsPerCell"]
    ):
        raise ConfigurationError("evidence trial is outside the frozen limit")
    if value.get("taskSha256") != task_digest_for_tree(
        program, matrix["skill"]["expectedTreeSha256"]
    ):
        raise ConfigurationError("evidence task digest drift")
    harness = value.get("harness", {})
    if {
        name: harness.get(name)
        for name in ("name", "observedVersion", "executableSha256")
    } != {
        "name": matrix["harness"]["name"],
        "observedVersion": matrix["harness"]["expectedObservedVersion"],
        "executableSha256": matrix["harness"]["expectedExecutableSha256"],
    }:
        raise ConfigurationError("evidence harness identity drift")
    execution_custody = harness.get("executionCustody")
    if execution_custody is not None and execution_custody.get(
        "entrySha256"
    ) != harness.get("executableSha256"):
        raise ConfigurationError("evidence execution custody entry drift")
    skill = value.get("skill", {})
    if (
        skill.get("classification") != matrix["skill"]["classification"]
        or skill.get("treeSha256") != matrix["skill"]["expectedTreeSha256"]
        or skill.get("fileCount") != matrix["skill"]["expectedFileCount"]
        or skill.get("totalBytes") != matrix["skill"]["expectedTotalBytes"]
        or skill.get("relevantFiles") != matrix["skill"]["relevantFiles"]
    ):
        raise ConfigurationError("evidence skill identity drift")
    maximum = policy["limits"]["maximumPersistedDiagnosticBytes"]
    diagnostics = (
        value.get("process", {}).get("sanitizedStderr"),
        value.get("observation", {}).get("sanitizedFailureDiagnostic"),
        value.get("workspace", {}).get("auditError"),
    )
    if any(
        diagnostic is not None
        and (
            not isinstance(diagnostic, str) or len(diagnostic.encode("utf-8")) > maximum
        )
        for diagnostic in diagnostics
    ):
        raise ConfigurationError("evidence diagnostic exceeds the frozen limit")
    validate_analyzer_owned_fields(value, matrix)


def validate_evidence_against_frozen(
    value: dict[str, Any],
    policy: dict[str, Any],
    matrix: dict[str, Any],
    snapshot: SkillSnapshot,
) -> None:
    validate_frozen_declarations(policy, matrix)
    validate_live_skill_input(matrix, snapshot, snapshot.root)
    validate_evidence_against_frozen_declarations(value, policy, matrix)


def write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, pretty_json_bytes(value))


def pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def atomic_write_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory only while the destination is absent."""

    if os.name == "nt":  # pragma: no cover - Windows rename is no-replace
        try:
            os.rename(source, destination)
        except FileExistsError as error:
            raise ConfigurationError(
                "evidence destination appeared; nothing was published"
            ) from error
        return

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            raise ConfigurationError(
                "atomic no-replace evidence publication is unavailable"
            )
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise ConfigurationError(
                "atomic no-replace evidence publication is unavailable"
            )
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        raise ConfigurationError(
            "atomic no-replace evidence publication is unavailable"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ConfigurationError("evidence destination appeared; nothing was published")
    raise ConfigurationError("atomic evidence publication failed") from OSError(
        error_number, os.strerror(error_number), destination
    )


def declared_skill_snapshot(matrix: dict[str, Any]) -> SkillSnapshot:
    skill = matrix["skill"]
    return SkillSnapshot(
        Path(),
        skill["expectedTreeSha256"],
        skill["expectedFileCount"],
        skill["expectedTotalBytes"],
        tuple(skill["relevantFiles"]),
    )


def verify_live_inputs(
    policy: dict[str, Any],
    matrix: dict[str, Any],
    skill_root: Path,
    executable: Path,
) -> SkillSnapshot:
    snapshot = snapshot_skill_tree(skill_root)
    validate_frozen_inputs(policy, matrix, snapshot, skill_root)
    if file_digest(executable) != matrix["harness"]["expectedExecutableSha256"]:
        raise ConfigurationError("Prime executable drift")
    version = observe_version(executable, isolated_environment({}))
    if version != matrix["harness"]["expectedObservedVersion"]:
        raise ConfigurationError("Prime version drift")
    return snapshot


def report_value(
    evidence: list[dict[str, Any]], snapshot: SkillSnapshot
) -> dict[str, Any]:
    ordered = sorted(
        evidence,
        key=lambda item: (
            item["route"]["id"],
            item["program"]["id"],
            item["program"]["trial"],
        ),
    )
    rows = [
        {
            "routeId": item["route"]["id"],
            "provider": item["route"]["provider"],
            "model": item["route"]["model"],
            "programId": item["program"]["id"],
            "trial": item["program"]["trial"],
            "classification": item["observation"]["classification"],
            "collectorClassification": item["observation"].get(
                "collectorClassification"
            ),
            "durationMs": item["process"]["durationMs"],
            "effectsPassed": item["workspace"].get("effectsPassed", False),
            "directSubagentStartsObserved": item["toolAudit"].get(
                "subagentStartsObserved", 0
            ),
            "subagentExecutionProven": item["workspace"].get(
                "subagentExecutionProven", False
            ),
            "proseInvocationObserved": item["toolAudit"]["proseInvocationObserved"],
            "legacyTelemetryAttemptObserved": item["toolAudit"][
                "legacyTelemetryAttemptObserved"
            ],
            "usage": item["observation"]["usage"],
            "cost": item["observation"]["cost"],
        }
        for item in ordered
    ]
    costs = [
        float(row["cost"]["totalUsd"])
        for row in rows
        if isinstance(row["cost"], dict)
        and isinstance(row["cost"].get("totalUsd"), (int, float))
    ]
    claims = {
        "exploratoryOldSkillCompatibility": "observation-only",
        "interactiveTuiObserved": False,
        "currentSkillCompatibility": "unknown",
        "strictWrapperAdmission": False,
        "semanticConformance": "unknown",
        "proseComplete": "unknown",
        "releaseEligible": False,
    }
    return {
        "schema": "openprose.direct-skill-real-report/1",
        "evidenceCount": len(rows),
        "oldSkillEffectPasses": sum(
            row["classification"] == "old-skill-effect-pass" for row in rows
        ),
        "filesystemEffectPasses": sum(row["effectsPassed"] for row in rows),
        "subagentProvenPasses": sum(row["subagentExecutionProven"] for row in rows),
        "harnessReportedCost": {
            "currency": "USD",
            "total": sum(costs),
            "observations": len(costs),
            "authoritativeForBilling": False,
        },
        "spendControl": {
            "kind": "harness-reported-between-trial-stop-threshold",
            "authoritativeSpendCap": False,
            "missingReportedCostCountsAsZero": True,
            "singleTrialOvershootPossible": True,
        },
        "processCustody": {
            "newCaptureEnforcement": "bounded-original-group-v1",
            "retainedEvidenceWithBoundedSettlement": sum(
                isinstance(item.get("process", {}).get("settlement"), dict)
                for item in ordered
            ),
            "retainedEvidenceWithoutSettlement": sum(
                item.get("process", {}).get("settlement") is None for item in ordered
            ),
            "detachedDescendantsContained": False,
            "strictContainmentClaimed": False,
        },
        "skill": {
            "treeSha256": snapshot.tree_sha256,
            "fileCount": snapshot.file_count,
            "totalBytes": snapshot.total_bytes,
            "files": list(snapshot.files),
            "fileInventoryScope": (
                "complete-live-tree"
                if len(snapshot.files) == snapshot.file_count
                else "matrix-relevant-files-only"
            ),
            "fileBodiesRetained": False,
        },
        "rows": rows,
        "claims": claims,
    }


def report_markdown(report: dict[str, Any]) -> str:
    historical = report["processCustody"]["retainedEvidenceWithoutSettlement"]
    custody = (
        f"{historical} retained observation(s) predate bounded settlement "
        "metadata, so no cleanup authority is inferred from them. "
        if historical
        else "All observations carry bounded process-settlement metadata. "
    )
    custody += (
        "New POSIX captures settle only the original harness process group. "
        "A descendant that calls setsid(2) is not contained, so this lane makes "
        "no strict descendant-containment claim."
    )
    lines = [
        "# Direct legacy-skill observations",
        "",
        (
            "This is an opt-in noninteractive proxy for the direct skill path. "
            "It is not an interactive TUI observation and cannot admit current "
            "semantics or a release."
        ),
        (
            "A filesystem-effect pass is not a subagent pass. The audited "
            "classification requires authoritative subagent execution evidence; "
            "a structured start event alone is insufficient."
        ),
        custody,
        "",
        f"- Evidence: {report['evidenceCount']}",
        f"- Old-skill effect passes: {report['oldSkillEffectPasses']}",
        f"- Exact filesystem-effect passes: {report['filesystemEffectPasses']}",
        f"- Direct-subagent-proven passes: {report['subagentProvenPasses']}",
        (
            "- Harness-reported non-authoritative cost: "
            f"${report['harnessReportedCost']['total']:.6f}"
        ),
        (
            "- Spend control: **non-authoritative between-trial heuristic**; "
            "missing reported cost counts as zero and one trial can overshoot."
        ),
        f"- Legacy skill tree: `{report['skill']['treeSha256']}`",
        "- Current skill compatibility: **unknown**",
        "- Semantic conformance: **unknown**",
        "- Interactive TUI observed: **no**",
        "- Release eligible: **no**",
        "",
        (
            "| Route | Model | Program | Trial | Classification | Effects | "
            "Direct subagent starts | CLI attempted | Telemetry attempted |"
        ),
        "|---|---|---|---:|---|---|---:|---|---|",
    ]
    for row in report["rows"]:
        prefix = (
            f"| {row['routeId']} | {row['model']} | {row['programId']} | "
            f"{row['trial']} | "
        )
        lines.append(
            prefix
            + f"{row['classification']} | {'yes' if row['effectsPassed'] else 'no'} | "
            f"{row['directSubagentStartsObserved']} | "
            f"{'yes' if row['proseInvocationObserved'] else 'no'} | "
            f"{'yes' if row['legacyTelemetryAttemptObserved'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def validate_derived_reports(
    evidence_directory: Path,
    evidence: list[dict[str, Any]],
    snapshot: SkillSnapshot,
) -> None:
    """Verify both committed report forms without rewriting retained evidence."""

    if not evidence:
        return
    report = report_value(evidence, snapshot)
    expected = {
        "report.json": pretty_json_bytes(report),
        "REPORT.md": report_markdown(report).encode("utf-8"),
    }
    for name, body in expected.items():
        path = evidence_directory / name
        try:
            observed = read_bounded_regular(
                path, MAXIMUM_SKILL_TREE_BYTES, f"derived report {name}"
            )
        except ConfigurationError as error:
            raise ConfigurationError(f"derived report drift: {name}") from error
        if observed != body:
            raise ConfigurationError(f"derived report drift: {name}")


def command_validate(args: argparse.Namespace) -> int:
    policy = read_json(args.policy)
    matrix = read_json(args.matrix)
    validate_frozen_declarations(policy, matrix)
    evidence = []
    for path in sorted(args.evidence.glob("*.evidence.json")):
        retained = read_json(path)
        validate_evidence_against_frozen_declarations(retained, policy, matrix)
        evidence.append(retained)
    validate_derived_reports(args.evidence, evidence, declared_skill_snapshot(matrix))
    if getattr(args, "verify_live_inputs", False):
        executable = getattr(args, "executable", None)
        skill_root = getattr(args, "skill_root", None)
        if executable is None or skill_root is None:
            raise ConfigurationError(
                "--verify-live-inputs requires --executable and --skill-root"
            )
        if not executable.is_absolute() or not skill_root.is_absolute():
            raise ConfigurationError("live input paths must be absolute")
        verify_live_inputs(policy, matrix, skill_root, executable)
    return 0


def command_report(args: argparse.Namespace) -> int:
    policy = read_json(args.policy)
    matrix = read_json(args.matrix)
    validate_frozen_declarations(policy, matrix)
    snapshot = declared_skill_snapshot(matrix)
    if getattr(args, "verify_live_inputs", False):
        executable = getattr(args, "executable", None)
        skill_root = getattr(args, "skill_root", None)
        if executable is None or skill_root is None:
            raise ConfigurationError(
                "--verify-live-inputs requires --executable and --skill-root"
            )
        if not executable.is_absolute() or not skill_root.is_absolute():
            raise ConfigurationError("live input paths must be absolute")
        snapshot = verify_live_inputs(policy, matrix, skill_root, executable)
    evidence = []
    for path in sorted(args.evidence.glob("*.evidence.json")):
        retained = read_json(path)
        validate_evidence_against_frozen_declarations(retained, policy, matrix)
        item = reanalyze_evidence(deepcopy(retained), matrix)
        validate_evidence_against_frozen_declarations(item, policy, matrix)
        evidence.append(item)
    report = report_value(evidence, snapshot)
    write_json(args.evidence / "report.json", report)
    atomic_write_bytes(
        args.evidence / "REPORT.md", report_markdown(report).encode("utf-8")
    )
    return 0


def command_run(args: argparse.Namespace) -> int:
    if not (
        args.i_understand_this_spends_money
        and args.i_understand_this_uses_a_legacy_skill
    ):
        raise ConfigurationError(
            "real execution requires both explicit opt-in acknowledgements"
        )
    requested_target = Path(args.evidence)
    if requested_target.exists() or requested_target.is_symlink():
        raise ConfigurationError(
            "evidence target already exists; choose a new versioned directory"
        )
    policy = read_json(args.policy)
    matrix = read_json(args.matrix)
    validate_frozen_declarations(policy, matrix)
    if not args.executable.is_absolute() or not args.skill_root.is_absolute():
        raise ConfigurationError("live input paths must be absolute")
    credential_names = {
        name for route in matrix["routes"] for name in route["credentialEnvironment"]
    }
    credentials = parse_dotenv_selected(args.env_file, credential_names)
    declared_skill_root = args.skill_root
    declared_skill_source = declared_skill_root / "SKILL.md"
    if declared_skill_root.is_symlink() or (
        declared_skill_source.resolve(strict=True)
        != declared_skill_root.resolve(strict=True) / "SKILL.md"
    ):
        raise ConfigurationError("explicit skill path does not match tree root")
    requested_target.parent.mkdir(parents=True, exist_ok=True)
    target_parent = requested_target.parent.resolve(strict=True)
    target = target_parent / requested_target.name
    if target.exists() or target.is_symlink():
        raise ConfigurationError(
            "evidence target already exists; choose a new versioned directory"
        )
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.staging-", dir=target_parent
    ) as raw_staging:
        staging = Path(raw_staging)
        snapshot = snapshot_tree_for_execution(
            declared_skill_root, staging / "custody" / "skill", "skill tree"
        )
        validate_skill_snapshot_identity(matrix, snapshot)
        skill_source_path = snapshot.root / "SKILL.md"
        harness_custody = prepare_harness_execution_custody(
            args.executable,
            matrix["harness"]["expectedExecutableSha256"],
            staging / "custody" / "harness",
        )
        executable = harness_custody.executable
        executable_sha256 = matrix["harness"]["expectedExecutableSha256"]
        base_environment = isolated_environment({})
        version = observe_version(
            executable, base_environment, harness_custody.launch_prefix
        )
        if version != matrix["harness"]["expectedObservedVersion"]:
            raise ConfigurationError("Prime version drift")
        publication = staging / "publication"
        publication.mkdir()
        evidence: list[dict[str, Any]] = []
        reported_total = 0.0
        total_trials = 0
        for route in matrix["routes"]:
            route_stopped = False
            route_cost = 0.0
            for program in matrix["programs"]:
                if route_stopped:
                    break
                for trial in range(1, policy["limits"]["maximumTrialsPerCell"] + 1):
                    if total_trials >= policy["limits"]["maximumTotalTrials"]:
                        raise ConfigurationError("frozen total trial limit exhausted")
                    if reported_total >= policy["limits"]["maximumTotalCostUsd"]:
                        raise ConfigurationError(
                            "non-authoritative harness-reported cost stop "
                            "threshold reached"
                        )
                    if route_cost >= route["plannedTotalCostCeilingUsd"]:
                        route_stopped = True
                        break
                    item = run_trial(
                        executable=executable,
                        executable_sha256=executable_sha256,
                        observed_version=version,
                        skill_snapshot=snapshot,
                        policy=policy,
                        matrix=matrix,
                        route=route,
                        program=program,
                        trial=trial,
                        env_file=args.env_file,
                        credential_values=credentials,
                        executable_prefix=harness_custody.launch_prefix,
                        skill_source_path=skill_source_path,
                        execution_custody=harness_custody.evidence,
                    )
                    validate_evidence_against_frozen_declarations(item, policy, matrix)
                    evidence.append(item)
                    total_trials += 1
                    cost = item["observation"]["cost"]
                    observed_cost = (
                        float(cost["totalUsd"])
                        if isinstance(cost, dict)
                        and isinstance(cost.get("totalUsd"), (int, float))
                        else 0.0
                    )
                    route_cost += observed_cost
                    reported_total += observed_cost
                    filename = (
                        f"{route['id']}--{program['id']}--trial-{trial}.evidence.json"
                    )
                    write_json(publication / filename, item)
                    if item["observation"]["classification"] not in {
                        "old-skill-effect-pass",
                        "filesystem-effect-pass-subagent-unproven",
                    }:
                        if policy["execution"]["stopRouteAfterUnavailable"]:
                            route_stopped = True
                        break
        report = report_value(evidence, declared_skill_snapshot(matrix))
        write_json(publication / "report.json", report)
        atomic_write_bytes(
            publication / "REPORT.md", report_markdown(report).encode("utf-8")
        )
        publish_directory_no_replace(publication, target)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    validate.add_argument("--verify-live-inputs", action="store_true")
    validate.add_argument("--executable", type=Path)
    validate.add_argument("--skill-root", type=Path)
    report = commands.add_parser("report")
    report.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    report.add_argument("--verify-live-inputs", action="store_true")
    report.add_argument("--executable", type=Path)
    report.add_argument("--skill-root", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    run.add_argument("--executable", type=Path, required=True)
    run.add_argument("--skill-root", type=Path, required=True)
    run.add_argument("--env-file", type=Path)
    run.add_argument("--i-understand-this-spends-money", action="store_true")
    run.add_argument("--i-understand-this-uses-a-legacy-skill", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            return command_validate(args)
        if args.command == "report":
            return command_report(args)
        return command_run(args)
    except (ConfigurationError, OSError, json.JSONDecodeError) as error:
        print(f"direct-skill-real: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
