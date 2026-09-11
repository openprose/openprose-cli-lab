#!/usr/bin/env python3
"""Verify a target-bound functional-alpha live evidence matrix."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
SURFACES = ("rust", "bun", "npm")
HARNESS_ORDER = ("prime", "omp", "codex", "claude")
TARGET_HARNESS_SUPPORT = {
    "darwin-arm64": HARNESS_ORDER,
    "darwin-x64": ("codex",),
    "linux-x64-gnu": ("codex", "omp"),
    "linux-arm64-gnu": ("codex",),
}
TARGET_SURFACE_SUPPORT = {target: SURFACES for target in TARGET_HARNESS_SUPPORT}
SHA256 = __import__("re").compile(r"^[0-9a-f]{64}$")
COMMIT_ID = __import__("re").compile(r"^[A-Za-z0-9._+-]{1,128}$")
PLATFORM_ID = __import__("re").compile(
    r"^(?:darwin-(?:arm64|x64)|linux-(?:arm64|x64)-(?:gnu|musl)|win32-(?:arm64|x64))$"
)
CLAIMS = {
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
}
CONFIGURATION = {
    "xdgConfigHomeOwned": True,
    "startedEmpty": True,
    "selectionPathContained": True,
    "ownedTemporaryRootRemoved": True,
}
TERMINAL = {
    "classification": "success",
    "transportCompleted": True,
    "terminalEventObserved": True,
    "exitCode": 0,
    "signal": None,
}


class MatrixError(RuntimeError):
    pass


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "openprose_live_alpha_runner", HERE / "run.py"
    )
    if spec is None or spec.loader is None:
        raise MatrixError("live evidence runner could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def read_evidence(path: Path) -> tuple[dict[str, Any], str]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MatrixError(f"evidence is unavailable: {path}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MatrixError(f"evidence must be a non-symlink regular file: {path}")
    if not 0 < metadata.st_size <= MAX_EVIDENCE_BYTES:
        raise MatrixError(
            f"evidence size is outside 1..{MAX_EVIDENCE_BYTES} bytes: {path}"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise MatrixError(
            f"evidence could not be opened safely: {path}: {error}"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise MatrixError(f"evidence changed before it was opened: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(
                descriptor, min(1024 * 1024, MAX_EVIDENCE_BYTES + 1 - total)
            )
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > MAX_EVIDENCE_BYTES:
                raise MatrixError(
                    f"evidence exceeds {MAX_EVIDENCE_BYTES} bytes: {path}"
                )
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or total != opened.st_size:
            raise MatrixError(f"evidence changed while being read: {path}")
        encoded = b"".join(chunks)
    finally:
        os.close(descriptor)

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for name, item in pairs:
            if name in value:
                raise MatrixError(f"evidence contains a duplicate JSON key: {path}")
            value[name] = item
        return value

    try:
        parsed = json.loads(encoded.decode("utf-8"), object_pairs_hook=no_duplicates)
    except MatrixError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MatrixError(f"evidence is not one UTF-8 JSON value: {path}") from error
    if not isinstance(parsed, dict):
        raise MatrixError(f"evidence root must be an object: {path}")
    return parsed, hashlib.sha256(encoded).hexdigest()


def exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise MatrixError(f"{label} has an unsupported shape")
    return value


def sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise MatrixError(f"{label} is not a lowercase SHA-256 digest")
    return value


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MatrixError(f"{label} must be a nonempty string")
    return value


def file_identity(value: Any, label: str) -> dict[str, Any]:
    record = exact_keys(value, {"path", "byteLength", "sha256"}, label)
    nonempty(record["path"], f"{label}.path")
    if (
        isinstance(record["byteLength"], bool)
        or not isinstance(record["byteLength"], int)
        or record["byteLength"] < 1
    ):
        raise MatrixError(f"{label}.byteLength must be positive")
    sha(record["sha256"], f"{label}.sha256")
    return record


def content_identity(value: Any, label: str) -> dict[str, Any]:
    record = exact_keys(value, {"byteLength", "sha256"}, label)
    if (
        isinstance(record["byteLength"], bool)
        or not isinstance(record["byteLength"], int)
        or not 1 <= record["byteLength"] <= RUNNER.MAX_HARNESS_FILE_BYTES
    ):
        raise MatrixError(f"{label}.byteLength is outside the bounded range")
    sha(record["sha256"], f"{label}.sha256")
    return record


def harness_custody(value: Any, harness: str, label: str) -> dict[str, Any]:
    record = exact_keys(
        value,
        {
            "schema",
            "harness",
            "commandName",
            "route",
            "distributionKind",
            "launcherKind",
            "entrypoint",
            "runtime",
            "packages",
            "dependencyGraph",
            "fileCount",
            "totalByteLength",
            "externalAuthorities",
            "aggregateSha256",
        },
        label,
    )
    if (
        record["schema"] != "openprose.harness-byte-custody/1"
        or record["harness"] != harness
        or record["commandName"] != RUNNER.HARNESS_COMMANDS[harness]
    ):
        raise MatrixError(f"{label} harness identity differs")
    route = exact_keys(record["route"], {"kind", "linkCount"}, f"{label}.route")
    if (
        route["kind"] not in {"direct", "symlink-chain"}
        or isinstance(route["linkCount"], bool)
        or not isinstance(route["linkCount"], int)
        or not 0 <= route["linkCount"] <= RUNNER.MAX_HARNESS_SYMLINK_HOPS
        or (route["kind"] == "direct") != (route["linkCount"] == 0)
    ):
        raise MatrixError(f"{label}.route is inconsistent")
    content_identity(record["entrypoint"], f"{label}.entrypoint")
    packages = record["packages"]
    if not isinstance(packages, list) or len(packages) > RUNNER.MAX_HARNESS_PACKAGES:
        raise MatrixError(f"{label}.packages exceeds the bounded set")
    package_sort_keys: list[tuple[bool, str, str, str]] = []
    for index, package in enumerate(packages):
        item = exact_keys(
            package,
            {"name", "version", "root", "manifest", "tree"},
            f"{label}.packages[{index}]",
        )
        name = nonempty(item["name"], f"{label}.packages[{index}].name")
        version = nonempty(item["version"], f"{label}.packages[{index}].version")
        if RUNNER.SEMVER.fullmatch(version) is None:
            raise MatrixError(f"{label}.packages[{index}].version is invalid")
        content_identity(item["manifest"], f"{label}.packages[{index}].manifest")
        tree = exact_keys(
            item["tree"],
            {"memberCount", "totalByteLength", "sha256"},
            f"{label}.packages[{index}].tree",
        )
        if any(
            isinstance(tree[key], bool) or not isinstance(tree[key], int)
            for key in ("memberCount", "totalByteLength")
        ) or not (
            1 <= tree["memberCount"] <= RUNNER.MAX_HARNESS_FILES
            and 1 <= tree["totalByteLength"] <= RUNNER.MAX_HARNESS_TOTAL_BYTES
        ):
            raise MatrixError(f"{label}.packages[{index}].tree is outside bounds")
        tree_digest = sha(tree["sha256"], f"{label}.packages[{index}].tree.sha256")
        if not isinstance(item["root"], bool):
            raise MatrixError(f"{label}.packages[{index}].root must be boolean")
        package_sort_keys.append((not item["root"], name, version, tree_digest))
    if package_sort_keys != sorted(package_sort_keys):
        raise MatrixError(f"{label}.packages is not canonically ordered")
    distribution = record["distributionKind"]
    if distribution == "native":
        if (
            record["launcherKind"] != "native"
            or record["runtime"] is not None
            or packages
            or record["dependencyGraph"] is not None
        ):
            raise MatrixError(f"{label} native distribution is inconsistent")
    elif distribution in {"npm-package", "dev-shell-package"}:
        expected = RUNNER.HARNESS_PACKAGES.get(harness)
        expected_launcher = (
            "shell-shebang" if distribution == "dev-shell-package" else "env-shebang"
        )
        if (
            expected is None
            or record["launcherKind"] != expected_launcher
            or not packages
            or (distribution == "dev-shell-package" and harness != "omp")
        ):
            raise MatrixError(f"{label} package distribution is inconsistent")
        runtime = exact_keys(
            record["runtime"],
            {"name", "version", "launcher", "executable"},
            f"{label}.runtime",
        )
        if runtime["name"] != expected[1]:
            raise MatrixError(f"{label}.runtime differs from the harness package")
        if RUNNER.SEMVER.fullmatch(runtime["version"]) is None:
            raise MatrixError(f"{label}.runtime.version is invalid")
        if runtime["name"] == "bun" and tuple(
            int(part) for part in runtime["version"].split(".")[:3]
        ) < (1, 3, 14):
            raise MatrixError(f"{label}.runtime.version is too old for OMP")
        launcher = exact_keys(
            runtime["launcher"],
            {"name", "executable"},
            f"{label}.runtime.launcher",
        )
        expected_interpreter = "sh" if distribution == "dev-shell-package" else "env"
        if launcher["name"] != expected_interpreter:
            raise MatrixError(f"{label}.runtime.launcher differs")
        content_identity(launcher["executable"], f"{label}.runtime.launcher.executable")
        content_identity(runtime["executable"], f"{label}.runtime.executable")
        graph = exact_keys(
            record["dependencyGraph"],
            {"edgeCount", "missingOptionalCount", "sha256"},
            f"{label}.dependencyGraph",
        )
        if any(
            isinstance(graph[key], bool)
            or not isinstance(graph[key], int)
            or graph[key] < 0
            for key in ("edgeCount", "missingOptionalCount")
        ):
            raise MatrixError(f"{label}.dependencyGraph counts are invalid")
        sha(graph["sha256"], f"{label}.dependencyGraph.sha256")
        roots = [
            package
            for package in packages
            if package["name"] == expected[0] and package["root"]
        ]
        if len(roots) != 1:
            raise MatrixError(f"{label} root package is not unique")
    else:
        raise MatrixError(f"{label}.distributionKind is unsupported")
    if (
        isinstance(record["fileCount"], bool)
        or not isinstance(record["fileCount"], int)
        or not 1 <= record["fileCount"] <= RUNNER.MAX_HARNESS_FILES
        or isinstance(record["totalByteLength"], bool)
        or not isinstance(record["totalByteLength"], int)
        or not 1 <= record["totalByteLength"] <= RUNNER.MAX_HARNESS_TOTAL_BYTES
    ):
        raise MatrixError(f"{label} aggregate bounds are invalid")
    if record["externalAuthorities"] != [
        "dynamic-undeclared-imports",
        "native-os-loader-libraries",
        "interpreter-resource-files",
        "ambient-harness-config-plugins-skills",
        "cached-account-provider-state",
        "provider-side-model-routing",
    ]:
        raise MatrixError(f"{label}.externalAuthorities differs")
    expected_aggregate = hashlib.sha256(
        canonical_json(
            {key: item for key, item in record.items() if key != "aggregateSha256"}
        )
    ).hexdigest()
    if record["aggregateSha256"] != expected_aggregate:
        raise MatrixError(f"{label}.aggregateSha256 is not canonical")
    return record


def invocation_identity(value: Any, harness: str, label: str) -> dict[str, Any]:
    record = exact_keys(value, {"model", "authRoute"}, label)
    model = nonempty(record["model"], f"{label}.model")
    if RUNNER.MODEL_ID.fullmatch(model) is None:
        raise MatrixError(f"{label}.model is invalid")
    auth = exact_keys(
        record["authRoute"], {"profile", "category"}, f"{label}.authRoute"
    )
    profiles = RUNNER.AUTH_PROFILES[harness]
    if auth.get("profile") not in profiles or auth.get("category") != profiles.get(
        auth.get("profile")
    ):
        raise MatrixError(f"{label}.authRoute is unsupported")
    return record


def candidate_closure(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MatrixError(f"{label} has an unsupported shape")
    kind = value.get("kind")
    expected_keys = {"schema", "kind", "members"}
    expected_roles = ["executable"]
    if kind == "npm":
        expected_keys.add("npm")
        expected_roles = [
            "launcher",
            "meta-package-json",
            "platform-package-json",
            "native-executable",
        ]
    elif kind != "direct":
        raise MatrixError(f"{label}.kind is unsupported")
    record = exact_keys(value, expected_keys, label)
    if record["schema"] != "openprose.candidate-closure/1":
        raise MatrixError(f"{label}.schema is unsupported")
    members = record["members"]
    if not isinstance(members, list) or len(members) != len(expected_roles):
        raise MatrixError(f"{label}.members is not the closed ordered set")
    for index, (member, role) in enumerate(zip(members, expected_roles)):
        item = exact_keys(
            member,
            {"role", "path", "byteLength", "sha256"},
            f"{label}.members[{index}]",
        )
        if item["role"] != role:
            raise MatrixError(f"{label}.members is not the closed ordered set")
        nonempty(item["path"], f"{label}.members[{index}].path")
        if (
            isinstance(item["byteLength"], bool)
            or not isinstance(item["byteLength"], int)
            or item["byteLength"] < 1
        ):
            raise MatrixError(f"{label}.members[{index}].byteLength must be positive")
        sha(item["sha256"], f"{label}.members[{index}].sha256")
    if len({member["path"] for member in members}) != len(members):
        raise MatrixError(f"{label}.members contains duplicate paths")
    if kind == "npm":
        npm = exact_keys(
            record["npm"],
            {
                "platformId",
                "metaPackage",
                "metaVersion",
                "platformPackage",
                "platformVersion",
                "binaryRelativePath",
                "declaredBinaryByteLength",
                "declaredBinarySha256",
                "node",
            },
            f"{label}.npm",
        )
        platform_id = nonempty(npm["platformId"], f"{label}.npm.platformId")
        meta_version = nonempty(npm["metaVersion"], f"{label}.npm.metaVersion")
        expected_binary = (
            "bin/prose.exe" if platform_id.startswith("win32-") else "bin/prose"
        )
        if (
            PLATFORM_ID.fullmatch(platform_id) is None
            or RUNNER.SEMVER.fullmatch(meta_version) is None
            or npm["metaPackage"] != "@openprose/prose-cli"
            or npm["platformPackage"] != f"@openprose/prose-cli-{platform_id}"
            or npm["platformVersion"] != meta_version
            or npm["binaryRelativePath"] != expected_binary
        ):
            raise MatrixError(f"{label}.npm identity is inconsistent")
        node = exact_keys(
            npm["node"],
            {
                "commandPath",
                "path",
                "byteLength",
                "sha256",
                "platform",
                "architecture",
                "libc",
                "platformId",
            },
            f"{label}.npm.node",
        )
        nonempty(node["commandPath"], f"{label}.npm.node.commandPath")
        nonempty(node["path"], f"{label}.npm.node.path")
        if (
            isinstance(node["byteLength"], bool)
            or not isinstance(node["byteLength"], int)
            or node["byteLength"] < 1
        ):
            raise MatrixError(f"{label}.npm.node.byteLength must be positive")
        sha(node["sha256"], f"{label}.npm.node.sha256")
        try:
            observed_platform_id = RUNNER.node_platform_id(
                node["platform"], node["architecture"], node["libc"]
            )
        except (RUNNER.SmokeError, TypeError) as error:
            raise MatrixError(f"{label}.npm Node identity is inconsistent") from error
        if node["platformId"] != platform_id or observed_platform_id != platform_id:
            raise MatrixError(f"{label}.npm Node identity is inconsistent")
        if node["path"] in {member["path"] for member in members}:
            raise MatrixError(
                f"{label}.npm Node executable duplicates a candidate member"
            )
        native = members[3]
        if (
            npm["declaredBinaryByteLength"] != native["byteLength"]
            or npm["declaredBinarySha256"] != native["sha256"]
        ):
            raise MatrixError(
                f"{label}.npm integrity metadata differs from native executable"
            )
    return record


def candidate_closure_without_paths(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema": value["schema"],
        "kind": value["kind"],
        "members": [
            {
                "role": member["role"],
                "byteLength": member["byteLength"],
                "sha256": member["sha256"],
            }
            for member in value["members"]
        ],
    }
    if value["kind"] == "npm":
        npm = dict(value["npm"])
        node = npm["node"]
        npm["node"] = {
            key: node[key]
            for key in (
                "byteLength",
                "sha256",
                "platform",
                "architecture",
                "libc",
                "platformId",
            )
        }
        result["npm"] = npm
    return result


def target_execution(value: Any, label: str) -> dict[str, Any]:
    record = exact_keys(
        value,
        {"authority", "platformId", "platform", "architecture", "libc", "node"},
        label,
    )
    if record["authority"] != "exact-node-runtime-probe":
        raise MatrixError(f"{label}.authority is unsupported")
    try:
        observed_platform_id = RUNNER.node_platform_id(
            record["platform"], record["architecture"], record["libc"]
        )
    except (RUNNER.SmokeError, TypeError) as error:
        raise MatrixError(f"{label} identity is inconsistent") from error
    if (
        record["platformId"] != observed_platform_id
        or record["platformId"] not in TARGET_HARNESS_SUPPORT
    ):
        raise MatrixError(f"{label} identity is inconsistent or unsupported")
    node = exact_keys(
        record["node"],
        {"commandPath", "path", "byteLength", "sha256"},
        f"{label}.node",
    )
    nonempty(node["commandPath"], f"{label}.node.commandPath")
    nonempty(node["path"], f"{label}.node.path")
    if (
        isinstance(node["byteLength"], bool)
        or not isinstance(node["byteLength"], int)
        or node["byteLength"] < 1
    ):
        raise MatrixError(f"{label}.node.byteLength must be positive")
    sha(node["sha256"], f"{label}.node.sha256")
    return record


def target_execution_without_paths(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "authority": value["authority"],
        "platformId": value["platformId"],
        "platform": value["platform"],
        "architecture": value["architecture"],
        "libc": value["libc"],
        "node": {
            "byteLength": value["node"]["byteLength"],
            "sha256": value["node"]["sha256"],
        },
    }


def runner_identity(value: Any, label: str) -> dict[str, str]:
    record = exact_keys(value, {"name", "version", "commit"}, label)
    if (
        record["name"] not in {"rust", "bun"}
        or not isinstance(record["version"], str)
        or RUNNER.SEMVER.fullmatch(record["version"]) is None
        or not isinstance(record["commit"], str)
        or COMMIT_ID.fullmatch(record["commit"]) is None
    ):
        raise MatrixError(f"{label} identity is unsupported")
    return record  # type: ignore[return-value]


def image_identity(value: Any, label: str, *, doctor: bool = False) -> dict[str, Any]:
    keys = {"formatVersion", "version", "sha256"}
    if doctor:
        keys.add("releaseEligible")
    record = exact_keys(value, keys, label)
    if (
        record["formatVersion"] != "openprose.skill-runtime-image/1"
        or record["version"] != "echo-v0"
    ):
        raise MatrixError(f"{label} is not the provisional echo-v0 image")
    sha(record["sha256"], f"{label}.sha256")
    if doctor and not isinstance(record["releaseEligible"], bool):
        raise MatrixError(f"{label}.releaseEligible must be boolean")
    return record


def validate_cell(value: dict[str, Any]) -> None:
    exact_keys(
        value,
        {
            "schema",
            "status",
            "target",
            "surface",
            "candidate",
            "runner",
            "harness",
            "harnessCustody",
            "invocation",
            "program",
            "selection",
            "configuration",
            "doctor",
            "run",
            "claims",
        },
        "evidence",
    )
    if (
        value["schema"] != "openprose.functional-alpha-live-evidence/5"
        or value["status"] != "pass"
    ):
        raise MatrixError("evidence is not a passing functional-alpha v5 record")
    target = target_execution(value["target"], "target")
    surface = value["surface"]
    harness = value["harness"]
    if surface not in SURFACES or harness not in HARNESS_ORDER:
        raise MatrixError("evidence surface or harness is unsupported")
    custody = harness_custody(value["harnessCustody"], harness, "harnessCustody")
    invocation_identity(value["invocation"], harness, "invocation")
    closure = candidate_closure(value["candidate"], "candidate closure")
    if (surface == "npm") != (closure["kind"] == "npm"):
        raise MatrixError("surface and candidate closure kind differ")
    if closure["kind"] == "npm":
        npm_node = closure["npm"]["node"]
        if (
            closure["npm"]["platformId"] != target["platformId"]
            or any(
                npm_node[key] != target[key]
                for key in ("platform", "architecture", "libc", "platformId")
            )
            or any(
                npm_node[key] != target["node"][key]
                for key in ("commandPath", "path", "byteLength", "sha256")
            )
        ):
            raise MatrixError("npm candidate and target execution identities differ")
    file_identity(value["program"], "program")
    runner = runner_identity(value["runner"], "runner")
    expected_runner = "rust" if surface == "rust" else "bun"
    if runner["name"] != expected_runner:
        raise MatrixError("surface and runner identity differ")
    if value["claims"] != CLAIMS:
        raise MatrixError("evidence claims are unsupported")
    if value["configuration"] != CONFIGURATION:
        raise MatrixError("configuration isolation was not closed")

    selection = exact_keys(
        value["selection"], {"exitCode", "schema", "scope", "changed"}, "selection"
    )
    if (
        selection["exitCode"] != 0
        or selection["schema"] != "openprose.harness-selection/1"
        or selection["scope"] != "user"
        or not isinstance(selection["changed"], bool)
    ):
        raise MatrixError("persisted selection facts differ")

    adapter_id = RUNNER.ADAPTERS[harness]
    transport = RUNNER.TRANSPORTS[harness]
    doctor = exact_keys(
        value["doctor"],
        {
            "exitCode",
            "schema",
            "ready",
            "selectedHarness",
            "selectedHarnessVersion",
            "selectedTransport",
            "selectedAdapterId",
            "promptPlacement",
            "isolation",
            "authCategory",
            "billingOwner",
            "image",
        },
        "doctor",
    )
    if (
        doctor["exitCode"] != 0
        or doctor["schema"] != "openprose.doctor-report/1"
        or doctor["ready"] is not True
        or doctor["selectedHarness"] != harness
        or doctor["selectedTransport"] != transport
        or doctor["selectedAdapterId"] != adapter_id
        or doctor["authCategory"] != "harness-managed"
        or doctor["billingOwner"] != "user-provider"
    ):
        raise MatrixError("doctor facts differ from the declared cell")
    doctor_image = image_identity(doctor["image"], "doctor.image", doctor=True)
    image_contract = RUNNER.echo_image_contract()
    if {
        key: doctor_image[key] for key in ("formatVersion", "version", "sha256")
    } != image_contract["image"] or doctor_image["releaseEligible"] != image_contract[
        "releaseEligible"
    ]:
        raise MatrixError("doctor image differs from the frozen echo-v0 manifest")

    run = exact_keys(
        value["run"],
        {
            "exitCode",
            "schema",
            "adapterId",
            "adapterDescriptorSha256",
            "harnessVersion",
            "admittedVersions",
            "repairCommand",
            "transport",
            "promptPlacement",
            "isolation",
            "image",
            "deliveredImageSha256",
            "taskSha256",
            "cwdIdentitySha256",
            "terminal",
            "semantic",
            "billing",
            "durationMs",
        },
        "run",
    )
    if (
        run["exitCode"] != 0
        or run["schema"] != "openprose.runner-result/1"
        or run["adapterId"] != adapter_id
        or run["transport"] != transport
    ):
        raise MatrixError("run identity differs from the declared cell")
    harness_version = nonempty(run["harnessVersion"], "run.harnessVersion")
    try:
        admission = RUNNER.validate_harness_version(harness, harness_version)
    except RUNNER.SmokeError as error:
        raise MatrixError(str(error)) from error
    if (
        run["admittedVersions"] != list(admission.admitted_versions)
        or run["repairCommand"] != admission.repair_command
        or run["adapterDescriptorSha256"] != admission.recipe_digest
    ):
        raise MatrixError("run does not bind the frozen adapter recipe")
    if custody["distributionKind"] in {"npm-package", "dev-shell-package"}:
        root_name = RUNNER.HARNESS_PACKAGES[harness][0]
        roots = [
            package
            for package in custody["packages"]
            if package["root"] and package["name"] == root_name
        ]
        observed_versions = [
            match.group(0) for match in RUNNER.SEMVER.finditer(harness_version)
        ]
        if (
            len(roots) != 1
            or len(observed_versions) != 1
            or roots[0]["version"] != observed_versions[0]
        ):
            raise MatrixError("harness version differs from its package custody")
    recipe_digest = admission.recipe_digest
    recipe, independent_recipe_digest = RUNNER.adapter_recipe(harness)
    prompt_placement = (
        recipe.get("launch", {})
        .get("instructionPlacement", {})
        .get("manifestPlacementId")
    )
    isolation = recipe.get("isolation", {}).get("guarantee")
    if independent_recipe_digest != recipe_digest:
        raise MatrixError("adapter recipe custody differs")
    if (
        not isinstance(prompt_placement, str)
        or not isinstance(isolation, str)
        or doctor["promptPlacement"] != prompt_placement
        or run["promptPlacement"] != prompt_placement
        or doctor["isolation"] != isolation
        or run["isolation"] != isolation
    ):
        raise MatrixError("doctor/run capabilities differ from the frozen recipe")
    if doctor["selectedHarnessVersion"] != harness_version:
        raise MatrixError("doctor/run harness versions differ")
    run_image = image_identity(run["image"], "run.image")
    if {
        key: doctor_image[key] for key in ("formatVersion", "version", "sha256")
    } != run_image:
        raise MatrixError("doctor/run image identities differ")
    if run["deliveredImageSha256"] != image_contract["deliveredImageSha256"]:
        raise MatrixError(
            "delivered image digest differs from the frozen model-visible bytes"
        )
    for name in ("taskSha256", "cwdIdentitySha256", "deliveredImageSha256"):
        sha(run[name], f"run.{name}")
    if run["terminal"] != TERMINAL:
        raise MatrixError("terminal settlement is not closed success")
    semantic = exact_keys(
        run["semantic"],
        {"status", "terminalSchemaSha256", "terminalEnvelopeDigestSha256"},
        "run.semantic",
    )
    if semantic["status"] != "not-applicable":
        raise MatrixError("provisional evidence cannot claim semantic execution")
    sha(semantic["terminalSchemaSha256"], "run.semantic.terminalSchemaSha256")
    if semantic["terminalSchemaSha256"] != image_contract["terminalSchemaSha256"]:
        raise MatrixError(
            "terminal schema digest differs from the frozen echo-v0 manifest"
        )
    sha(
        semantic["terminalEnvelopeDigestSha256"],
        "run.semantic.terminalEnvelopeDigestSha256",
    )
    if run["billing"] != {"owner": "user-provider", "authCategory": "harness-managed"}:
        raise MatrixError("run billing boundary differs")
    if (
        isinstance(run["durationMs"], bool)
        or not isinstance(run["durationMs"], int)
        or run["durationMs"] < 0
    ):
        raise MatrixError("run.durationMs must be a nonnegative integer")


def require_one(values: Iterable[Any], label: str) -> Any:
    unique = {
        json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values
    }
    if len(unique) != 1:
        raise MatrixError(f"cross-cell {label} differs")
    return json.loads(next(iter(unique)))


def build_report(
    records: list[tuple[dict[str, Any], str]],
    required_surfaces: tuple[str, ...],
    target_platform: str = "darwin-arm64",
    required_harnesses: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not records:
        raise MatrixError("at least one evidence record is required")
    if target_platform not in TARGET_HARNESS_SUPPORT:
        raise MatrixError("target platform is unsupported")
    supported_surfaces = TARGET_SURFACE_SUPPORT[target_platform]
    supported_harnesses = TARGET_HARNESS_SUPPORT[target_platform]
    if (
        not required_surfaces
        or len(set(required_surfaces)) != len(required_surfaces)
        or any(surface not in supported_surfaces for surface in required_surfaces)
    ):
        raise MatrixError(
            "required surfaces must be a nonempty unique target-supported set"
        )
    if required_harnesses is None:
        required_harnesses = supported_harnesses
    if (
        not required_harnesses
        or len(set(required_harnesses)) != len(required_harnesses)
        or any(harness not in supported_harnesses for harness in required_harnesses)
    ):
        raise MatrixError(
            "required harnesses must be a nonempty unique target-supported set"
        )
    cells: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for value, evidence_digest in records:
        validate_cell(value)
        key = (value["surface"], value["harness"])
        if key in cells:
            raise MatrixError(f"duplicate matrix cell {key[0]}/{key[1]}")
        if key[0] not in required_surfaces:
            raise MatrixError(f"unexpected matrix surface {key[0]}")
        if key[1] not in required_harnesses:
            raise MatrixError(f"unexpected matrix harness {key[1]}")
        if value["target"]["platformId"] != target_platform:
            raise MatrixError(
                f"evidence target differs from matrix target: {key[0]}/{key[1]}"
            )
        cells[key] = (value, evidence_digest)
    expected = {
        (surface, harness)
        for surface in required_surfaces
        for harness in required_harnesses
    }
    missing = sorted(expected - set(cells))
    if missing:
        raise MatrixError(
            "matrix is incomplete: "
            + ", ".join(f"{surface}/{harness}" for surface, harness in missing)
        )

    ordered = [
        cells[(surface, harness)]
        for surface in required_surfaces
        for harness in required_harnesses
    ]
    values = [value for value, _ in ordered]
    common_target = require_one(
        (target_execution_without_paths(value["target"]) for value in values),
        "target execution identity",
    )
    common_program = require_one(
        (
            {
                "byteLength": value["program"]["byteLength"],
                "sha256": value["program"]["sha256"],
            }
            for value in values
        ),
        "program identity",
    )
    common_task = require_one(
        (value["run"]["taskSha256"] for value in values), "task digest"
    )
    common_image = require_one(
        (value["run"]["image"] for value in values), "image identity"
    )
    common_image_release_eligible = require_one(
        (value["doctor"]["image"]["releaseEligible"] for value in values),
        "image manifest release eligibility",
    )
    common_schema = require_one(
        (value["run"]["semantic"]["terminalSchemaSha256"] for value in values),
        "terminal schema digest",
    )
    common_terminal = require_one(
        (value["run"]["semantic"]["terminalEnvelopeDigestSha256"] for value in values),
        "terminal envelope digest",
    )
    common_build = require_one(
        (
            {"version": value["runner"]["version"], "commit": value["runner"]["commit"]}
            for value in values
        ),
        "runner build identity",
    )

    surface_identities = []
    for surface in required_surfaces:
        surface_values = [
            cells[(surface, harness)][0] for harness in required_harnesses
        ]
        candidate_with_paths = require_one(
            (value["candidate"] for value in surface_values),
            f"{surface} candidate closure",
        )
        candidate = candidate_closure_without_paths(candidate_with_paths)
        runner = require_one(
            (value["runner"] for value in surface_values), f"{surface} runner identity"
        )
        surface_identities.append(
            {"surface": surface, "candidateClosure": candidate, "runner": runner}
        )

    if "bun" in required_surfaces and "npm" in required_surfaces:
        bun_candidate = next(
            item["candidateClosure"]
            for item in surface_identities
            if item["surface"] == "bun"
        )
        npm_candidate = next(
            item["candidateClosure"]
            for item in surface_identities
            if item["surface"] == "npm"
        )
        bun_executable = bun_candidate["members"][0]
        npm_native = npm_candidate["members"][3]
        if {
            "byteLength": bun_executable["byteLength"],
            "sha256": bun_executable["sha256"],
        } != {
            "byteLength": npm_native["byteLength"],
            "sha256": npm_native["sha256"],
        }:
            raise MatrixError(
                "npm native executable differs from the Bun direct candidate"
            )

    harness_identities = []
    for harness in required_harnesses:
        harness_values = [cells[(surface, harness)][0] for surface in required_surfaces]
        custody = require_one(
            (value["harnessCustody"] for value in harness_values),
            f"{harness} byte custody",
        )
        invocation = require_one(
            (value["invocation"] for value in harness_values),
            f"{harness} invocation identity",
        )
        harness_identities.append(
            {
                "harness": harness,
                "custody": custody,
                "invocation": invocation,
                "adapterId": RUNNER.ADAPTERS[harness],
                "transport": RUNNER.TRANSPORTS[harness],
                "promptPlacement": require_one(
                    (value["run"]["promptPlacement"] for value in harness_values),
                    f"{harness} prompt placement",
                ),
                "isolation": require_one(
                    (value["run"]["isolation"] for value in harness_values),
                    f"{harness} isolation",
                ),
                "harnessVersion": require_one(
                    (value["run"]["harnessVersion"] for value in harness_values),
                    f"{harness} version",
                ),
                "admittedVersions": require_one(
                    (value["run"]["admittedVersions"] for value in harness_values),
                    f"{harness} admitted versions",
                ),
                "repairCommand": require_one(
                    (value["run"]["repairCommand"] for value in harness_values),
                    f"{harness} repair command",
                ),
                "adapterDescriptorSha256": require_one(
                    (
                        value["run"]["adapterDescriptorSha256"]
                        for value in harness_values
                    ),
                    f"{harness} recipe digest",
                ),
            }
        )

    rows = []
    for (surface, harness), (value, evidence_digest) in sorted(cells.items()):
        rows.append(
            {
                "surface": surface,
                "harness": harness,
                "targetPlatform": value["target"]["platformId"],
                "evidenceSha256": evidence_digest,
                "candidateClosure": candidate_closure_without_paths(value["candidate"]),
                "harnessCustodySha256": value["harnessCustody"]["aggregateSha256"],
                "invocation": value["invocation"],
                "runnerName": value["runner"]["name"],
                "harnessVersion": value["run"]["harnessVersion"],
                "admittedVersions": value["run"]["admittedVersions"],
                "repairCommand": value["run"]["repairCommand"],
                "durationMs": value["run"]["durationMs"],
            }
        )
    return {
        "schema": "openprose.functional-alpha-live-matrix/4",
        "status": "pass",
        "target": common_target,
        "requiredSurfaces": list(required_surfaces),
        "requiredHarnesses": list(required_harnesses),
        "cellCount": len(rows),
        "common": {
            "runnerBuild": common_build,
            "program": common_program,
            "taskSha256": common_task,
            "image": common_image,
            "imageManifestReleaseEligible": common_image_release_eligible,
            "semanticStatus": "not-applicable",
            "terminalSchemaSha256": common_schema,
            "terminalEnvelopeDigestSha256": common_terminal,
        },
        "surfaces": surface_identities,
        "harnesses": harness_identities,
        "cells": rows,
        "claims": {
            "authority": "aggregate-of-candidate-reported-smoke",
            "crossSurfaceEquality": "candidate-closure-digest-and-settlement",
            "cliSelectionConfigIsolation": "owned-temporary-root-observed",
            "harnessConfigurationIsolation": "recipe-reported-per-harness",
            "reliability": "not-measured",
            "failedAttemptRetention": "outside-this-pass-matrix",
            "strictVersionAdmission": "exact-recipe-list-revalidated",
            "targetBinding": "exact-node-runtime-probe-reauthenticated",
            "harnessByteBinding": (
                "independent-path-selection-and-bounded-declared-package-runtime-"
                "bytes-reauthenticated"
            ),
            "modelAndAuthRouteBinding": "driver-input-category-only",
            "ambientHarnessState": "external-unbound",
            "custodyThreatModel": "same-user-persistent-mutation-detection",
            "coverage": "exact-declared-target-surface-harness-product",
            "openProseExecuted": False,
            "semanticConformance": "not-applicable",
            "providerCredentialCharged": "unverified",
            "releaseEligible": False,
        },
    }


def parse_surfaces(value: str) -> tuple[str, ...]:
    surfaces = tuple(item.strip() for item in value.split(",") if item.strip())
    if (
        not surfaces
        or len(set(surfaces)) != len(surfaces)
        or any(item not in SURFACES for item in surfaces)
    ):
        raise argparse.ArgumentTypeError(
            "surfaces must be a unique comma-separated subset of rust,bun,npm"
        )
    return surfaces


def parse_harnesses(value: str) -> tuple[str, ...]:
    harnesses = tuple(item.strip() for item in value.split(",") if item.strip())
    if (
        not harnesses
        or len(set(harnesses)) != len(harnesses)
        or any(item not in HARNESS_ORDER for item in harnesses)
    ):
        raise argparse.ArgumentTypeError(
            "harnesses must be a unique comma-separated subset of "
            "prime,omp,codex,claude"
        )
    return harnesses


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("evidence", nargs="+", type=Path)
    result.add_argument(
        "--target-platform", choices=tuple(TARGET_HARNESS_SUPPORT), required=True
    )
    result.add_argument("--surfaces", type=parse_surfaces, default=SURFACES)
    result.add_argument(
        "--harnesses",
        type=parse_harnesses,
        help="target-supported subset; defaults to every supported harness",
    )
    result.add_argument("--out", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    try:
        records = [read_evidence(path.expanduser()) for path in options.evidence]
        report = build_report(
            records,
            options.surfaces,
            target_platform=options.target_platform,
            required_harnesses=options.harnesses,
        )
        encoded = canonical_json(report)
        if options.out is not None:
            output = options.out.expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(output, flags, 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(encoded)
        sys.stdout.buffer.write(encoded)
        return 0
    except (OSError, MatrixError) as error:
        print(f"functional-alpha-matrix: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
