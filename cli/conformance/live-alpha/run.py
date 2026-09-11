#!/usr/bin/env python3
"""Run the explicit, cost-bearing functional-alpha smoke through one CLI."""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, NamedTuple


HERE = Path(__file__).resolve().parent
CLI = HERE.parents[1]
DEFAULT_PROGRAM = HERE / "hello.prose.md"
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_ENV_FILE_BYTES = 64 * 1024
MAX_LAUNCHER_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
MAX_NATIVE_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_NODE_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_NODE_PROBE_BYTES = 512
MAX_HARNESS_FILES = 65_536
MAX_HARNESS_PACKAGES = 512
MAX_HARNESS_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_HARNESS_FILE_BYTES = 512 * 1024 * 1024
MAX_HARNESS_SYMLINK_HOPS = 32
ALLOWED_CREDENTIALS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_OAUTH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CODEX_ACCESS_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "GEMINI_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_PROJECT",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    }
)
# The evidence driver is a credential boundary.  It intentionally does not
# treat the caller's whole environment as an execution surface: doing so would
# give every candidate (and, transitively, every selected harness) unrelated
# tokens, build controls, proxy credentials, or local service addresses.  HOME
# remains admitted because all four functional-alpha cached-login routes may
# use their ordinary HOME-backed stores.  XDG_CONFIG_HOME is deliberately not
# ambient authority; run_smoke assigns one fresh driver-owned root below.
BASE_ENVIRONMENT_NAMES = (
    "PATH",
    "PATHEXT",
    "HOME",
    "USER",
    "LOGNAME",
    "USERPROFILE",
    "SHELL",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "__CF_USER_TEXT_ENCODING",
)
ADAPTERS = {
    "prime": "prime/rpc",
    "omp": "omp/rpc",
    "codex": "codex/exec-json",
    "claude": "claude/print-stream-json",
}
TRANSPORTS = {
    "prime": "rpc",
    "omp": "rpc",
    "codex": "exec-json",
    "claude": "print-stream-json",
}
HARNESS_COMMANDS = {
    "prime": "prime-agent",
    "omp": "omp",
    "codex": "codex",
    "claude": "claude",
}
HARNESS_PACKAGES = {
    "prime": ("prime-agent", "node"),
    "omp": ("@oh-my-pi/pi-coding-agent", "bun"),
    "codex": ("@openai/codex", "node"),
}
AUTH_PROFILES = {
    "prime": {
        "prime-harness-login": "harness-login",
        "anthropic": "provider-api-key",
        "openai": "provider-api-key",
        "openrouter": "provider-api-key",
        "google": "provider-api-key",
        "github-copilot": "provider-access-token",
        "aws-bedrock": "cloud-provider-credentials",
    },
    "omp": {
        "omp-harness-login": "harness-login",
        "anthropic": "provider-api-key",
        "openai": "provider-api-key",
        "openrouter": "provider-api-key",
        "google": "provider-api-key",
        "github-copilot": "provider-access-token",
        "aws-bedrock": "cloud-provider-credentials",
    },
    "codex": {
        "cached-chatgpt-login": "harness-login",
        "openai-api-key": "provider-api-key",
        "codex-access-token": "provider-access-token",
    },
    "claude": {"claude-subscription": "harness-login"},
}
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,255}$")
RECIPE_PATHS = {
    harness: CLI / "shared/capabilities/adapters/recipes" / filename
    for harness, filename in {
        "prime": "prime-rpc.v1.json",
        "omp": "omp-rpc.v1.json",
        "codex": "codex-exec-json.v1.json",
        "claude": "claude-print-stream-json.v1.json",
    }.items()
}
IMAGE_MANIFEST_PATH = CLI / "shared/image/echo-v0/manifest.json"
SEMVER = re.compile(
    r"(?<![0-9A-Za-z])"
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?![0-9A-Za-z])"
)
PRIME_DIAGNOSTIC_LIFECYCLE_PHASES = frozenset(
    {
        "await-prompt-ack",
        "await-agent-start",
        "await-turn-start",
        "await-user-message-start",
        "await-user-message-end",
        "await-assistant-message-start",
        "await-thinking-or-text-start",
        "await-thinking-delta-or-end",
        "await-text-start",
        "await-text-delta-or-end",
        "await-assistant-message-end",
        "await-turn-end",
        "await-agent-end",
        "complete",
    }
)


class SmokeError(RuntimeError):
    pass


class HarnessAdmission(NamedTuple):
    admitted_versions: tuple[str, ...]
    repair_command: str
    recipe_digest: str


class AdmittedFile(NamedTuple):
    path: Path
    role: str
    encoded: bytes
    maximum: int
    command_path: Path | None = None
    command_path_identity: tuple[int, int, int, int, int] | None = None
    search_path: str | None = None


class AdmittedHarnessFile(NamedTuple):
    path: Path
    label: str
    byte_length: int
    sha256: str
    identity: tuple[int, int, int, int, int]
    allow_empty: bool = False


class AdmittedRouteLink(NamedTuple):
    path: Path
    identity: tuple[int, int, int, int, int]
    target: str


class AdmittedHarnessRoute(NamedTuple):
    command_name: str
    command_path: Path
    search_path: str
    links: tuple[AdmittedRouteLink, ...]
    target: Path
    path_selected: bool = True


class AbsentDependency(NamedTuple):
    package_root: Path
    dependency: str


class HarnessCustody(NamedTuple):
    evidence: dict[str, Any]
    files: tuple[AdmittedHarnessFile, ...]
    routes: tuple[AdmittedHarnessRoute, ...]
    absent_dependencies: tuple[AbsentDependency, ...]


class BoundedCapture:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.data = bytearray()
        self.overflow = threading.Event()
        self.error: OSError | None = None

    def drain(self, stream: Any) -> None:
        try:
            while True:
                block = stream.read(65_536)
                if not block:
                    return
                remaining = self.maximum + 1 - len(self.data)
                if remaining > 0:
                    self.data.extend(block[:remaining])
                if len(self.data) > self.maximum or len(block) > remaining:
                    self.overflow.set()
                    return
        except OSError as error:
            self.error = error
        finally:
            try:
                stream.close()
            except OSError:
                pass


