#!/usr/bin/env python3
"""Exercise a bounded, provider-free lifecycle over an existing development package set."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, Mapping, Sequence


REPORT_SCHEMA = "openprose.package-lifecycle-report/1"
ERROR_SCHEMA = "openprose.package-lifecycle-error/1"
MARKER_NAME = ".openprose-package-lifecycle-root"
MARKER_BYTES = b"owned provider-free package lifecycle root\n"
MAX_TREE_ENTRIES = 8_192
MAX_TREE_FILE_BYTES = 256 * 1024 * 1024
SENSITIVE_NAMES = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "NODE_AUTH_TOKEN",
    }
)
SENSITIVE_SUFFIXES = ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN", "_CREDENTIALS")


class LifecycleError(RuntimeError):
    """A local package lifecycle boundary failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str) -> None:
    raise LifecycleError(code, message)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_benchmark() -> Any:
    path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "installed"
        / "benchmark.py"
    )
    spec = importlib.util.spec_from_file_location(
        "openprose_package_lifecycle_benchmark", path
    )
    if spec is None or spec.loader is None:
        fail("BENCHMARK_UNAVAILABLE", "installed-package benchmark cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_deadline(deadline_monotonic: float) -> float:
    if (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(float(deadline_monotonic))
        or float(deadline_monotonic) <= time.monotonic()
    ):
        fail(
            "ARGUMENT_INVALID",
            "deadline_monotonic must be a finite future monotonic time",
        )
    return float(deadline_monotonic)


def remaining(deadline: float, stage: str) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        fail("DEADLINE_EXCEEDED", f"package lifecycle deadline expired {stage}")
    return value


def prepare_owned_root(path: Path) -> Path:
    requested = Path(os.path.abspath(path))
    if os.path.lexists(requested):
        fail("OWNED_ROOT_UNSAFE", "lifecycle work root must not already exist")
    try:
        parent = requested.parent.resolve(strict=True)
        metadata = parent.lstat()
    except OSError as error:
        fail("OWNED_ROOT_UNSAFE", f"lifecycle work-root parent is unavailable: {error}")
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail(
            "OWNED_ROOT_UNSAFE",
            "lifecycle work-root parent must resolve to a directory",
        )
    absolute = parent / requested.name
    if os.path.lexists(absolute):
        fail("OWNED_ROOT_UNSAFE", "resolved lifecycle work root already exists")
    try:
        absolute.mkdir(mode=0o700)
        with (absolute / MARKER_NAME).open("xb") as marker:
            marker.write(MARKER_BYTES)
    except OSError as error:
        fail("OWNED_ROOT_UNSAFE", f"cannot create lifecycle work root: {error}")
    return absolute


def write_exclusive(path: Path, value: bytes, mode: int = 0o400) -> None:
    try:
        with path.open("xb") as destination:
            destination.write(value)
        path.chmod(mode)
    except OSError as error:
        fail(
            "OWNED_ROOT_UNSAFE",
            f"cannot write owned lifecycle input {path.name}: {error}",
        )


def snapshot_package(
    benchmark: Any, context: Mapping[str, Any], destination: Path
) -> dict[str, Any]:
    if destination.exists():
        fail("OWNED_ROOT_UNSAFE", "owned package snapshot already exists")
    destination.mkdir(mode=0o700)
    encoded = context.get("encoded")
    checksums = context.get("checksums")
    if not isinstance(encoded, dict) or not isinstance(checksums, dict):
        fail(
            "PACKAGE_MALFORMED",
            "verified package context lacks closed bytes and checksums",
        )
    for name in sorted(encoded):
        value = encoded[name]
        if (
            not isinstance(name, str)
            or not isinstance(value, bytes)
            or digest(value) != checksums.get(name)
        ):
            fail("PACKAGE_MUTATED", "verified package context changed before snapshot")
        write_exclusive(destination / name, value)
    sums = benchmark.safe_read(
        Path(context["root"]) / "SHA256SUMS", benchmark.MAX_EVIDENCE_BYTES
    )
    write_exclusive(destination / "SHA256SUMS", sums)
    try:
        return benchmark.verify_package_output(
            destination, expected_platform=context.get("platform")
        )
    except Exception as error:
        fail(
            getattr(error, "code", "PACKAGE_MALFORMED"),
            f"owned package snapshot failed: {error}",
        )


def active_processes(benchmark: Any) -> list[str]:
    probe = getattr(benchmark, "live_owned_probe_descriptions", None)
    if probe is None:
        fail(
            "PROCESS_AUTHORITY_UNAVAILABLE", "benchmark lacks owned-process bookkeeping"
        )
    values = probe()
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        fail(
            "PROCESS_AUTHORITY_UNAVAILABLE",
            "benchmark process bookkeeping is malformed",
        )
    return values


def probe_corrupted_member(
    benchmark: Any,
    context: Mapping[str, Any],
    probe_root: Path,
    member: str,
    *,
    deadline_monotonic: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = validate_deadline(deadline_monotonic)
    remaining(deadline, "before corruption probe")
    if os.path.lexists(probe_root):
        fail("OWNED_ROOT_UNSAFE", "corruption probe root already exists")
    probe_root.mkdir(mode=0o700)
    package = probe_root / "package"
    package.mkdir(mode=0o700)
    encoded = context.get("encoded")
    if not isinstance(encoded, dict) or member not in encoded:
        fail(
            "PACKAGE_MALFORMED",
            "corruption probe member is not in verified package bytes",
        )
    for name in sorted(encoded):
        value = encoded[name]
        if not isinstance(value, bytes):
            fail("PACKAGE_MALFORMED", "verified package bytes are malformed")
        write_exclusive(package / name, value + (b"\x00" if name == member else b""))
        remaining(deadline, f"while snapshotting corruption probe {member}")
    sums = benchmark.safe_read(
        Path(context["root"]) / "SHA256SUMS", benchmark.MAX_EVIDENCE_BYTES
    )
    write_exclusive(package / "SHA256SUMS", sums)
    install = probe_root / "must-not-install"
    if active_processes(benchmark):
        fail(
            "PROCESS_UNSETTLED",
            "a lifecycle process was active before corruption rejection",
        )
    try:
        benchmark.run_benchmark(
            package,
            install,
            trials=1,
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline,
        )
    except Exception as error:
        code = getattr(error, "code", None)
        if code != "CHECKSUM_MISMATCH":
            fail(
                "CORRUPTION_REJECTION_INVALID",
                f"corruption produced {code or type(error).__name__}",
            )
    else:
        fail("CORRUPTION_ACCEPTED", "corrupted package member was accepted")
    if os.path.lexists(install) or active_processes(benchmark):
        fail(
            "CORRUPTION_EXECUTED",
            "corruption rejection crossed the pre-execution boundary",
        )
    remaining(deadline, "after corruption probe")
    return {
        "status": "passed",
        "member": member,
        "errorCode": "CHECKSUM_MISMATCH",
        "executionStarted": False,
    }


def read_tree_file(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_TREE_FILE_BYTES:
        fail("INSTALL_MUTATED", f"installed tree member is unsafe or oversized: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            opened = os.fstat(source.fileno())
            value = source.read(MAX_TREE_FILE_BYTES + 1)
            after = os.fstat(source.fileno())
    except OSError as error:
        fail("INSTALL_MUTATED", f"cannot read installed tree member: {error}")
    if (
        len(value) > MAX_TREE_FILE_BYTES
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
    ):
        fail("INSTALL_MUTATED", f"installed tree member changed while read: {path}")
    return value


def tree_identity(root: Path) -> str:
    try:
        root_metadata = root.lstat()
        canonical_root = root.resolve(strict=True)
    except OSError as error:
        fail("INSTALL_MUTATED", f"cannot inspect installed root: {error}")
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        fail("INSTALL_MUTATED", "installed root is not a non-symlink directory")
    paths: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if len(paths) >= MAX_TREE_ENTRIES:
                        fail("INSTALL_MUTATED", "installed tree has too many members")
                    path = Path(entry.path)
                    paths.append(path)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError as error:
            fail("INSTALL_MUTATED", f"cannot enumerate installed tree: {error}")
    records: list[list[Any]] = []
    for path in sorted(paths, key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            records.append([relative, "directory", metadata.st_mode & 0o777])
        elif stat.S_ISREG(metadata.st_mode):
            value = read_tree_file(path)
            records.append(
                [
                    relative,
                    "regular",
                    metadata.st_mode & 0o777,
                    len(value),
                    digest(value),
                ]
            )
        elif stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            try:
                path.resolve(strict=True).relative_to(canonical_root)
            except (OSError, ValueError):
                fail(
                    "INSTALL_MUTATED",
                    f"installed symlink escapes its owned root: {relative}",
                )
            records.append([relative, "symlink", target])
        else:
            fail(
                "INSTALL_MUTATED",
                f"installed tree member has unsupported type: {relative}",
            )
    return digest(canonical_json(records))


def npm_environment(
    benchmark: Any, runtime_root: Path, tool_paths: list[Path], cache: Path
) -> dict[str, str]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    environment = benchmark.clean_environment(runtime_root, tool_paths)
    user_config = runtime_root / "npmrc"
    global_config = runtime_root / "global-npmrc"
    for config in (user_config, global_config):
        if not config.exists():
            write_exclusive(config, b"", 0o600)
    environment.update(
        {
            "npm_config_cache": str(cache),
            "npm_config_userconfig": str(user_config),
            "npm_config_globalconfig": str(global_config),
            "npm_config_registry": "http://127.0.0.1:9/",
            "npm_config_offline": "true",
            "npm_config_ignore_scripts": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
            "npm_config_package_lock": "false",
            "npm_config_progress": "false",
            "npm_config_color": "false",
        }
    )
    for name, value in environment.items():
        if name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES):
            fail(
                "ENVIRONMENT_UNSAFE",
                f"credential-shaped environment key survived: {name}",
            )
        if not isinstance(value, str):
            fail("ENVIRONMENT_UNSAFE", "lifecycle environment values must be strings")
    return environment


def validate_reinstalled_npm(
    benchmark: Any, install: Path, raw_report: Mapping[str, Any]
) -> dict[str, Any]:
    platform_value = raw_report["platform"]
    package_identity = raw_report["packageIdentity"]
    version = package_identity["version"]
    command, meta_root, platform_root, _modules = benchmark.npm_layout(
        install / "npm-prefix", platform_value
    )
    executable = "prose.exe" if platform_value.startswith("win32-") else "prose"
    launcher = benchmark.safe_read(
        meta_root / "bin" / "prose.js", benchmark.MAX_MEMBER_BYTES
    )
    manifest = benchmark.safe_read(
        platform_root / "package.json", benchmark.MAX_EVIDENCE_BYTES
    )
    binary = benchmark.safe_read(
        platform_root / "bin" / executable, benchmark.MAX_MEMBER_BYTES
    )
    npm_surface = raw_report["surfaces"]["npm-launcher"]
    resolution = raw_report["launcherResolution"]
    command_identity = benchmark.launcher_command_identity(
        command, meta_root / "bin" / "prose.js"
    )
    if (
        digest(launcher) != npm_surface["launcherSourceSha256"]
        or digest(manifest) != resolution["platformManifestSha256"]
        or digest(binary) != npm_surface["binarySha256"]
        or digest(binary) != package_identity["bunBinarySha256"]
        or command_identity != npm_surface["launcherCommandIdentity"]
        or command_identity != resolution["launcherCommand"]
    ):
        fail(
            "REINSTALL_IDENTITY_DIVERGED",
            "reinstalled npm package bytes or launcher diverged",
        )
    return {
        "command": command,
        "binarySha256": digest(binary),
        "launcherSourceSha256": digest(launcher),
        "launcherCommandIdentity": command_identity,
    }


def npm_uninstall_reinstall(
    benchmark: Any,
    package_context: Mapping[str, Any],
    install: Path,
    raw_report: Mapping[str, Any],
    *,
    deadline: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    platform_value = raw_report["platform"]
    version = raw_report["packageIdentity"]["version"]
    toolchain = raw_report["toolchain"]
    current_npm = benchmark.resolve_tool("npm")
    current_node = benchmark.resolve_tool("node")
    expected_node = {
        key: value for key, value in toolchain["node"].items() if key != "version"
    }
    if current_npm != toolchain["npm"] or current_node != expected_node:
        fail("TOOL_IDENTITY_DIVERGED", "npm or Node changed after the fresh install")
    npm_path = Path(current_npm["command"])
    node_path = Path(current_node["command"])
    prefix = install / "npm-prefix"
    command, meta_root, platform_root, _modules = benchmark.npm_layout(
        prefix, platform_value
    )
    if (
        not os.path.lexists(command)
        or not meta_root.is_dir()
        or not platform_root.is_dir()
    ):
        fail("INSTALL_MUTATED", "fresh npm installation is incomplete before uninstall")
    environment = npm_environment(
        benchmark,
        install / "lifecycle-npm-environment",
        [npm_path.parent, node_path.parent],
        install / "lifecycle-npm-cache",
    )
    base = [
        current_npm["command"],
        "uninstall",
        "--global",
        "--offline",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--loglevel=error",
        "--prefix",
        str(prefix),
        "@openprose/prose-cli",
        f"@openprose/prose-cli-{platform_value}",
    ]
    outcome = benchmark.run_process_before_deadline(
        base,
        install,
        environment,
        timeout_seconds,
        "offline npm uninstall",
        deadline,
    )
    if outcome["exitCode"] != 0:
        fail("NPM_UNINSTALL_FAILED", "offline npm uninstall returned nonzero")
    if os.path.lexists(command) or meta_root.exists() or platform_root.exists():
        fail(
            "NPM_UNINSTALL_INCOMPLETE",
            "npm uninstall retained a launcher or package root",
        )
    snapshots = install / "npm-package-snapshots"
    platform_tarball = snapshots / f"openprose-prose-cli-{platform_value}-{version}.tgz"
    meta_tarball = snapshots / f"openprose-prose-cli-{version}.tgz"
    install_argv = [
        current_npm["command"],
        "install",
        "--global",
        "--offline",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--loglevel=error",
        "--prefix",
        str(prefix),
        str(platform_tarball),
        str(meta_tarball),
    ]
    outcome = benchmark.run_process_before_deadline(
        install_argv,
        install,
        environment,
        timeout_seconds,
        "offline npm reinstall",
        deadline,
    )
    if outcome["exitCode"] != 0:
        fail("NPM_REINSTALL_FAILED", "offline npm reinstall returned nonzero")
    identity = validate_reinstalled_npm(benchmark, install, raw_report)
    npm_installation = next(
        record
        for record in raw_report["installations"]
        if record.get("surface") == "npm-launcher"
    )
    command_path = Path(identity["command"])
    meta_root = benchmark.npm_layout(prefix, platform_value)[1]
    allowed_links = (
        {command_path: meta_root / "bin" / "prose.js"}
        if command_path.is_symlink()
        else {}
    )
    reinstalled_tree_sha256 = benchmark.verify_installed_tree(
        prefix,
        npm_installation["treeIdentity"],
        allowed_links,
    )
    workspace = install / "workspace"
    run_environment = benchmark.clean_environment(
        install / "lifecycle-run-environment", [node_path.parent]
    )
    invocation = benchmark.run_process_before_deadline(
        [str(identity["command"]), *benchmark.INVOCATION_ARGV],
        workspace,
        run_environment,
        timeout_seconds,
        "reinstalled npm launcher invocation",
        deadline,
    )
    if invocation["exitCode"] != 0 or invocation["stderr"]:
        fail(
            "NPM_REINSTALL_UNUSABLE", "reinstalled npm launcher did not settle cleanly"
        )
    task = {
        "schema": "openprose.task-envelope/1",
        "argv": list(benchmark.FORWARDED_TASK_ARGV),
        "interactionMode": "non-interactive",
    }
    task_digest = benchmark.sha256_bytes(benchmark.canonical_json(task))
    benchmark.validate_runner_result(
        invocation["stdout"], "bun", package_context["release"], task_digest
    )
    benchmark.assert_package_output_unchanged(package_context)
    remaining(deadline, "after npm reinstall verification")
    return {
        "status": "passed",
        "uninstallExitCode": 0,
        "reinstallExitCode": 0,
        "invocationExitCode": 0,
        "binarySha256": identity["binarySha256"],
        "launcherSourceSha256": identity["launcherSourceSha256"],
        "treeSha256": reinstalled_tree_sha256,
        "lifecycleScripts": "disabled",
        "registryMode": "offline-with-loopback-invalid-registry",
    }


def run_lifecycle(
    package_output: Path,
    work_root: Path,
    *,
    timeout_seconds: float = 10.0,
    deadline_monotonic: float,
    benchmark_module: Any | None = None,
) -> dict[str, Any]:
    if os.name == "nt":
        fail(
            "PLATFORM_UNSUPPORTED",
            "Windows lifecycle requires native Job Object containment before any process spawn",
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or not 1 <= float(timeout_seconds) <= 120
    ):
        fail("ARGUMENT_INVALID", "timeout_seconds must be finite and between 1 and 120")
    deadline = validate_deadline(deadline_monotonic)
    benchmark = benchmark_module if benchmark_module is not None else load_benchmark()
    remaining(deadline, "before source package verification")
    try:
        source_context = benchmark.verify_package_output(package_output)
    except Exception as error:
        fail(
            getattr(error, "code", "PACKAGE_MALFORMED"),
            f"package verification failed: {error}",
        )
    remaining(deadline, "after source package verification")
    root = prepare_owned_root(work_root)
    package = root / "package-snapshot"
    package_context = snapshot_package(benchmark, source_context, package)
    install = root / "fresh-install"
    try:
        raw_report = benchmark.run_benchmark(
            package,
            install,
            trials=1,
            timeout_seconds=float(timeout_seconds),
            deadline_monotonic=deadline,
        )
        analysis = benchmark.analyse_report(raw_report)
    except Exception as error:
        fail(
            getattr(error, "code", "FRESH_INSTALL_FAILED"),
            f"fresh installed-package lifecycle failed: {error}",
        )
    if (
        raw_report.get("schema") != "openprose.installed-package-benchmark/1"
        or analysis.get("schema") != "openprose.installed-package-benchmark-analysis/1"
        or raw_report.get("measurementPlan", {}).get("trials") != 1
        or raw_report.get("measurementPlan", {}).get("deadlineApplied") is not True
    ):
        fail("FRESH_INSTALL_INVALID", "fresh installed-package evidence is incomplete")
    checks: list[dict[str, Any]] = [
        {
            "name": "fresh-install",
            "status": "passed",
            "surfaces": ["direct-rust", "direct-bun", "npm-launcher"],
            "invocations": 3,
        }
    ]
    before_reinstall = tree_identity(install)
    if active_processes(benchmark):
        fail("PROCESS_UNSETTLED", "fresh installation left an owned process active")
    try:
        benchmark.run_benchmark(
            package,
            install,
            trials=1,
            timeout_seconds=float(timeout_seconds),
            deadline_monotonic=deadline,
        )
    except Exception as error:
        if getattr(error, "code", None) != "INSTALL_ROOT_UNSAFE":
            fail(
                "NON_OVERWRITE_INVALID",
                f"repeat install produced {getattr(error, 'code', None)}",
            )
    else:
        fail("NON_OVERWRITE_FAILED", "repeat installation overwrote an existing root")
    if tree_identity(install) != before_reinstall or active_processes(benchmark):
        fail(
            "NON_OVERWRITE_MUTATED",
            "repeat installation changed the retained install tree",
        )
    checks.append(
        {
            "name": "non-overwrite-refusal",
            "status": "passed",
            "errorCode": "INSTALL_ROOT_UNSAFE",
            "treeSha256": before_reinstall,
        }
    )
    try:
        npm_check = npm_uninstall_reinstall(
            benchmark,
            package_context,
            install,
            raw_report,
            deadline=deadline,
            timeout_seconds=float(timeout_seconds),
        )
    except LifecycleError:
        raise
    except Exception as error:
        fail(
            getattr(error, "code", "NPM_LIFECYCLE_FAILED"),
            f"npm uninstall/reinstall lifecycle failed: {error}",
        )
    checks.append({"name": "npm-uninstall-reinstall", **npm_check})
    artifacts = package_context["artifacts"]
    standalone = next(
        name
        for name, record in artifacts.items()
        if record["implementation"] == "rust" and record["kind"] == "standalone-archive"
    )
    npm_tarball = next(
        name for name, record in artifacts.items() if record["kind"] == "npm-meta"
    )
    standalone_check = probe_corrupted_member(
        benchmark,
        package_context,
        root / "corrupt-standalone",
        standalone,
        deadline_monotonic=deadline,
        timeout_seconds=float(timeout_seconds),
    )
    checks.append({"name": "corrupt-standalone-archive", **standalone_check})
    npm_corrupt_check = probe_corrupted_member(
        benchmark,
        package_context,
        root / "corrupt-npm",
        npm_tarball,
        deadline_monotonic=deadline,
        timeout_seconds=float(timeout_seconds),
    )
    checks.append({"name": "corrupt-npm-tarball", **npm_corrupt_check})
    try:
        benchmark.assert_package_output_unchanged(source_context)
        benchmark.assert_package_output_unchanged(package_context)
    except Exception as error:
        fail(
            getattr(error, "code", "PACKAGE_MUTATED"),
            f"package changed during lifecycle: {error}",
        )
    if active_processes(benchmark):
        fail("PROCESS_UNSETTLED", "lifecycle completed with an owned process active")
    remaining(deadline, "before lifecycle report return")
    return {
        "schema": REPORT_SCHEMA,
        "status": "passed-provider-free-development-lifecycle",
        "packageIdentity": raw_report["packageIdentity"],
        "checks": checks,
        "claims": {
            "providerCalls": "none",
            "semanticEvaluation": "not-performed",
            "releaseEligible": False,
            "publicationAuthorized": False,
            "networkIsolation": "not-enforced",
            "detachedDescendantContainment": "not-enforced",
            "globalMutationMonitoring": "not-performed",
            "packageInputsUnchanged": True,
            "configuredWriteRoot": "owned-work-root",
        },
    }


class ClosedParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        fail("ARGUMENT_INVALID", message)


def parser() -> argparse.ArgumentParser:
    result = ClosedParser(description=__doc__)
    result.add_argument("--packages", type=Path, required=True)
    result.add_argument("--work-root", type=Path, required=True)
    result.add_argument("--timeout-seconds", type=float, default=10.0)
    result.add_argument("--budget-seconds", type=float, default=300.0)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        options = parser().parse_args(argv)
        if (
            not math.isfinite(options.budget_seconds)
            or not 5 <= options.budget_seconds <= 1_800
        ):
            fail(
                "ARGUMENT_INVALID",
                "budget-seconds must be finite and between 5 and 1800",
            )
        report = run_lifecycle(
            options.packages,
            options.work_root,
            timeout_seconds=options.timeout_seconds,
            deadline_monotonic=time.monotonic() + options.budget_seconds,
        )
        sys.stdout.buffer.write(canonical_json(report))
        return 0
    except LifecycleError as error:
        sys.stderr.buffer.write(
            canonical_json(
                {
                    "schema": ERROR_SCHEMA,
                    "code": error.code,
                    "message": error.message,
                    "releaseEligible": False,
                    "publicationAuthorized": False,
                }
            )
        )
        return 1
    except Exception:
        sys.stderr.buffer.write(
            canonical_json(
                {
                    "schema": ERROR_SCHEMA,
                    "code": "INTERNAL_ERROR",
                    "message": "package lifecycle failed at an internal closed boundary",
                    "releaseEligible": False,
                    "publicationAuthorized": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