def process_group_exists(group: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_candidate(process: subprocess.Popen[bytes]) -> bool:
    if os.name == "nt":
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
        return process.poll() is not None
    if process.pid == os.getpgrp():
        return False
    for sent_signal in (signal.SIGTERM, signal.SIGKILL):
        if not process_group_exists(process.pid):
            break
        try:
            os.killpg(process.pid, sent_signal)
        except ProcessLookupError:
            break
        except PermissionError:
            return False
        deadline = time.monotonic() + 5
        while process_group_exists(process.pid) and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.01)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return process.poll() is not None and not process_group_exists(process.pid)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    try:
        linked = path.lstat()
    except OSError as error:
        raise SmokeError(f"{label} is unavailable: {error}") from error
    if stat.S_ISLNK(linked.st_mode):
        raise SmokeError(f"{label} must be a non-symlink regular file")
    if not stat.S_ISREG(linked.st_mode) or not 0 < linked.st_size <= maximum:
        raise SmokeError(
            f"{label} must be a non-empty regular file no larger than {maximum} bytes"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SmokeError(f"{label} could not be opened safely: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            linked.st_dev,
            linked.st_ino,
            linked.st_size,
            linked.st_mtime_ns,
        ) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise SmokeError(f"{label} changed before it was opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum:
                raise SmokeError(f"{label} exceeds {maximum} bytes")
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or total != opened.st_size:
            raise SmokeError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def json_object(encoded: bytes, label: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                raise SmokeError(f"{label} contains a duplicate JSON key")
            result[name] = value
        return result

    try:
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=no_duplicates)
    except SmokeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeError(f"{label} is not one UTF-8 JSON object") from error
    if not isinstance(value, dict):
        raise SmokeError(f"{label} is not a JSON object")
    return value


def sanitized_base_environment(
    ambient: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if ambient is None else ambient
    if os.name == "nt":
        by_name = {name.upper(): value for name, value in source.items()}
        selected = {
            name: by_name[name.upper()]
            for name in BASE_ENVIRONMENT_NAMES
            if name.upper() in by_name
        }
    else:
        selected = {
            name: source[name] for name in BASE_ENVIRONMENT_NAMES if name in source
        }
    selected.setdefault("PATH", os.defpath)
    return selected


def require_unaliased_absolute_path(path: Path, label: str) -> Path:
    """Require every raw path component to name its canonical non-symlink object."""

    if not path.is_absolute():
        raise SmokeError(f"{label} path must be absolute")
    raw = str(path)
    if os.path.normpath(raw) != raw:
        raise SmokeError(f"{label} path must not contain an alias")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as error:
            raise SmokeError(f"{label} is unavailable: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise SmokeError(f"{label} path must not traverse a symbolic link or alias")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SmokeError(f"{label} is unavailable: {error}") from error
    if resolved != path:
        raise SmokeError(f"{label} path must equal its exact realpath without aliases")
    return path


def node_platform_id(platform_name: str, architecture: str, libc: str | None) -> str:
    if platform_name not in {"darwin", "linux", "win32"} or architecture not in {
        "arm64",
        "x64",
    }:
        raise SmokeError("npm candidate closure has an unsupported Node platform")
    if platform_name == "linux":
        if libc not in {"gnu", "musl"}:
            raise SmokeError("Node runtime did not report a supported Linux libc")
        return f"linux-{architecture}-{libc}"
    if libc is not None:
        raise SmokeError("Node runtime reported libc on a non-Linux platform")
    return f"{platform_name}-{architecture}"


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _admit_closure_member(
    path: Path, role: str, maximum: int
) -> tuple[dict[str, Any], AdmittedFile]:
    path = require_unaliased_absolute_path(path, f"candidate closure {role}")
    encoded = regular_bytes(path, f"candidate closure {role}", maximum)
    require_unaliased_absolute_path(path, f"candidate closure {role}")
    return (
        {
            "role": role,
            "path": str(path),
            "byteLength": len(encoded),
            "sha256": sha256_bytes(encoded),
        },
        AdmittedFile(path, role, encoded, maximum),
    )


def admit_node_runtime(
    environment: dict[str, str], cwd: Path
) -> tuple[dict[str, Any], AdmittedFile]:
    search_path = environment.get("PATH", os.defpath)
    command = shutil.which("node", path=search_path)
    if command is None:
        raise SmokeError("Node executable is unavailable on the sanitized PATH")
    command_path = Path(command)
    if not command_path.is_absolute():
        raise SmokeError("Node executable selected from PATH is not absolute")
    try:
        command_metadata = command_path.lstat()
        canonical = command_path.resolve(strict=True)
    except OSError as error:
        raise SmokeError(f"Node executable is unavailable: {error}") from error
    if not (
        stat.S_ISREG(command_metadata.st_mode) or stat.S_ISLNK(command_metadata.st_mode)
    ):
        raise SmokeError("Node executable route is not a regular file or symbolic link")
    member, base_admitted = _admit_closure_member(
        canonical, "node-executable", MAX_NODE_EXECUTABLE_BYTES
    )
    probe = (
        "const h=(process.report&&process.report.getReport)"
        "?process.report.getReport().header:{};"
        "const libc=process.platform==='linux'"
        "?(h.glibcVersionRuntime?'gnu':'musl'):null;"
        "process.stdout.write(JSON.stringify({platform:process.platform,"
        "architecture:process.arch,libc})+'\\n');"
    )
    exit_code, stdout, stderr, _ = run_bounded(
        [str(canonical), "--eval", probe],
        cwd=cwd,
        environment=environment,
        timeout_seconds=10,
        maximum_output_bytes=MAX_NODE_PROBE_BYTES,
    )
    if exit_code != 0 or stderr:
        raise SmokeError("Node platform probe failed without a clean bounded result")
    observed = json_object(stdout, "Node platform probe")
    if set(observed) != {"platform", "architecture", "libc"}:
        raise SmokeError("Node platform probe has an unsupported shape")
    platform_name = observed["platform"]
    architecture = observed["architecture"]
    libc = observed["libc"]
    if (
        not isinstance(platform_name, str)
        or not isinstance(architecture, str)
        or (libc is not None and not isinstance(libc, str))
    ):
        raise SmokeError("Node platform probe has invalid field types")
    platform_id = node_platform_id(platform_name, architecture, libc)
    admitted = AdmittedFile(
        base_admitted.path,
        base_admitted.role,
        base_admitted.encoded,
        base_admitted.maximum,
        command_path,
        _metadata_identity(command_metadata),
        search_path,
    )
    require_candidate_closure_unchanged((admitted,))
    return (
        {
            "commandPath": str(command_path),
            "path": member["path"],
            "byteLength": member["byteLength"],
            "sha256": member["sha256"],
            "platform": platform_name,
            "architecture": architecture,
            "libc": libc,
            "platformId": platform_id,
        },
        admitted,
    )


def admit_candidate_closure(
    raw_candidate: Path,
    declared_surface: str | None,
    *,
    node_environment: dict[str, str] | None = None,
    node_cwd: Path | None = None,
) -> tuple[Path, dict[str, Any], tuple[AdmittedFile, ...]]:
    candidate = require_unaliased_absolute_path(raw_candidate, "candidate")

    if declared_surface != "npm":
        member, admitted = _admit_closure_member(
            candidate, "executable", MAX_NATIVE_EXECUTABLE_BYTES
        )
        return (
            candidate,
            {
                "schema": "openprose.candidate-closure/1",
                "kind": "direct",
                "members": [member],
            },
            (admitted,),
        )

    meta_root = candidate.parent.parent
    scope_root = meta_root.parent
    expected_launcher = meta_root / "bin" / "prose.js"
    if (
        candidate != expected_launcher
        or meta_root.name != "prose-cli"
        or scope_root.name != "@openprose"
    ):
        raise SmokeError(
            "npm candidate must be the exact @openprose/prose-cli/bin/prose.js launcher"
        )

    launcher_member, launcher_admitted = _admit_closure_member(
        candidate, "launcher", MAX_LAUNCHER_BYTES
    )
    meta_path = meta_root / "package.json"
    meta_member, meta_admitted = _admit_closure_member(
        meta_path, "meta-package-json", MAX_PACKAGE_MANIFEST_BYTES
    )
    meta = json_object(meta_admitted.encoded, "candidate closure meta-package-json")
    meta_version = meta.get("version")
    if (
        meta.get("name") != "@openprose/prose-cli"
        or not isinstance(meta_version, str)
        or SEMVER.fullmatch(meta_version) is None
        or meta.get("bin") != {"prose": "bin/prose.js"}
    ):
        raise SmokeError("npm meta package identity is missing or invalid")

    node, node_admitted = admit_node_runtime(
        node_environment or sanitized_base_environment(), node_cwd or HERE
    )
    platform_id = node["platformId"]
    platform_package = f"@openprose/prose-cli-{platform_id}"
    optional = meta.get("optionalDependencies")
    if not isinstance(optional, dict) or optional.get(platform_package) != meta_version:
        raise SmokeError(
            "npm meta package does not bind the selected platform package "
            "at its exact version"
        )
    platform_basename = platform_package.removeprefix("@openprose/")
    manifest_candidates = (
        meta_root / "node_modules" / "@openprose" / platform_basename / "package.json",
        scope_root / platform_basename / "package.json",
    )
    existing_platform_paths: list[Path] = []
    for path in manifest_candidates:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise SmokeError(
                f"selected npm platform package is unavailable: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SmokeError(
                "candidate closure platform-package-json must be a non-symlink "
                "regular file"
            )
        existing_platform_paths.append(path)
    if not existing_platform_paths:
        raise SmokeError("selected npm platform package is not installed")
    if len(existing_platform_paths) != 1:
        raise SmokeError("duplicate selected npm platform packages are installed")
    platform_path = existing_platform_paths[0]

    platform_member, platform_admitted = _admit_closure_member(
        platform_path, "platform-package-json", MAX_PACKAGE_MANIFEST_BYTES
    )
    platform_manifest = json_object(
        platform_admitted.encoded, "candidate closure platform-package-json"
    )
    expected_binary = (
        "bin/prose.exe" if platform_id.startswith("win32-") else "bin/prose"
    )
    declared_length = platform_manifest.get("openproseBinaryByteLength")
    declared_digest = platform_manifest.get("openproseBinarySha256")
    if (
        platform_manifest.get("name") != platform_package
        or platform_manifest.get("version") != meta_version
        or platform_manifest.get("openproseBinary") != expected_binary
        or isinstance(declared_length, bool)
        or not isinstance(declared_length, int)
        or declared_length < 1
        or not isinstance(declared_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", declared_digest) is None
    ):
        raise SmokeError("selected npm platform package identity is missing or invalid")

    platform_root = platform_path.parent
    native_path = platform_root / Path(expected_binary)
    native_member, native_admitted = _admit_closure_member(
        native_path, "native-executable", MAX_NATIVE_EXECUTABLE_BYTES
    )
    if (
        native_member["byteLength"] != declared_length
        or native_member["sha256"] != declared_digest
    ):
        raise SmokeError(
            "selected npm native executable differs from platform package "
            "integrity metadata"
        )
    if os.name != "nt" and not os.access(native_path, os.X_OK):
        raise SmokeError("selected npm native executable is not executable")

    members = [launcher_member, meta_member, platform_member, native_member]
    admitted = (
        launcher_admitted,
        meta_admitted,
        platform_admitted,
        native_admitted,
        node_admitted,
    )
    if len({item.path for item in admitted}) != len(admitted):
        raise SmokeError(
            "candidate closure and Node executable contain duplicate paths"
        )
    return (
        candidate,
        {
            "schema": "openprose.candidate-closure/1",
            "kind": "npm",
            "members": members,
            "npm": {
                "platformId": platform_id,
                "metaPackage": "@openprose/prose-cli",
                "metaVersion": meta_version,
                "platformPackage": platform_package,
                "platformVersion": meta_version,
                "binaryRelativePath": expected_binary,
                "declaredBinaryByteLength": declared_length,
                "declaredBinarySha256": declared_digest,
                "node": node,
            },
        },
        admitted,
    )


def require_candidate_closure_unchanged(admitted: tuple[AdmittedFile, ...]) -> None:
    for item in admitted:
        require_unaliased_absolute_path(item.path, f"candidate closure {item.role}")
        require_unchanged(
            item.path,
            item.encoded,
            f"candidate closure {item.role}",
            item.maximum,
        )
        require_unaliased_absolute_path(item.path, f"candidate closure {item.role}")
        if item.command_path is not None:
            try:
                route_metadata = item.command_path.lstat()
                route_target = item.command_path.resolve(strict=True)
            except OSError as error:
                raise SmokeError(f"Node executable route changed: {error}") from error
            selected = shutil.which("node", path=item.search_path)
            if (
                selected is None
                or Path(selected) != item.command_path
                or _metadata_identity(route_metadata) != item.command_path_identity
                or route_target != item.path
            ):
                raise SmokeError(
                    "Node executable route changed after initial admission"
                )


def _hash_regular_file(
    path: Path, label: str, *, allow_empty: bool = False
) -> AdmittedHarnessFile:
    try:
        linked = path.lstat()
    except OSError as error:
        raise SmokeError(f"{label} is unavailable: {error}") from error
    if stat.S_ISLNK(linked.st_mode) or not stat.S_ISREG(linked.st_mode):
        raise SmokeError(f"{label} must be a non-symlink regular file")
    minimum = 0 if allow_empty else 1
    if not minimum <= linked.st_size <= MAX_HARNESS_FILE_BYTES:
        raise SmokeError(
            f"{label} must be nonempty and no larger than "
            f"{MAX_HARNESS_FILE_BYTES} bytes"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SmokeError(f"{label} could not be opened safely: {error}") from error
    try:
        opened = os.fstat(descriptor)
        before = _metadata_identity(linked)
        if not stat.S_ISREG(opened.st_mode) or _metadata_identity(opened) != before:
            raise SmokeError(f"{label} changed before it was opened")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_HARNESS_FILE_BYTES:
                raise SmokeError(f"{label} exceeds the bounded file limit")
            digest.update(block)
        after = os.fstat(descriptor)
        if _metadata_identity(after) != before or total != opened.st_size:
            raise SmokeError(f"{label} changed while being read")
        return AdmittedHarnessFile(
            path, label, total, digest.hexdigest(), before, allow_empty
        )
    finally:
        os.close(descriptor)


def _regular_prefix(path: Path, label: str, maximum: int = 128) -> bytes:
    item = _hash_regular_file(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        prefix = os.read(descriptor, maximum)
        if _metadata_identity(os.fstat(descriptor)) != item.identity:
            raise SmokeError(f"{label} changed while its launcher was inspected")
        return prefix
    finally:
        os.close(descriptor)


def _resolve_path_route(
    command_name: str,
    environment: dict[str, str],
    *,
    explicit_path: Path | None = None,
) -> tuple[AdmittedHarnessRoute, AdmittedHarnessFile]:
    search_path = environment.get("PATH", os.defpath)
    if explicit_path is None:
        selected = shutil.which(command_name, path=search_path)
        if selected is None:
            raise SmokeError(f"{command_name} is unavailable on the sanitized PATH")
        command_path = Path(selected)
    else:
        command_path = explicit_path
    if not command_path.is_absolute():
        raise SmokeError(f"{command_name} PATH route must be absolute")
    if explicit_path is None:
        require_unaliased_absolute_path(
            command_path.parent, f"{command_name} PATH directory"
        )
    current = command_path
    links: list[AdmittedRouteLink] = []
    visited: set[Path] = set()
    for _ in range(MAX_HARNESS_SYMLINK_HOPS + 1):
        if current in visited:
            raise SmokeError(f"{command_name} PATH route contains a symlink cycle")
        visited.add(current)
        try:
            metadata = current.lstat()
        except OSError as error:
            raise SmokeError(
                f"{command_name} PATH route is unavailable: {error}"
            ) from error
        if not stat.S_ISLNK(metadata.st_mode):
            break
        if len(links) == MAX_HARNESS_SYMLINK_HOPS:
            raise SmokeError(f"{command_name} PATH route has too many symlink hops")
        try:
            target = os.readlink(current)
        except OSError as error:
            raise SmokeError(
                f"{command_name} PATH route could not be read: {error}"
            ) from error
        links.append(AdmittedRouteLink(current, _metadata_identity(metadata), target))
        target_path = Path(target)
        current = (
            target_path if target_path.is_absolute() else current.parent / target_path
        )
        current = Path(os.path.normpath(str(current)))
    else:  # pragma: no cover - loop exits through break or bounded rejection
        raise SmokeError(f"{command_name} PATH route could not be resolved")
    try:
        target = current.resolve(strict=True)
    except OSError as error:
        raise SmokeError(
            f"{command_name} executable is unavailable: {error}"
        ) from error
    admitted = _hash_regular_file(target, f"{command_name} executable")
    if os.name != "nt" and not os.access(target, os.X_OK):
        raise SmokeError(f"{command_name} executable is not executable")
    return (
        AdmittedHarnessRoute(
            command_name,
            command_path,
            search_path,
            tuple(links),
            target,
            explicit_path is None,
        ),
        admitted,
    )


def _package_manifest(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    encoded = regular_bytes(path, label, MAX_PACKAGE_MANIFEST_BYTES)
    return json_object(encoded, label), encoded


def _find_package_root(entrypoint: Path, package_name: str, command_name: str) -> Path:
    for root in (entrypoint.parent, *entrypoint.parents):
        manifest_path = root / "package.json"
        try:
            metadata = manifest_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise SmokeError(
                f"{package_name} package manifest is unavailable: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SmokeError(f"{package_name} package manifest must be a regular file")
        manifest, _ = _package_manifest(
            manifest_path, f"{package_name} package manifest"
        )
        if manifest.get("name") != package_name:
            continue
        bin_value = manifest.get("bin")
        relative = entrypoint.relative_to(root).as_posix()
        declared = bin_value.get(command_name) if isinstance(bin_value, dict) else None
        if declared != relative and not (
            package_name == "@oh-my-pi/pi-coding-agent" and relative == "scripts/omp"
        ):
            raise SmokeError(
                f"{package_name} package bin entry does not select the PATH target"
            )
        return root
    raise SmokeError(f"{package_name} package root could not be identified")


def _resolve_dependency(package_root: Path, dependency: str) -> Path | None:
    parts = dependency.split("/") if dependency.startswith("@") else [dependency]
    if any(not part or part in {".", ".."} for part in parts):
        raise SmokeError("harness package declares an invalid dependency name")
    for ancestor in (package_root, *package_root.parents):
        candidate = ancestor / "node_modules"
        for part in parts:
            candidate /= part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise SmokeError(f"harness dependency is unavailable: {error}") from error
        require_unaliased_absolute_path(
            candidate.parent, "harness dependency route directory"
        )
        if stat.S_ISLNK(metadata.st_mode):
            raise SmokeError(
                "harness dependency directory symlinks are unsupported by "
                "strict custody"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise SmokeError("harness dependency route is not a directory")
        return require_unaliased_absolute_path(candidate, "harness dependency")
    return None


def _capture_package_tree(
    root: Path, package_name: str
) -> tuple[dict[str, Any], list[AdmittedHarnessFile]]:
    members: list[dict[str, Any]] = []
    admitted: list[AdmittedHarnessFile] = []
    for raw_directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory = Path(raw_directory)
        if "node_modules" in directory_names:
            directory_names.remove("node_modules")
        for name in list(directory_names):
            child = directory / name
            if stat.S_ISLNK(child.lstat().st_mode):
                raise SmokeError(
                    f"{package_name} package tree contains a directory symlink"
                )
        for name in file_names:
            child = directory / name
            relative = child.relative_to(root).as_posix()
            item = _hash_regular_file(
                child, f"{package_name} package file", allow_empty=True
            )
            admitted.append(item)
            members.append(
                {
                    "relativePath": relative,
                    "byteLength": item.byte_length,
                    "sha256": item.sha256,
                    "executable": bool(item.identity[2] & 0o111),
                }
            )
            if len(admitted) > MAX_HARNESS_FILES:
                raise SmokeError(
                    "harness execution closure exceeds the file-count limit"
                )
    members.sort(key=lambda member: member["relativePath"])
    if not members:
        raise SmokeError(f"{package_name} package tree is empty")
    tree_bytes = canonical_json(
        {"schema": "openprose.harness-package-tree/1", "members": members}
    )
    return (
        {
            "memberCount": len(members),
            "totalByteLength": sum(member["byteLength"] for member in members),
            "sha256": sha256_bytes(tree_bytes),
        },
        admitted,
    )


def _capture_package_graph(
    root: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    list[AdmittedHarnessFile],
    list[AbsentDependency],
]:
    pending = deque([root.resolve(strict=True)])
    seen: set[Path] = set()
    packages: list[dict[str, Any]] = []
    package_summaries: dict[Path, dict[str, Any]] = {}
    admitted: list[AdmittedHarnessFile] = []
    edges: list[dict[str, Any]] = []
    missing_optional = 0
    absent_dependencies: list[AbsentDependency] = []
    while pending:
        package_root = pending.popleft()
        if package_root in seen:
            continue
        seen.add(package_root)
        if len(seen) > MAX_HARNESS_PACKAGES:
            raise SmokeError(
                "harness execution closure exceeds the package-count limit"
            )
        manifest_path = package_root / "package.json"
        manifest, manifest_bytes = _package_manifest(
            manifest_path, "harness dependency package manifest"
        )
        name = manifest.get("name")
        version = manifest.get("version")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(version, str)
            or SEMVER.fullmatch(version) is None
        ):
            raise SmokeError("harness dependency package identity is invalid")
        tree, tree_files = _capture_package_tree(package_root, name)
        manifest_file = next(
            (item for item in tree_files if item.path == manifest_path), None
        )
        if manifest_file is None or manifest_file.sha256 != sha256_bytes(
            manifest_bytes
        ):
            raise SmokeError("harness package manifest differs from its package tree")
        admitted.extend(tree_files)
        package_summary = {
            "name": name,
            "version": version,
            "root": package_root == root,
            "manifest": {
                "byteLength": len(manifest_bytes),
                "sha256": sha256_bytes(manifest_bytes),
            },
            "tree": tree,
        }
        packages.append(package_summary)
        package_summaries[package_root] = package_summary
        dependencies: dict[str, str] = {}
        dependency_kinds: dict[str, str] = {}
        for field, kind in (
            ("dependencies", "required"),
            ("optionalDependencies", "optional"),
            ("peerDependencies", "peer"),
        ):
            raw = manifest.get(field, {})
            if not isinstance(raw, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in raw.items()
            ):
                raise SmokeError(f"{name} package {field} is malformed")
            for dependency, declared in raw.items():
                dependencies[dependency] = declared
                dependency_kinds[dependency] = kind
        for dependency in sorted(dependencies):
            resolved = _resolve_dependency(package_root, dependency)
            kind = dependency_kinds[dependency]
            if resolved is None:
                if kind == "required":
                    raise SmokeError(
                        f"required harness dependency {dependency} is missing"
                    )
                missing_optional += 1
                absent_dependencies.append(AbsentDependency(package_root, dependency))
                edges.append(
                    {
                        "fromName": name,
                        "fromVersion": version,
                        "dependency": dependency,
                        "kind": kind,
                        "toManifestSha256": None,
                        "toTreeSha256": None,
                    }
                )
                continue
            dependency_manifest, dependency_bytes = _package_manifest(
                resolved / "package.json", "resolved harness dependency manifest"
            )
            if not isinstance(dependency_manifest.get("name"), str):
                raise SmokeError("resolved harness dependency identity is invalid")
            edges.append(
                {
                    "fromName": name,
                    "fromVersion": version,
                    "dependency": dependency,
                    "kind": kind,
                    "toManifestSha256": sha256_bytes(dependency_bytes),
                    "_resolvedPath": str(resolved),
                }
            )
            pending.append(resolved)
    if len(admitted) > MAX_HARNESS_FILES:
        raise SmokeError("harness execution closure exceeds the file-count limit")
    total = sum(item.byte_length for item in admitted)
    if total > MAX_HARNESS_TOTAL_BYTES:
        raise SmokeError("harness execution closure exceeds the total byte limit")
    for edge in edges:
        resolved_text = edge.pop("_resolvedPath", None)
        if resolved_text is not None:
            resolved_summary = package_summaries.get(Path(resolved_text))
            if resolved_summary is None:
                raise SmokeError(
                    "resolved dependency escaped the captured package graph"
                )
            edge["toTreeSha256"] = resolved_summary["tree"]["sha256"]
    packages.sort(
        key=lambda item: (
            not item["root"],
            item["name"],
            item["version"],
            item["tree"]["sha256"],
        )
    )
    edges.sort(
        key=lambda item: (
            item["fromName"],
            item["fromVersion"],
            item["dependency"],
            item["kind"],
            item["toManifestSha256"] or "",
            item["toTreeSha256"] or "",
        )
    )
    graph = {
        "edgeCount": len(edges),
        "missingOptionalCount": missing_optional,
        "sha256": sha256_bytes(
            canonical_json(
                {"schema": "openprose.harness-dependency-graph/1", "edges": edges}
            )
        ),
    }
    return packages, graph, admitted, absent_dependencies


def _file_summary(item: AdmittedHarnessFile) -> dict[str, Any]:
    return {"byteLength": item.byte_length, "sha256": item.sha256}


def _runtime_version(name: str, executable: Path, environment: dict[str, str]) -> str:
    exit_code, stdout, stderr, _ = run_bounded(
        [str(executable), "--version"],
        cwd=HERE,
        environment=environment,
        timeout_seconds=10,
        maximum_output_bytes=512,
    )
    if exit_code != 0 or stderr:
        raise SmokeError(f"{name} runtime version probe failed")
    try:
        observed = stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise SmokeError(f"{name} runtime version probe was not UTF-8") from error
    normalized = observed.removeprefix("v") if name == "node" else observed
    if SEMVER.fullmatch(normalized) is None:
        raise SmokeError(f"{name} runtime version probe was not exact semver")
    if name == "bun" and tuple(int(part) for part in normalized.split(".")[:3]) < (
        1,
        3,
        14,
    ):
        raise SmokeError("OMP 18.0.9 requires Bun 1.3.14 or newer")
    return normalized


def admit_harness_custody(
    harness: str,
    environment: dict[str, str],
    target_node: dict[str, Any],
    target_node_admitted: AdmittedFile,
) -> HarnessCustody:
    command_name = HARNESS_COMMANDS[harness]
    route, entrypoint = _resolve_path_route(command_name, environment)
    files: list[AdmittedHarnessFile] = [entrypoint]
    routes = [route]
    packages: list[dict[str, Any]] = []
    dependency_graph: dict[str, Any] | None = None
    absent_dependencies: list[AbsentDependency] = []
    runtime: dict[str, Any] | None = None
    launcher_kind = "native"
    distribution_kind = "native"
    prefix = _regular_prefix(entrypoint.path, f"{command_name} executable")
    env_package_launcher = harness in HARNESS_PACKAGES and prefix.startswith(
        f"#!/usr/bin/env {HARNESS_PACKAGES[harness][1]}\n".encode("ascii")
    )
    omp_shell_launcher = harness == "omp" and prefix.startswith(b"#!/bin/sh\n")
    if env_package_launcher or omp_shell_launcher:
        package_name, runtime_name = HARNESS_PACKAGES[harness]
        launcher_kind = "shell-shebang" if omp_shell_launcher else "env-shebang"
        distribution_kind = "dev-shell-package" if omp_shell_launcher else "npm-package"
        package_root = _find_package_root(entrypoint.path, package_name, command_name)
        (
            packages,
            dependency_graph,
            package_files,
            absent_dependencies,
        ) = _capture_package_graph(package_root)
        files.extend(package_files)
        launcher_interpreter_route, launcher_interpreter = _resolve_path_route(
            "sh" if omp_shell_launcher else "env",
            environment,
            explicit_path=Path("/bin/sh" if omp_shell_launcher else "/usr/bin/env"),
        )
        files.append(launcher_interpreter)
        routes.append(launcher_interpreter_route)
        if runtime_name == "node":
            runtime_file = AdmittedHarnessFile(
                target_node_admitted.path,
                "node executable",
                len(target_node_admitted.encoded),
                sha256_bytes(target_node_admitted.encoded),
                _metadata_identity(target_node_admitted.path.lstat()),
                False,
            )
            runtime_route, _ = _resolve_path_route("node", environment)
            if runtime_route.target != target_node_admitted.path:
                raise SmokeError("harness Node runtime differs from the target probe")
            if _file_summary(runtime_file) != {
                "byteLength": target_node["byteLength"],
                "sha256": target_node["sha256"],
            }:
                raise SmokeError(
                    "harness Node runtime differs from the target identity"
                )
        else:
            runtime_route, runtime_file = _resolve_path_route("bun", environment)
        routes.append(runtime_route)
        files.append(runtime_file)
        runtime = {
            "name": runtime_name,
            "version": _runtime_version(
                runtime_name, runtime_route.target, environment
            ),
            "launcher": {
                "name": "sh" if omp_shell_launcher else "env",
                "executable": _file_summary(launcher_interpreter),
            },
            "executable": _file_summary(runtime_file),
        }
        root_package = next(
            (
                item
                for item in packages
                if item["name"] == package_name and item["root"]
            ),
            None,
        )
        if root_package is None:
            raise SmokeError(
                "harness root package is absent from its execution closure"
            )
        if harness == "codex":
            platform_suffix = (
                target_node["platformId"].replace("-gnu", "").replace("-musl", "")
            )
            expected_platform = f"@openai/codex-{platform_suffix}"
            root_version = root_package["version"]
            if not any(
                (item["name"] == expected_platform)
                or (
                    item["name"] == "@openai/codex"
                    and item["version"] == f"{root_version}-{platform_suffix}"
                )
                for item in packages
                if not item["root"]
            ):
                raise SmokeError(
                    "Codex execution closure lacks its selected platform package"
                )
    elif prefix.startswith(b"#!"):
        raise SmokeError(
            f"{harness} harness uses an unsupported script interpreter closure"
        )

    # Deduplicate the entrypoint/manifest files that are also package-tree members.
    unique_files = {item.path: item for item in files}
    files = list(unique_files.values())
    total = sum(item.byte_length for item in files)
    if len(files) > MAX_HARNESS_FILES or total > MAX_HARNESS_TOTAL_BYTES:
        raise SmokeError("harness execution closure exceeds its bounded limits")
    route_summary = {
        "kind": "symlink-chain" if route.links else "direct",
        "linkCount": len(route.links),
    }
    evidence: dict[str, Any] = {
        "schema": "openprose.harness-byte-custody/1",
        "harness": harness,
        "commandName": command_name,
        "route": route_summary,
        "distributionKind": distribution_kind,
        "launcherKind": launcher_kind,
        "entrypoint": _file_summary(entrypoint),
        "runtime": runtime,
        "packages": packages,
        "dependencyGraph": dependency_graph,
        "fileCount": len(files),
        "totalByteLength": total,
        "externalAuthorities": [
            "dynamic-undeclared-imports",
            "native-os-loader-libraries",
            "interpreter-resource-files",
            "ambient-harness-config-plugins-skills",
            "cached-account-provider-state",
            "provider-side-model-routing",
        ],
    }
    evidence["aggregateSha256"] = sha256_bytes(canonical_json(evidence))
    custody = HarnessCustody(
        evidence, tuple(files), tuple(routes), tuple(absent_dependencies)
    )
    require_harness_custody_unchanged(custody, environment)
    return custody


def require_harness_custody_unchanged(
    custody: HarnessCustody, environment: dict[str, str]
) -> None:
    for route in custody.routes:
        if route.path_selected:
            selected = shutil.which(route.command_name, path=route.search_path)
            if selected is None or Path(selected) != route.command_path:
                raise SmokeError(
                    f"{route.command_name} PATH selection changed after admission"
                )
        for link in route.links:
            try:
                metadata = link.path.lstat()
                target = os.readlink(link.path)
            except OSError as error:
                raise SmokeError(
                    f"{route.command_name} symlink route changed: {error}"
                ) from error
            if _metadata_identity(metadata) != link.identity or target != link.target:
                raise SmokeError(
                    f"{route.command_name} symlink route changed after admission"
                )
        try:
            current_target = route.command_path.resolve(strict=True)
        except OSError as error:
            raise SmokeError(f"{route.command_name} route changed: {error}") from error
        if current_target != route.target:
            raise SmokeError(
                f"{route.command_name} route target changed after admission"
            )
    for item in custody.files:
        current = _hash_regular_file(
            item.path, item.label, allow_empty=item.allow_empty
        )
        if (
            current.identity != item.identity
            or current.byte_length != item.byte_length
            or current.sha256 != item.sha256
        ):
            raise SmokeError(f"{item.label} changed after initial admission")
    for absent in custody.absent_dependencies:
        if _resolve_dependency(absent.package_root, absent.dependency) is not None:
            raise SmokeError(
                "a previously absent optional harness dependency appeared "
                "after admission"
            )


def parse_env_file(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    encoded = regular_bytes(path, "credential environment file", MAX_ENV_FILE_BYTES)
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SmokeError("credential environment file must be UTF-8") from error
    selected: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in ALLOWED_CREDENTIALS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise SmokeError(
                f"credential {name} has an unsupported empty or multiline value"
            )
        selected[name] = value
    return selected


def run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    maximum_output_bytes: int = MAX_OUTPUT_BYTES,
) -> tuple[int, bytes, bytes, int]:
    started = time.monotonic()
    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        if os.name == "nt"
        else 0
    )
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=(os.name != "nt"),
            creationflags=creation_flags,
        )
    except OSError as error:
        raise SmokeError(f"candidate command could not start: {error}") from error
    stdout = BoundedCapture(maximum_output_bytes)
    stderr = BoundedCapture(maximum_output_bytes)
    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = started + timeout_seconds
    failure: str | None = None
    try:
        while (
            process.poll() is None
            and not stdout.overflow.is_set()
            and not stderr.overflow.is_set()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if process.poll() is None:
            failure = (
                "exceeded the bounded output limit"
                if stdout.overflow.is_set() or stderr.overflow.is_set()
                else f"timed out after {timeout_seconds}s"
            )
        remaining = max(0.0, deadline - time.monotonic())
        for reader in readers:
            reader.join(timeout=min(5.0, remaining))
        if stdout.overflow.is_set() or stderr.overflow.is_set():
            failure = "exceeded the bounded output limit"
        if any(reader.is_alive() for reader in readers):
            failure = failure or "left output streams unsettled"
        if os.name != "nt" and process_group_exists(process.pid):
            failure = failure or "left its process group unsettled"
        if failure is not None:
            if not terminate_candidate(process):
                raise SmokeError(
                    f"candidate command {failure}; cleanup could not be verified"
                )
            for reader in readers:
                reader.join(timeout=5)
            raise SmokeError(f"candidate command {failure}")
        if stdout.error is not None or stderr.error is not None:
            raise SmokeError("candidate command output could not be read")
        assert process.returncode is not None
        return (
            process.returncode,
            bytes(stdout.data),
            bytes(stderr.data),
            round((time.monotonic() - started) * 1000),
        )
    except BaseException:
        if process.poll() is None or (
            os.name != "nt" and process_group_exists(process.pid)
        ):
            terminate_candidate(process)
        raise


def parse_json_stdout(stdout: bytes, label: str) -> dict[str, Any]:
    try:
        return json_object(stdout, label)
    except SmokeError as error:
        raise SmokeError(f"{label} did not emit one JSON object") from error


def sanitized_prime_parser_failure(value: dict[str, Any]) -> str | None:
    """Project one closed Prime parser diagnostic without retaining candidate text."""

    adapter = value.get("adapter")
    error = value.get("error")
    if (
        value.get("schema") != "openprose.runner-result/1"
        or value.get("runnerExitCode") != 22
        or not isinstance(adapter, dict)
        or adapter.get("id") != "prime/rpc"
        or not isinstance(error, dict)
        or error.get("schema") != "openprose.runner-error/1"
        or error.get("code") not in {"PROTOCOL_MALFORMED", "PROTOCOL_TRUNCATED"}
        or error.get("boundary") != "protocol"
        or error.get("exitCode") != 22
        or not isinstance(error.get("details"), dict)
    ):
        return None
    diagnostic = error["details"].get("adapterDiagnostic")
    if not isinstance(diagnostic, dict) or set(diagnostic) != {
        "schema",
        "adapterId",
        "stage",
        "phase",
        "counters",
    }:
        return None
    if (
        diagnostic.get("schema") != "openprose.adapter-diagnostic/1"
        or diagnostic.get("adapterId") != "prime/rpc"
    ):
        return None
    stage = diagnostic.get("stage")
    phase = diagnostic.get("phase")
    if not (
        (stage == "jsonl-framing" and phase == "record-boundary")
        or (stage == "prime-lifecycle" and phase in PRIME_DIAGNOSTIC_LIFECYCLE_PHASES)
    ):
        return None
    counters = diagnostic.get("counters")
    if not isinstance(counters, dict) or set(counters) != {
        "acceptedRecords",
        "thinkingDeltas",
        "textDeltas",
        "saturated",
    }:
        return None
    for name in ("acceptedRecords", "thinkingDeltas", "textDeltas"):
        counter = counters.get(name)
        if type(counter) is not int or not 0 <= counter <= 4_294_967_295:
            return None
    if type(counters.get("saturated")) is not bool:
        return None
    return (
        "candidate-reported Prime parser diagnostic: "
        f"code={error['code']} stage={stage} phase={phase} "
        f"acceptedRecords={counters['acceptedRecords']} "
        f"thinkingDeltas={counters['thinkingDeltas']} "
        f"textDeltas={counters['textDeltas']} "
        f"saturated={str(counters['saturated']).lower()}"
    )


def command_json(
    candidate: Path,
    args: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    label: str,
    render_prime_diagnostic: bool = False,
) -> tuple[dict[str, Any], int]:
    exit_code, stdout, stderr, duration_ms = run_bounded(
        [str(candidate), "--output", "json", *args],
        cwd=cwd,
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    if stderr:
        raise SmokeError(f"{label} wrote diagnostics")
    value = parse_json_stdout(stdout, label)
    if exit_code != 0:
        safe_diagnostic = (
            sanitized_prime_parser_failure(value) if render_prime_diagnostic else None
        )
        if safe_diagnostic is not None:
            raise SmokeError(f"{label} failed with {safe_diagnostic}")
        raise SmokeError(f"{label} failed with a nonzero exit")
    return value, duration_ms


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise SmokeError(f"{label} differs")


def require_unchanged(path: Path, expected: bytes, label: str, maximum: int) -> None:
    """Reauthenticate an admitted input around every candidate execution phase.

    This detects ordinary same-user replacement or mutation while keeping the
    evidence bound to the bytes that were initially admitted. It is not a
    privilege boundary against an attacker able to replace and restore files
    between these checks.
    """

    if regular_bytes(path, label, maximum) != expected:
        raise SmokeError(f"{label} changed after initial admission")


def adapter_recipe(harness: str) -> tuple[dict[str, Any], str]:
    path = RECIPE_PATHS[harness]
    encoded = regular_bytes(path, f"{harness} adapter recipe", 1024 * 1024)
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeError(f"{harness} adapter recipe is malformed") from error
    if not isinstance(value, dict) or value.get("adapterId") != ADAPTERS[harness]:
        raise SmokeError(f"{harness} adapter recipe identity differs")
    return value, sha256_bytes(encoded)


def echo_image_contract() -> dict[str, Any]:
    encoded = regular_bytes(IMAGE_MANIFEST_PATH, "echo-v0 image manifest", 1024 * 1024)
    try:
        manifest = json.loads(encoded)
        result = {
            "image": {
                "formatVersion": manifest["imageFormatVersion"],
                "version": manifest["imageVersion"],
                "sha256": manifest["aggregateSha256"]["sha256"],
            },
            "releaseEligible": manifest["releaseEligible"],
            "deliveredImageSha256": manifest["modelVisibleBytes"]["sha256"],
            "terminalSchemaSha256": manifest["terminalEnvelope"]["sha256"],
        }
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise SmokeError("echo-v0 image manifest is malformed") from error
    if (
        result["image"]["formatVersion"] != "openprose.skill-runtime-image/1"
        or result["image"]["version"] != "echo-v0"
        or not isinstance(result["releaseEligible"], bool)
    ):
        raise SmokeError("echo-v0 image manifest identity differs")
    for name in ("sha256",):
        require_sha(result["image"][name], f"echo-v0 image {name}")
    require_sha(result["deliveredImageSha256"], "echo-v0 delivered image digest")
    require_sha(result["terminalSchemaSha256"], "echo-v0 terminal schema digest")
    return result


def validate_harness_version(harness: str, observed: str) -> HarnessAdmission:
    recipe, recipe_digest = adapter_recipe(harness)
    pattern = recipe.get("probe", {}).get("versionPattern")
    support = recipe.get("support")
    if not isinstance(pattern, str) or not isinstance(support, dict):
        raise SmokeError(f"{harness} adapter recipe lacks exact version admission")
    admitted = support.get("admittedVersions")
    repair_command = support.get("repairCommand")
    if (
        not isinstance(admitted, list)
        or not admitted
        or any(
            not isinstance(version, str) or SEMVER.fullmatch(version) is None
            for version in admitted
        )
        or len(set(admitted)) != len(admitted)
        or not isinstance(repair_command, str)
        or re.fullmatch(r"[^\x00-\x1f\x7f]{1,1024}", repair_command) is None
    ):
        raise SmokeError(f"{harness} adapter recipe lacks exact version admission")
    try:
        version_shape_matches = re.fullmatch(pattern, observed) is not None
    except re.error as error:
        raise SmokeError(
            f"{harness} adapter recipe lacks exact version admission"
        ) from error
    matches = list(SEMVER.finditer(observed)) if version_shape_matches else []
    if len(matches) != 1 or matches[0].group(0) not in admitted:
        raise SmokeError(f"{harness} harness version is outside frozen support")
    return HarnessAdmission(tuple(admitted), repair_command, recipe_digest)


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SmokeError(f"{label} is missing or invalid")
    return value


def require_runner(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"name", "version", "commit"}:
        raise SmokeError(f"{label} identity is missing or invalid")
    if (
        value.get("name") not in {"rust", "bun"}
        or not isinstance(value.get("version"), str)
        or SEMVER.fullmatch(value["version"]) is None
        or not isinstance(value.get("commit"), str)
        or re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", value["commit"]) is None
    ):
        raise SmokeError(f"{label} identity is missing or invalid")
    return value


def invocation_identity(options: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(options.model, str) or MODEL_ID.fullmatch(options.model) is None:
        raise SmokeError(
            "live evidence requires an explicit bounded --model identifier"
        )
    profiles = AUTH_PROFILES[options.harness]
    if (
        not isinstance(options.auth_profile, str)
        or options.auth_profile not in profiles
    ):
        raise SmokeError(
            "live evidence requires an explicit --auth-profile supported by "
            "the selected harness"
        )
    return {
        "model": options.model,
        "authRoute": {
            "profile": options.auth_profile,
            "category": profiles[options.auth_profile],
        },
    }


def run_smoke(options: argparse.Namespace) -> dict[str, Any]:
    invocation = invocation_identity(options)
    base_environment = sanitized_base_environment()
    program = options.program.expanduser().resolve(strict=True)
    candidate, candidate_closure, admitted_candidate = admit_candidate_closure(
        options.candidate,
        options.surface,
        node_environment=base_environment,
        node_cwd=program.parent,
    )
    if candidate_closure["kind"] == "npm":
        target_node = candidate_closure["npm"]["node"]
        target_node_admitted = next(
            item for item in admitted_candidate if item.role == "node-executable"
        )
    else:
        target_node, target_node_admitted = admit_node_runtime(
            base_environment, program.parent
        )
        if target_node_admitted.path in {item.path for item in admitted_candidate}:
            raise SmokeError("target Node executable duplicates a candidate member")
        admitted_candidate = (*admitted_candidate, target_node_admitted)
    harness_custody = admit_harness_custody(
        options.harness,
        base_environment,
        target_node,
        target_node_admitted,
    )
    program_bytes = regular_bytes(program, "program", 4 * 1024 * 1024)
    credentials = parse_env_file(options.env_file)
    image_contract = echo_image_contract()

    config_root: Path | None = None
    configuration_facts: dict[str, bool] | None = None
    with tempfile.TemporaryDirectory(prefix="openprose-live-alpha-") as raw_config:
        config_root = Path(raw_config).resolve()
        if any(config_root.iterdir()):
            raise SmokeError("owned configuration root did not start empty")
        if os.name != "nt" and stat.S_IMODE(config_root.stat().st_mode) != 0o700:
            raise SmokeError("owned configuration root is not private")
        environment = dict(base_environment)
        environment.update(credentials)
        environment["XDG_CONFIG_HOME"] = raw_config
        environment["NO_COLOR"] = "1"
        environment["TERM"] = "dumb"
        environment["CI"] = "1"
        environment.pop("PROSE_MODEL", None)
        environment.pop("PROSE_AUTH_PROFILE", None)

        require_candidate_closure_unchanged(admitted_candidate)
        require_harness_custody_unchanged(harness_custody, environment)
        selection, _ = command_json(
            candidate,
            [
                "cli",
                "harness",
                "use",
                options.harness,
                "--model",
                options.model,
                "--auth-profile",
                options.auth_profile,
            ],
            cwd=program.parent,
            environment=environment,
            timeout_seconds=options.timeout,
            label="harness selection",
        )
        require_equal(
            selection.get("schema"), "openprose.harness-selection/1", "selection schema"
        )
        require_equal(selection.get("harness"), options.harness, "persisted harness")
        require_equal(selection.get("scope"), "user", "selection scope")
        require_candidate_closure_unchanged(admitted_candidate)
        require_harness_custody_unchanged(harness_custody, environment)
        require_unchanged(program, program_bytes, "program", 4 * 1024 * 1024)
        raw_selection_path = selection.get("path")
        if (
            not isinstance(raw_selection_path, str)
            or not Path(raw_selection_path).is_absolute()
        ):
            raise SmokeError("persisted selection path is not absolute")
        selection_path = Path(raw_selection_path)
        try:
            selection_metadata = selection_path.lstat()
            resolved_selection_path = selection_path.resolve(strict=True)
            resolved_selection_path.relative_to(config_root)
        except (OSError, ValueError) as error:
            raise SmokeError(
                "persisted selection escaped the owned configuration root"
            ) from error
        if stat.S_ISLNK(selection_metadata.st_mode) or not stat.S_ISREG(
            selection_metadata.st_mode
        ):
            raise SmokeError("persisted selection is not an owned regular file")
        if selection_metadata.st_nlink != 1 or (
            hasattr(os, "getuid") and selection_metadata.st_uid != os.getuid()
        ):
            raise SmokeError("persisted selection does not have private file custody")
        configuration_facts = {
            "xdgConfigHomeOwned": True,
            "startedEmpty": True,
            "selectionPathContained": True,
            "ownedTemporaryRootRemoved": False,
        }

        doctor, _ = command_json(
            candidate,
            ["cli", "doctor"],
            cwd=program.parent,
            environment=environment,
            timeout_seconds=options.timeout,
            label="doctor",
        )
        require_equal(
            doctor.get("schema"), "openprose.doctor-report/1", "doctor schema"
        )
        require_equal(
            doctor.get("selectedHarness"), options.harness, "doctor selectedHarness"
        )
        require_equal(doctor.get("ready"), True, "doctor readiness")
        require_equal(doctor.get("cwd"), str(program.parent), "doctor cwd")
        require_equal(
            doctor.get("selectedTransport"),
            TRANSPORTS[options.harness],
            "doctor transport",
        )
        require_equal(
            doctor.get("selectedAdapterId"), ADAPTERS[options.harness], "doctor adapter"
        )
        require_equal(
            doctor.get("authCategory"), "harness-managed", "doctor auth category"
        )
        require_equal(
            doctor.get("billingOwner"), "user-provider", "doctor billing owner"
        )
        require_candidate_closure_unchanged(admitted_candidate)
        require_harness_custody_unchanged(harness_custody, environment)
        require_unchanged(program, program_bytes, "program", 4 * 1024 * 1024)

        result, duration_ms = command_json(
            candidate,
            ["run", program.name],
            cwd=program.parent,
            environment=environment,
            timeout_seconds=options.timeout,
            label="functional-alpha run",
            render_prime_diagnostic=options.harness == "prime",
        )
        require_candidate_closure_unchanged(admitted_candidate)
        require_harness_custody_unchanged(harness_custody, environment)
        require_unchanged(program, program_bytes, "program", 4 * 1024 * 1024)

    assert config_root is not None and configuration_facts is not None
    if config_root.exists():
        raise SmokeError("owned configuration root was not removed after settlement")
    configuration_facts["ownedTemporaryRootRemoved"] = True
    require_harness_custody_unchanged(harness_custody, base_environment)

    require_equal(
        result.get("schema"), "openprose.runner-result/1", "runner result schema"
    )
    require_equal(result.get("runnerExitCode"), 0, "runner exit code")
    require_equal(
        result.get("adapter", {}).get("id"), ADAPTERS[options.harness], "adapter id"
    )
    require_equal(result.get("transport"), TRANSPORTS[options.harness], "transport")
    require_equal(
        result.get("languageImage", {}).get("version"), "echo-v0", "image version"
    )
    terminal = result.get("terminal")
    require_equal(
        terminal,
        {
            "classification": "success",
            "transportCompleted": True,
            "terminalEventObserved": True,
            "exitCode": 0,
            "signal": None,
        },
        "terminal settlement",
    )
    require_equal(
        result.get("semantic", {}).get("status"), "not-applicable", "semantic status"
    )
    require_equal(
        result.get("billing", {}).get("owner"), "user-provider", "billing owner"
    )
    require_equal(
        result.get("billing", {}).get("authCategory"),
        "harness-managed",
        "billing auth category",
    )
    runner = require_runner(result.get("runner"), "run runner")
    doctor_runner = require_runner(doctor.get("runner"), "doctor runner")
    require_equal(doctor_runner, runner, "doctor/run runner identity")
    surface = options.surface or runner["name"]
    expected_runner = "rust" if surface == "rust" else "bun"
    if runner["name"] != expected_runner:
        raise SmokeError("declared surface does not match runner identity")

    language_image = result.get("languageImage")
    expected_image = image_contract["image"]
    require_equal(language_image, expected_image, "language image identity")
    doctor_image = doctor.get("image")
    if (
        not isinstance(doctor_image, dict)
        or set(doctor_image)
        != {"formatVersion", "version", "sha256", "releaseEligible"}
        or not isinstance(doctor_image.get("releaseEligible"), bool)
    ):
        raise SmokeError("doctor image identity is missing or invalid")
    require_equal(
        {key: doctor_image[key] for key in ("formatVersion", "version", "sha256")},
        expected_image,
        "doctor/run image identity",
    )
    require_equal(
        doctor_image["releaseEligible"],
        image_contract["releaseEligible"],
        "doctor image release eligibility",
    )
    require_equal(
        result.get("digests", {}).get("deliveredImageSha256"),
        image_contract["deliveredImageSha256"],
        "delivered image digest",
    )

    result_cwd = result.get("cwd")
    cwd_text = str(program.parent)
    expected_cwd_digest = sha256_bytes(cwd_text.encode("utf-8"))
    require_equal(
        result_cwd,
        {"path": cwd_text, "identitySha256": expected_cwd_digest},
        "run cwd identity",
    )
    task = {
        "schema": "openprose.task-envelope/1",
        "argv": ["prose", "run", program.name],
        "interactionMode": "non-interactive",
    }
    expected_task_digest = sha256_bytes(canonical_json(task).rstrip(b"\n"))
    require_equal(
        result.get("digests", {}).get("taskSha256"), expected_task_digest, "task digest"
    )
    terminal_schema_digest = require_sha(
        result.get("semantic", {}).get("terminalSchemaSha256"),
        "terminal schema digest",
    )
    require_equal(
        terminal_schema_digest,
        image_contract["terminalSchemaSha256"],
        "terminal schema digest",
    )
    terminal_digest = require_sha(
        result.get("semantic", {}).get("terminalEnvelopeDigestSha256"),
        "terminal envelope digest",
    )
    harness_version = result.get("adapter", {}).get("harnessVersion")
    if not isinstance(harness_version, str) or not harness_version:
        raise SmokeError("harness version is missing")
    admission = validate_harness_version(options.harness, harness_version)
    if harness_custody.evidence["distributionKind"] in {
        "npm-package",
        "dev-shell-package",
    }:
        expected_package = HARNESS_PACKAGES[options.harness][0]
        observed_versions = [
            match.group(0) for match in SEMVER.finditer(harness_version)
        ]
        root_packages = [
            item
            for item in harness_custody.evidence["packages"]
            if item["name"] == expected_package and item["root"]
        ]
        if (
            len(observed_versions) != 1
            or len(root_packages) != 1
            or root_packages[0]["version"] != observed_versions[0]
        ):
            raise SmokeError(
                "harness version differs from its admitted package closure"
            )
    recipe_digest = admission.recipe_digest
    recipe, independently_observed_recipe_digest = adapter_recipe(options.harness)
    require_equal(
        independently_observed_recipe_digest,
        recipe_digest,
        "adapter recipe custody",
    )
    prompt_placement = (
        recipe.get("launch", {})
        .get("instructionPlacement", {})
        .get("manifestPlacementId")
    )
    isolation = recipe.get("isolation", {}).get("guarantee")
    if not isinstance(prompt_placement, str) or not isinstance(isolation, str):
        raise SmokeError("adapter recipe capability boundary is malformed")
    require_equal(
        doctor.get("promptPlacement"), prompt_placement, "doctor prompt placement"
    )
    require_equal(doctor.get("isolation"), isolation, "doctor isolation guarantee")
    capabilities = result.get("negotiatedCapabilities")
    if not isinstance(capabilities, dict):
        raise SmokeError("run negotiated capabilities are missing")
    require_equal(
        capabilities.get("promptPlacement"),
        prompt_placement,
        "run prompt placement",
    )
    require_equal(capabilities.get("isolation"), isolation, "run isolation guarantee")
    require_equal(
        doctor.get("selectedHarnessVersion"),
        harness_version,
        "doctor/run harness version",
    )
    require_equal(
        result.get("adapter", {}).get("descriptorDigestSha256"),
        recipe_digest,
        "adapter recipe digest",
    )
    require_candidate_closure_unchanged(admitted_candidate)
    require_harness_custody_unchanged(harness_custody, base_environment)
    require_unchanged(program, program_bytes, "program", 4 * 1024 * 1024)

    return {
        "schema": "openprose.functional-alpha-live-evidence/5",
        "status": "pass",
        "target": {
            "authority": "exact-node-runtime-probe",
            "platformId": target_node["platformId"],
            "platform": target_node["platform"],
            "architecture": target_node["architecture"],
            "libc": target_node["libc"],
            "node": {
                key: target_node[key]
                for key in ("commandPath", "path", "byteLength", "sha256")
            },
        },
        "surface": surface,
        "candidate": candidate_closure,
        "runner": runner,
        "harness": options.harness,
        "harnessCustody": harness_custody.evidence,
        "invocation": invocation,
        "program": {
            "path": str(program),
            "byteLength": len(program_bytes),
            "sha256": sha256_bytes(program_bytes),
        },
        "selection": {
            "exitCode": 0,
            "schema": selection["schema"],
            "scope": selection["scope"],
            "changed": bool(selection.get("changed")),
        },
        "configuration": configuration_facts,
        "doctor": {
            "exitCode": 0,
            "schema": doctor["schema"],
            "ready": doctor["ready"],
            "selectedHarness": doctor["selectedHarness"],
            "selectedHarnessVersion": doctor["selectedHarnessVersion"],
            "selectedTransport": doctor["selectedTransport"],
            "selectedAdapterId": doctor["selectedAdapterId"],
            "promptPlacement": prompt_placement,
            "isolation": isolation,
            "authCategory": doctor["authCategory"],
            "billingOwner": doctor["billingOwner"],
            "image": doctor["image"],
        },
        "run": {
            "exitCode": 0,
            "schema": result["schema"],
            "adapterId": result["adapter"]["id"],
            "adapterDescriptorSha256": recipe_digest,
            "harnessVersion": harness_version,
            "admittedVersions": list(admission.admitted_versions),
            "repairCommand": admission.repair_command,
            "transport": result["transport"],
            "promptPlacement": prompt_placement,
            "isolation": isolation,
            "image": expected_image,
            "deliveredImageSha256": result["digests"]["deliveredImageSha256"],
            "taskSha256": result["digests"]["taskSha256"],
            "cwdIdentitySha256": expected_cwd_digest,
            "terminal": terminal,
            "semantic": {
                "status": "not-applicable",
                "terminalSchemaSha256": terminal_schema_digest,
                "terminalEnvelopeDigestSha256": terminal_digest,
            },
            "billing": {
                "owner": "user-provider",
                "authCategory": "harness-managed",
            },
            "durationMs": duration_ms,
        },
        "claims": {
            "authority": "candidate-reported-smoke",
            "installedHarnessLaunched": "candidate-reported",
            "opaqueImageAndTaskDelivered": "candidate-reported",
            "terminalEnvelopeRecovered": "candidate-reported",
            "openProseExecuted": False,
            "semanticConformance": "not-applicable",
            "providerCredentialCharged": "unverified",
            "harnessByteBinding": (
                "independent-path-selection-and-bounded-declared-package-runtime-"
                "bytes-reauthenticated"
            ),
            "modelAndAuthRouteBinding": "driver-input-category-only",
            "ambientHarnessState": "external-unbound",
            "custodyThreatModel": "same-user-persistent-mutation-detection",
            "releaseEligible": False,
        },
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--candidate", type=Path, required=True)
    result.add_argument("--harness", choices=tuple(ADAPTERS), required=True)
    result.add_argument(
        "--surface",
        choices=("rust", "bun", "npm"),
        help="declared distribution surface; inferred for direct Rust/Bun binaries",
    )
    result.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    result.add_argument("--model")
    result.add_argument("--auth-profile")
    result.add_argument("--env-file", type=Path)
    result.add_argument("--timeout", type=int, default=300)
    result.add_argument("--out", type=Path)
    result.add_argument("--acknowledge-provider-cost", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    if not options.acknowledge_provider_cost:
        print(
            "functional-alpha: refusing a real provider run without "
            "--acknowledge-provider-cost",
            file=sys.stderr,
        )
        return 2
    if not 1 <= options.timeout <= 1800:
        print(
            "functional-alpha: --timeout must be in [1, 1800] seconds", file=sys.stderr
        )
        return 2
    try:
        evidence = run_smoke(options)
        encoded = canonical_json(evidence)
        if options.out is not None:
            output = options.out.expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as destination:
                destination.write(encoded)
        sys.stdout.buffer.write(encoded)
        return 0
    except (OSError, SmokeError) as error:
        print(f"functional-alpha: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
