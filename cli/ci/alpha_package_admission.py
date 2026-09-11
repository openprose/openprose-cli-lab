#!/usr/bin/env python3
"""Admit one exact functional-alpha package set without contacting a provider."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import time
from typing import Any, Mapping, Sequence


SCHEMA = "openprose.alpha-package-admission/1"
ERROR_SCHEMA = "openprose.alpha-package-admission-error/1"
HERE = Path(__file__).resolve().parent
CLI = HERE.parent
BENCHMARK = CLI / "benchmarks" / "installed" / "benchmark.py"
PACKAGE_LOCAL = CLI / "ci" / "package_local.py"
FAKE_HARNESS = (
    CLI / "conformance" / "adversarial" / "adapter-products" / "fake_live_harness.py"
)
ADAPTER_MANIFEST = (
    CLI / "shared" / "capabilities" / "adapters" / "functional-alpha.v1.json"
)
OMP_ADAPTER_RECIPE = (
    CLI / "shared" / "capabilities" / "adapters" / "recipes" / "omp-rpc.v1.json"
)
HELLO_EXAMPLE = CLI / "conformance" / "live-alpha" / "hello.prose.md"
HELLO_EXAMPLE_MEMBER = "examples/hello.prose.md"
TARGET_PLATFORMS = {
    "linux-x64": "linux-x64-gnu",
    "linux-arm64": "linux-arm64-gnu",
    "darwin-arm": "darwin-arm64",
    "darwin-x64": "darwin-x64",
}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ALPHA_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)" r"-alpha\.(0|[1-9][0-9]*)$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GUIDANCE_SHELL_VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}|\$([A-Z][A-Z0-9_]*)")
GUIDANCE_SHELL_ASSIGNMENT = re.compile(r"^\s*([A-Z][A-Z0-9_]*)=")
GUIDANCE_AMBIENT_SHELL_VARIABLES = frozenset({"HOME", "PWD"})
MAX_INPUT_BYTES = 4 * 1024 * 1024
ROOT_MARKER = ".openprose-alpha-package-admission-root"
SURFACES = ("direct-rust", "direct-bun", "npm-launcher")
FIXTURE_MODEL = "fixture/model"
OMP_CONFIG_BYTES = (
    b"retry:\n"
    b"  enabled: false\n"
    b"disabledProviders:\n"
    b"  - native\n"
    b"  - omp-plugins\n"
    b"  - claude\n"
    b"  - agent-plugins\n"
    b"  - claude-plugins\n"
    b"  - codex\n"
    b"  - gemini\n"
    b"  - opencode\n"
    b"  - cursor\n"
    b"  - windsurf\n"
    b"  - vscode\n"
    b"  - mcp-json\n"
)
OMP_BUN_RUNTIME_FIXTURE = (
    b"#!/bin/sh\n"
    b'if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then\n'
    b"  printf '1.3.14\\n'\n"
    b"  exit 0\n"
    b"fi\n"
    b"exit 64\n"
)
ADAPTERS = (
    {
        "harness": "prime",
        "adapterId": "prime/rpc",
        "executable": "prime-agent",
        "harnessVersion": "prime-agent 0.7.0",
        "transport": "rpc",
        "promptPlacement": "system-append",
        "isolation": {"rust": "advisory", "bun": "advisory"},
        "model": FIXTURE_MODEL,
        "authProfile": "prime-harness-login",
    },
    {
        "harness": "omp",
        "adapterId": "omp/rpc",
        "executable": "omp",
        "harnessVersion": "omp/18.0.9",
        "transport": "rpc",
        "promptPlacement": "system-append",
        "isolation": {"rust": "unsupported", "bun": "unsupported"},
        "model": FIXTURE_MODEL,
        "authProfile": "omp-harness-login",
    },
    {
        "harness": "codex",
        "adapterId": "codex/exec-json",
        "executable": "codex",
        "harnessVersion": "codex-cli 0.149.0-alpha.4.1",
        "transport": "exec-json",
        "promptPlacement": "user-prefix-framed",
        "isolation": {"rust": "unsupported", "bun": "unsupported"},
        "model": None,
        "authProfile": None,
    },
    {
        "harness": "claude",
        "adapterId": "claude/print-stream-json",
        "executable": "claude",
        "harnessVersion": "2.1.243 (Claude Code)",
        "transport": "print-stream-json",
        "promptPlacement": "system-append",
        "isolation": {"rust": "advisory", "bun": "advisory"},
        "model": None,
        "authProfile": None,
    },
)
ALPHA_HARNESS_SUPPORT = {
    "darwin-arm64": ("prime", "omp", "codex", "claude"),
    "darwin-x64": ("codex",),
    "linux-x64-gnu": ("codex", "omp"),
    "linux-arm64-gnu": ("codex",),
}
ALPHA_BUN_RUNTIME_BY_PLATFORM = {
    "darwin-arm64": {
        "compileTarget": "bun-darwin-arm64",
        "runtimeVariant": "native",
    },
    "darwin-x64": {
        "compileTarget": "bun-darwin-x64-baseline",
        "runtimeVariant": "baseline",
    },
    "linux-x64-gnu": {
        "compileTarget": "bun-linux-x64-baseline",
        "runtimeVariant": "baseline",
    },
    "linux-arm64-gnu": {
        "compileTarget": "bun-linux-arm64",
        "runtimeVariant": "native",
    },
}
HARNESS_ADMISSION = {
    "prime": {
        "admittedVersions": ("0.7.0", "0.8.1"),
        "repairPin": "0.8.1",
        "authProfile": "prime-harness-login",
    },
    "omp": {
        "admittedVersions": ("18.0.9",),
        "repairPin": "18.0.9",
        "authProfile": "omp-harness-login",
    },
    "codex": {
        "admittedVersions": ("0.149.0-alpha.4.1",),
        "repairPin": "0.149.0-alpha.4.1",
        "authProfile": None,
    },
    "claude": {
        "admittedVersions": ("2.1.243",),
        "repairPin": "2.1.243",
        "authProfile": None,
    },
}
PROVIDER_CREDENTIALS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
}
ALLOWED_OBSERVED_ENVIRONMENT_NAMES = {
    "ALL_PROXY",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "OPENPROSE_INVOCATION_ID",
    "OPENPROSE_RECURSION_TOKEN",
    "OPENPROSE_RUN_NONCE",
    "PATH",
    "PRIME_AGENT_TELEMETRY",
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "__CF_USER_TEXT_ENCODING",
}
CLAIMS = {
    "providerCalls": "none-provider-free-fixture",
    "semanticEvaluation": False,
    "programPortabilityEvaluation": False,
    "releaseEligible": False,
    "publicationAuthorized": False,
    "rankingProduced": False,
    "strictDescendantContainment": False,
    "runtimeNetworkIsolation": False,
    "candidateExecution": "performed-posix-functional-alpha-echo",
}
ALPHA_README_MARKERS = (
    b"Release channel: functional alpha",
    b"does not execute OpenProse programs",
    b"SHA256SUMS",
    b"missing or duplicate checksum entry",
    b"awk -v name=\"$ASSET\" '$2 == name' SHA256SUMS",
    b"examples/hello.prose.md",
    b"echo-v0 image asks the selected harness to echo",
    b"human-output safety policy may withhold task-bearing text",
    b"does not open or read the packaged file, execute this OpenProse contract",
    b"never opens or controls an interactive TUI",
    b"The run command contacts the selected provider and may incur charges under the signed-in account. The CLI cannot determine the account or billing route.",
    b"https://github.com/openprose/prose/issues/new?template=openprose-cli-bug.yml",
    b"https://github.com/openprose/prose/security/advisories/new",
    b"Do not include credentials, account identifiers, private paths, or raw provider output in a public report.",
)


def persisted_config(harness: str) -> bytes:
    adapter = next(
        (item for item in ADAPTERS if item["harness"] == harness),
        None,
    )
    if adapter is None:
        fail("ADAPTER_MANIFEST_MISMATCH", "persisted harness is not admitted")
    lines = []
    if adapter["authProfile"] is not None:
        lines.append(f'auth_profile = "{adapter["authProfile"]}"')
    lines.append(f'harness = "{harness}"')
    if adapter["model"] is not None:
        lines.append(f'model = "{adapter["model"]}"')
    return ("\n".join(lines) + "\n").encode("ascii")


def supported_adapters(platform_value: str) -> tuple[Mapping[str, Any], ...]:
    harnesses = ALPHA_HARNESS_SUPPORT.get(platform_value)
    if harnesses is None:
        fail("PLATFORM_UNSUPPORTED", "platform has no functional-alpha harness support")
    by_harness = {str(adapter["harness"]): adapter for adapter in ADAPTERS}
    try:
        return tuple(by_harness[harness] for harness in harnesses)
    except KeyError:
        fail("ADAPTER_MANIFEST_MISMATCH", "platform harness support is not closed")


def hello_example_bytes(benchmark: Any) -> bytes:
    return read_input(benchmark, HELLO_EXAMPLE, "Hello World example contract")


def reported_isolation(adapter: Mapping[str, Any], runner: str) -> str:
    isolation = adapter["isolation"]
    if not isinstance(isolation, dict) or runner not in isolation:
        fail("ADAPTER_MANIFEST_MISMATCH", "adapter isolation oracle is incomplete")
    return str(isolation[runner])


class AdmissionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str) -> None:
    raise AdmissionError(code, message)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_record(value: bytes) -> dict[str, Any]:
    return {"byteLength": len(value), "sha256": sha256(value)}


def strict_object(value: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                fail("INPUT_MALFORMED", f"{label} contains duplicate key {key!r}")
            result[key] = item
        return result

    try:
        parsed = json.loads(value.decode("utf-8"), object_pairs_hook=pairs)
    except AdmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail("INPUT_MALFORMED", f"{label} is not strict UTF-8 JSON: {error}")
    if not isinstance(parsed, dict):
        fail("INPUT_MALFORMED", f"{label} must be an object")
    return parsed


def exact_keys(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        fail("INPUT_MALFORMED", f"{label} has unknown or missing fields")


def validate_digest_record(
    value: Any, label: str, *, allow_empty: bool = True
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("REPORT_MISMATCH", f"{label} digest record is not an object")
    exact_keys(value, {"byteLength", "sha256"}, label)
    length = value.get("byteLength")
    digest = value.get("sha256")
    minimum = 0 if allow_empty else 1
    if (
        isinstance(length, bool)
        or not isinstance(length, int)
        or length < minimum
        or not isinstance(digest, str)
        or SHA256.fullmatch(digest) is None
        or (length == 0 and digest != sha256(b""))
    ):
        fail("REPORT_MISMATCH", f"{label} digest record is malformed")
    return value


def load_benchmark() -> Any:
    spec = importlib.util.spec_from_file_location(
        "openprose_alpha_package_benchmark", BENCHMARK
    )
    if spec is None or spec.loader is None:
        fail("BENCHMARK_UNAVAILABLE", "installed-package verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_package_renderer() -> Any:
    spec = importlib.util.spec_from_file_location(
        "openprose_alpha_package_renderer", PACKAGE_LOCAL
    )
    if spec is None or spec.loader is None:
        fail("PACKAGE_GUIDANCE_MISMATCH", "package guidance renderer is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_input(benchmark: Any, path: Path, label: str) -> bytes:
    try:
        return benchmark.safe_read(path, MAX_INPUT_BYTES)
    except benchmark.BenchmarkError as error:
        fail(error.code, f"{label}: {error.message}")


def image_identity(benchmark: Any, manifest_path: Path) -> tuple[dict[str, Any], bytes]:
    encoded = read_input(benchmark, manifest_path, "functional-alpha image manifest")
    manifest = strict_object(encoded, "functional-alpha image manifest")
    aggregate = manifest.get("aggregateSha256")
    if (
        manifest.get("schema") != "openprose.skill-runtime-image-manifest/1"
        or manifest.get("purpose") != "functional-alpha-placeholder"
        or manifest.get("releaseEligible") is not True
        or not isinstance(manifest.get("imageFormatVersion"), str)
        or not isinstance(manifest.get("imageVersion"), str)
        or not isinstance(aggregate, dict)
        or aggregate.get("algorithm") != "sha256-path-length-nul-v1"
        or not isinstance(aggregate.get("sha256"), str)
        or SHA256.fullmatch(aggregate["sha256"]) is None
    ):
        fail("IMAGE_MISMATCH", "image manifest is not the functional-alpha placeholder")
    return (
        {
            "formatVersion": manifest["imageFormatVersion"],
            "version": manifest["imageVersion"],
            "sha256": aggregate["sha256"],
            "manifestSha256": sha256(encoded),
            "releaseEligible": True,
            "purpose": "functional-alpha-placeholder",
        },
        encoded,
    )


def adapter_manifest_identity(benchmark: Any) -> dict[str, Any]:
    encoded = read_input(
        benchmark, ADAPTER_MANIFEST, "functional-alpha adapter manifest"
    )
    manifest = strict_object(encoded, "functional-alpha adapter manifest")
    adapters = manifest.get("adapters")
    expected_ids = [item["adapterId"] for item in ADAPTERS]
    if (
        manifest.get("schema") != "openprose.functional-alpha-adapter-oracle/1"
        or manifest.get("status") != "functional-alpha"
        or manifest.get("imageVersion") != "echo-v0"
        or manifest.get("semanticStatus") != "not-applicable"
        or manifest.get("fallback") != "forbidden"
        or not isinstance(adapters, list)
        or [
            item.get("adapterId") if isinstance(item, dict) else None
            for item in adapters
        ]
        != expected_ids
    ):
        fail("ADAPTER_MANIFEST_MISMATCH", "functional-alpha adapter manifest differs")
    omp_prerequisite: dict[str, str] | None = None
    for expected, observed in zip(ADAPTERS, adapters, strict=True):
        if (
            observed.get("selection") != "explicit"
            or observed.get("shell") is not False
            or observed.get("outerPty") is not False
            or observed.get("authCategory") != "harness-managed"
            or observed.get("billingOwner") != "user-provider"
            or observed.get("terminalCarriage") != "assistant-final-text"
            or not isinstance(observed.get("recipe"), str)
            or not observed["recipe"].startswith(
                "cli/shared/capabilities/adapters/recipes/"
            )
        ):
            fail(
                "ADAPTER_MANIFEST_MISMATCH",
                f"functional-alpha adapter control differs: {expected['adapterId']}",
            )
        if expected["adapterId"] == "omp/rpc":
            prerequisites = observed.get("runtimePrerequisites")
            if not isinstance(prerequisites, list) or len(prerequisites) != 1:
                fail(
                    "ADAPTER_MANIFEST_MISMATCH",
                    "OMP must declare exactly one runtime prerequisite",
                )
            prerequisite = prerequisites[0]
            if not isinstance(prerequisite, dict):
                fail(
                    "ADAPTER_MANIFEST_MISMATCH",
                    "OMP runtime prerequisite is malformed",
                )
            exact_keys(
                prerequisite,
                {"runtime", "versionRange", "repairCommand"},
                "OMP runtime prerequisite",
            )
            expected_prerequisite = {
                "runtime": "bun",
                "versionRange": ">=1.3.14",
                "repairCommand": "npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9",
            }
            if (
                prerequisite != expected_prerequisite
                or observed.get("repairCommand")
                != expected_prerequisite["repairCommand"]
            ):
                fail(
                    "ADAPTER_MANIFEST_MISMATCH",
                    "OMP runtime prerequisite or repair authority differs",
                )
            omp_prerequisite = dict(expected_prerequisite)

    if omp_prerequisite is None:
        fail("ADAPTER_MANIFEST_MISMATCH", "OMP runtime prerequisite is unavailable")
    recipe_encoded = read_input(benchmark, OMP_ADAPTER_RECIPE, "OMP adapter recipe")
    recipe = strict_object(recipe_encoded, "OMP adapter recipe")
    support = recipe.get("support")
    if (
        recipe.get("adapterId") != "omp/rpc"
        or not isinstance(support, dict)
        or support.get("runtimePrerequisites") != [omp_prerequisite]
        or support.get("repairCommand") != omp_prerequisite["repairCommand"]
    ):
        fail(
            "ADAPTER_MANIFEST_MISMATCH",
            "OMP recipe runtime prerequisite differs from alpha authority",
        )
    launch = recipe.get("launch")
    controls = launch.get("controls") if isinstance(launch, dict) else None
    overlay = controls.get("ownedConfigOverlay") if isinstance(controls, dict) else None
    expected_overlay = {
        "argvFlag": "--config",
        "value": "rendered-config-path",
        "encoding": "utf8",
        "mode": "0600",
        "bytes": OMP_CONFIG_BYTES.decode("utf-8"),
        "byteLength": len(OMP_CONFIG_BYTES),
        "sha256": sha256(OMP_CONFIG_BYTES),
        "precedence": "final-cli-overlay",
    }
    if overlay != expected_overlay:
        fail(
            "ADAPTER_MANIFEST_MISMATCH",
            "OMP recipe control overlay differs from alpha authority",
        )
    return {
        "path": str(ADAPTER_MANIFEST.relative_to(CLI.parent)),
        **digest_record(encoded),
        "adapterIds": expected_ids,
        "ompRuntimePrerequisite": omp_prerequisite,
        "ompRecipe": {
            "path": str(OMP_ADAPTER_RECIPE.relative_to(CLI.parent)),
            **digest_record(recipe_encoded),
        },
    }


def image_member(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str):
        fail("IMAGE_MISMATCH", f"{label} path is malformed")
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        fail("IMAGE_MISMATCH", f"{label} path is unsafe")
    candidate = root.joinpath(*pure.parts)
    try:
        resolved_root = root.resolve(strict=True)
        candidate.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError):
        fail("IMAGE_MISMATCH", f"{label} does not resolve inside the image")
    return candidate


def prepare_root(path: Path) -> Path:
    requested = Path(os.path.abspath(path))
    if os.path.lexists(requested):
        fail("WORK_ROOT_UNSAFE", "work root must not already exist")
    try:
        requested.mkdir(mode=0o700)
        marker = requested / ROOT_MARKER
        with marker.open("xb") as output:
            output.write(b"openprose-alpha-package-admission/1\n")
        marker.chmod(0o400)
    except OSError as error:
        fail("WORK_ROOT_UNSAFE", f"cannot create owned work root: {error}")
    return requested


def write_exclusive(path: Path, value: bytes, mode: int = 0o400) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
        try:
            view = memoryview(value)
            while view:
                written = os.write(descriptor, view)
                if written < 1:
                    fail("OUTPUT_FAILED", f"short write for {path}")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        path.chmod(mode)
    except AdmissionError:
        raise
    except OSError as error:
        fail("OUTPUT_FAILED", f"cannot write {path}: {error}")


def snapshot_package(benchmark: Any, context: Mapping[str, Any], target: Path) -> Any:
    try:
        target.mkdir(mode=0o700)
    except OSError as error:
        fail("OUTPUT_FAILED", f"cannot create package snapshot: {error}")
    for name, value in sorted(context["encoded"].items()):
        write_exclusive(target / name, value)
    sums = read_input(benchmark, Path(context["root"]) / "SHA256SUMS", "SHA256SUMS")
    write_exclusive(target / "SHA256SUMS", sums)
    try:
        return benchmark.verify_package_output(
            target, expected_platform=context["platform"], purpose="alpha-invariants"
        )
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)


def validate_omp_guidance_lines(
    encoded: bytes,
    *,
    source: str,
    prerequisite: Mapping[str, str],
    markdown: bool,
    guidance_expected: bool,
    command_expected: bool,
) -> None:
    """Bind OMP guidance to its canonical line, not an inert substring."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    if markdown:
        guidance = (
            "- OMP: exact admitted version is `18.0.9`; it requires Bun `1.3.14` "
            "or newer."
        )
    else:
        guidance = (
            "  OMP: exact admitted version is 18.0.9. It requires Bun 1.3.14 "
            "or newer."
        )
    expected = [guidance] if guidance_expected else []
    if command_expected:
        indent = "    " if markdown else "  "
        expected.append(f"{indent}{prerequisite['repairCommand']}")
    critical = tuple(
        line
        for line in lines
        if "OMP:" in line
        or "18.0.9" in line
        or "@oh-my-pi/pi-coding-agent" in line
        or "bun@" in line
    )
    if critical != tuple(expected):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} OMP runtime and repair guidance is not canonical",
        )


def validate_guidance_shell_variables(encoded: bytes, *, source: str) -> None:
    """Reject copy-paste guidance that depends on an undefined shell variable."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    defined = set(GUIDANCE_AMBIENT_SHELL_VARIABLES)
    for line in lines:
        for match in GUIDANCE_SHELL_VARIABLE.finditer(line):
            name = match.group(1) or match.group(2)
            if name not in defined:
                fail(
                    "PACKAGE_GUIDANCE_MISMATCH",
                    f"{source} uses unresolved shell variable {name}",
                )
        assignment = GUIDANCE_SHELL_ASSIGNMENT.match(line)
        if assignment is not None:
            defined.add(assignment.group(1))


NPM_PLATFORM_RESOLUTION_BLOCK_LINES = (
    "PLATFORM_ID=",
    "PLATFORM_ARCH=",
    'case "$(uname -s):$(uname -m)" in',
    "Linux:x86_64) PLATFORM_ARCH=linux-x64 ;;",
    "Linux:aarch64|Linux:arm64) PLATFORM_ARCH=linux-arm64 ;;",
    "Darwin:arm64) PLATFORM_ID=darwin-arm64 ;;",
    "Darwin:x86_64) PLATFORM_ID=darwin-x64 ;;",
    '*) echo "unsupported functional-alpha platform" >&2; exit 1 ;;',
    "esac",
    'if [ -n "$PLATFORM_ARCH" ]; then',
    'GLIBC_VERSION=$(getconf GNU_LIBC_VERSION 2>/dev/null) || { echo "unsupported functional-alpha platform: glibc could not be verified" >&2; exit 1; }',
    'case "$GLIBC_VERSION" in',
    '"glibc "[0-9]*.[0-9]*) ;;',
    '*) echo "unsupported functional-alpha platform: glibc could not be verified" >&2; exit 1 ;;',
    "esac",
    "GLIBC_NUMBER=${GLIBC_VERSION#glibc }",
    'case "$GLIBC_NUMBER" in',
    '*[!0-9.]*|.*|*.|*.*.*) echo "unsupported functional-alpha platform: glibc could not be verified" >&2; exit 1 ;;',
    "esac",
    'PLATFORM_ID="$PLATFORM_ARCH-gnu"',
    "fi",
)


def validate_npm_platform_guidance(encoded: bytes, *, source: str) -> None:
    """Require self-contained, fail-closed platform lifecycle commands."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    stripped = [line.strip() for line in lines]
    block = list(NPM_PLATFORM_RESOLUTION_BLOCK_LINES)
    block_starts = [
        index
        for index in range(len(stripped) - len(block) + 1)
        if stripped[index : index + len(block)] == block
    ]
    if len(block_starts) != 4:
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} platform resolution is not canonical",
        )
    required_resolved_fragments = (
        '"@openprose/prose-cli-$PLATFORM_ID@',
        'ASSET="openprose-prose-cli-$PLATFORM_ID-',
        '"./openprose-prose-cli-$PLATFORM_ID-',
        "@openprose/prose-cli-$PLATFORM_ID/bin/prose",
        '"@openprose/prose-cli-$PLATFORM_ID"',
    )
    fragment_positions: list[int] = []
    for fragment in required_resolved_fragments:
        matches = [index for index, line in enumerate(lines) if fragment in line]
        if len(matches) != 1:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} platform resolution leaves a noncanonical command",
            )
        fragment_positions.append(matches[0])
    block_ends = [start + len(block) for start in block_starts]
    repair, asset, offline, gatekeeper, uninstall = fragment_positions
    lifecycle_ordered = (
        block_ends[0] <= repair < block_starts[1]
        and block_ends[1] <= asset < offline < block_starts[2]
        and block_ends[2] <= gatekeeper < block_starts[3]
        and block_ends[3] <= uninstall
    )
    if "<platform>" in "\n".join(lines) or not lifecycle_ordered:
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} platform resolution does not precede each lifecycle command",
        )


def validate_standalone_uninstall_guidance(
    encoded: bytes,
    *,
    source: str,
    root_name: str,
    executable: str,
) -> None:
    """Bind standalone removal to one exact, revalidated archive root."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    refusal = 'echo "refusing standalone uninstall" >&2; exit 1'
    expected = [
        "/bin/sh -c '",
        "UNINSTALL_ROOT=",
        f'printf "%s" "Absolute physical extracted-root path ending in {root_name}: " >&2',
        f"IFS= read -r UNINSTALL_ROOT || {{ {refusal}; }}",
        'case "$UNINSTALL_ROOT" in',
        "/*) ;;",
        f"*) {refusal} ;;",
        "esac",
        f'test "$UNINSTALL_ROOT" != "/" || {{ {refusal}; }}',
        f'test "${{UNINSTALL_ROOT##*/}}" = "{root_name}" || {{ {refusal}; }}',
        f'test -d "$UNINSTALL_ROOT" && test ! -L "$UNINSTALL_ROOT" || {{ {refusal}; }}',
        f'UNINSTALL_CANONICAL=$(CDPATH= cd -P "$UNINSTALL_ROOT" 2>/dev/null && pwd -P) || {{ {refusal}; }}',
        f'test "$UNINSTALL_CANONICAL" = "$UNINSTALL_ROOT" || {{ {refusal}; }}',
        f'test -f "$UNINSTALL_ROOT/README.txt" && test ! -L "$UNINSTALL_ROOT/README.txt" || {{ {refusal}; }}',
        f'test -f "$UNINSTALL_ROOT/{executable}" && test ! -L "$UNINSTALL_ROOT/{executable}" && test -x "$UNINSTALL_ROOT/{executable}" || {{ {refusal}; }}',
        f'test -d "$UNINSTALL_ROOT/examples" && test ! -L "$UNINSTALL_ROOT/examples" || {{ {refusal}; }}',
        f'test -f "$UNINSTALL_ROOT/{HELLO_EXAMPLE_MEMBER}" && test ! -L "$UNINSTALL_ROOT/{HELLO_EXAMPLE_MEMBER}" || {{ {refusal}; }}',
        'rm -rf -- "$UNINSTALL_ROOT"',
        "'",
    ]
    stripped = [line.strip() for line in lines]
    starts = [
        index
        for index in range(len(stripped) - len(expected) + 1)
        if stripped[index : index + len(expected)] == expected
    ]
    if (
        len(starts) != 1
        or 'rm -rf -- "$PWD/' in "\n".join(lines)
        or "<absolute" in "\n".join(lines)
        or "replace-with" in "\n".join(lines)
    ):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} standalone uninstall is not the canonical exact-root block",
        )


def validate_npm_upgrade_guidance(encoded: bytes, *, source: str) -> None:
    """Bind npm upgrade input validation before prefix construction and npm."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    refusal = 'echo "refusing npm functional-alpha upgrade" >&2; exit 1'
    expected = [
        "/bin/sh -c '",
        "NEW_VERSION=",
        'printf "%s" "Exact newer functional-alpha version (X.Y.Z-alpha.N): " >&2',
        f"IFS= read -r NEW_VERSION || {{ {refusal}; }}",
        "NEW_CORE=${NEW_VERSION%-alpha.*}",
        "NEW_ALPHA_NUMBER=${NEW_VERSION##*-alpha.}",
        f'test "$NEW_CORE-alpha.$NEW_ALPHA_NUMBER" = "$NEW_VERSION" || {{ {refusal}; }}',
        "NEW_MAJOR=${NEW_CORE%%.*}",
        "NEW_REMAINDER=${NEW_CORE#*.}",
        f'test "$NEW_REMAINDER" != "$NEW_CORE" || {{ {refusal}; }}',
        "NEW_MINOR=${NEW_REMAINDER%%.*}",
        "NEW_PATCH=${NEW_REMAINDER#*.}",
        f'test "$NEW_PATCH" != "$NEW_REMAINDER" || {{ {refusal}; }}',
        f'case "$NEW_PATCH" in *.*) {refusal} ;; esac',
        "NEW_IDENTIFIER=",
        'for NEW_IDENTIFIER in "$NEW_MAJOR" "$NEW_MINOR" "$NEW_PATCH" "$NEW_ALPHA_NUMBER"; do',
        'case "$NEW_IDENTIFIER" in',
        f'""|*[!0-9]*) {refusal} ;;',
        "0|[1-9]|[1-9][0-9]*) ;;",
        f"*) {refusal} ;;",
        "esac",
        "done",
        'npm install --global --ignore-scripts --prefix "$HOME/.local/openprose-cli-$NEW_VERSION" "@openprose/prose-cli@$NEW_VERSION"',
        "'",
    ]
    stripped = [line.strip() for line in lines]
    starts = [
        index
        for index in range(len(stripped) - len(expected) + 1)
        if stripped[index : index + len(expected)] == expected
    ]
    if (
        len(starts) != 1
        or "replace-with-exact-version" in "\n".join(lines)
        or "NEW_VERSION='<" in "\n".join(lines)
    ):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} npm upgrade is not the canonical validated-input block",
        )


DOCUMENTED_JOURNEY_HEADINGS = {
    "prime": "Prime — macOS Apple silicon only:",
    "omp": "OMP — macOS Apple silicon or Linux x64 only:",
    "codex": "Codex — every supported functional-alpha platform:",
    "claude": "Claude — macOS Apple silicon only:",
}
DOCUMENTED_JOURNEY_INSTALLS = {
    "prime": (
        "curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh "
        "| sh -s -- 0.8.1"
    ),
    "omp": (
        "npm install --global bun@1.3.14 "
        "@oh-my-pi/pi-coding-agent@18.0.9"
    ),
    "codex": "npm install --global @openai/codex@0.149.0-alpha.4.1",
    "claude": "npm install --global @anthropic-ai/claude-code@2.1.243",
}
DOCUMENTED_AUTHENTICATION_BOUNDARY = (
    "Authentication is not automated. Complete the harness sign-in flow before "
    "you run `cli doctor` or `run`."
)
DOCUMENTED_PROVIDER_CHARGE_BOUNDARY = (
    "The run command contacts the selected provider and may incur charges under "
    "the signed-in account. The CLI cannot determine the account or billing route."
)
DOCUMENTED_HARNESS_DISPLAY_NAMES = {
    "prime": "Prime",
    "omp": "OMP",
    "codex": "Codex",
    "claude": "Claude",
}


def documented_harness_names(harnesses: Sequence[str]) -> str:
    names = [DOCUMENTED_HARNESS_DISPLAY_NAMES[harness] for harness in harnesses]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def validate_documented_alternative_runs(
    encoded: bytes,
    *,
    source: str,
    executable: str,
    example: str,
    harnesses: Sequence[str],
) -> None:
    """Bind every documented harness heading to one exact complete journey."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    expected_harnesses = tuple(harnesses)
    expected_set = set(expected_harnesses)
    if (
        len(expected_set) != len(expected_harnesses)
        or any(harness not in DOCUMENTED_JOURNEY_HEADINGS for harness in expected_set)
    ):
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} harness journey set is invalid")
    observed: dict[str, int] = {}
    heading_by_line = {
        index: harness
        for index, line in enumerate(lines)
        for harness, heading in DOCUMENTED_JOURNEY_HEADINGS.items()
        if line.strip() == heading
    }
    for index, harness in heading_by_line.items():
        if harness not in expected_set:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} contains an unsupported {harness} documented journey",
            )
        if harness in observed:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} duplicates the documented {harness} journey",
            )
        observed[harness] = index
    for harness in expected_harnesses:
        start = observed.get(harness)
        if start is None:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} documented {harness} journey is missing its exact heading",
            )
        end = min(
            (index for index in heading_by_line if index > start), default=len(lines)
        )
        block = lines[start + 1 : end]
        stripped = [line.strip() for line in block]
        expected_install = DOCUMENTED_JOURNEY_INSTALLS[harness]
        if (
            stripped.count(DOCUMENTED_AUTHENTICATION_BOUNDARY) != 1
            or stripped.count(DOCUMENTED_PROVIDER_CHARGE_BOUNDARY) != 1
            or stripped.count(expected_install) != 1
        ):
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} documented {harness} journey lacks its authentication boundary or exact install command",
            )
        if harness == "codex" and stripped.count("codex login") != 1:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} documented codex journey lacks its exact sign-in command",
            )
        selection = [executable, "cli", "harness", "use", harness]
        if harness in {"prime", "omp"}:
            selection.extend(
                [
                    "--model",
                    "openai-codex/gpt-5.4",
                    "--auth-profile",
                    f"{harness}-harness-login",
                ]
            )
        expected_commands = [
            selection,
            [executable, "cli", "doctor"],
            [executable, "run", example],
        ]
        parsed_commands: list[list[str]] = []
        command_indexes: list[int] = []
        for index, line in enumerate(block):
            if executable not in line:
                continue
            try:
                argv = shlex.split(line.strip(), posix=True)
            except ValueError:
                fail(
                    "PACKAGE_GUIDANCE_MISMATCH",
                    f"{source} documented {harness} journey command is malformed",
                )
            if argv and argv[0] == executable:
                parsed_commands.append(argv)
                command_indexes.append(index)
        install_index = stripped.index(expected_install)
        charge_index = stripped.index(DOCUMENTED_PROVIDER_CHARGE_BOUNDARY)
        prerequisite_indexes = [charge_index, install_index]
        if harness == "codex":
            prerequisite_indexes.append(stripped.index("codex login"))
        if (
            parsed_commands != expected_commands
            or not command_indexes
            or prerequisite_indexes != sorted(prerequisite_indexes)
            or prerequisite_indexes[-1] >= command_indexes[0]
        ):
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{source} documented {harness} journey does not bind install, selection, doctor, and the exact packaged example",
            )


def validate_packaged_guidance(
    benchmark: Any, context: Mapping[str, Any], version: str
) -> dict[str, dict[str, Any]]:
    """Require actionable, honest first-use guidance inside every user package."""

    try:
        payloads = benchmark.validate_package_payloads(context)
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    hello = hello_example_bytes(benchmark)
    platform_value = str(context["platform"])
    supported = ALPHA_HARNESS_SUPPORT.get(platform_value)
    if supported is None:
        fail("PACKAGE_GUIDANCE_MISMATCH", "package platform is omitted from the alpha")
    documented: dict[str, dict[str, Any]] = {}
    omp_prerequisite = adapter_manifest_identity(benchmark)["ompRuntimePrerequisite"]
    renderer = load_package_renderer()
    for implementation in ("rust", "bun"):
        extracted = payloads["extracted"][implementation]
        readme_name = f"{extracted['rootName']}/README.txt"
        readme = extracted["members"][readme_name][0]
        source = f"{implementation}-standalone/README.txt"
        validate_guidance_shell_variables(readme, source=source)
        validate_standalone_uninstall_guidance(
            readme,
            source=source,
            root_name=extracted["rootName"],
            executable="prose",
        )
        expected_readme = renderer.standalone_readme(
            mode="alpha",
            implementation=implementation,
            version=version,
            platform_identifier=platform_value,
            archive_name=extracted["artifactName"],
            root_name=extracted["rootName"],
            linux_runtime=context["release"]["linuxRuntime"],
        )
        if readme != expected_readme:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{implementation} standalone guidance differs from canonical authority",
            )
        hello_name = f"{extracted['rootName']}/{HELLO_EXAMPLE_MEMBER}"
        packaged_hello = extracted["members"].get(hello_name)
        if packaged_hello != (hello, 0o644):
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{implementation} standalone hello contract differs",
            )
        required = (
            *ALPHA_README_MARKERS,
            version.encode("ascii"),
            extracted["artifactName"].encode("ascii"),
            (
                "Functional-alpha harness support on this platform: "
                + documented_harness_names(supported)
            ).encode("ascii"),
            *(
                (
                    b"Prime: exact admitted versions are 0.7.0 and 0.8.1",
                    b"curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh -s -- 0.8.1",
                )
                if "prime" in supported
                else ()
            ),
            *(
                (
                    b"OMP: exact admitted version is 18.0.9",
                    b"requires Bun 1.3.14 or newer",
                    omp_prerequisite["repairCommand"].encode("ascii"),
                )
                if "omp" in supported
                else ()
            ),
            *(
                (b"Codex: exact admitted version is 0.149.0-alpha.4.1",)
                if "codex" in supported
                else ()
            ),
            *(
                (b"Claude: exact admitted version is 2.1.243",)
                if "claude" in supported
                else ()
            ),
            (f'"$PWD/{extracted["rootName"]}/prose" cli harness list').encode("ascii"),
            b"Upgrade:",
            b"Uninstall this exact extracted version:",
            *(
                (
                    b"cli harness use prime --model openai-codex/gpt-5.4 --auth-profile prime-harness-login",
                )
                if "prime" in supported
                else ()
            ),
            *(
                (
                    b"cli harness use omp --model openai-codex/gpt-5.4 --auth-profile omp-harness-login",
                )
                if "omp" in supported
                else ()
            ),
            *(
                (b"macOS 13 or newer",)
                if implementation == "bun"
                and str(context["platform"]).startswith("darwin-")
                else ()
            ),
            *(
                (
                    (
                        "Compile target: "
                        + ALPHA_BUN_RUNTIME_BY_PLATFORM[platform_value]["compileTarget"]
                    ).encode("ascii"),
                    (
                        "Runtime variant: "
                        + ALPHA_BUN_RUNTIME_BY_PLATFORM[platform_value][
                            "runtimeVariant"
                        ]
                    ).encode("ascii"),
                )
                if implementation == "bun"
                else ()
            ),
            *(
                (
                    b"ad-hoc signed and not notarized",
                    (
                        f'xattr -d com.apple.quarantine "$PWD/{extracted["rootName"]}/prose"'
                    ).encode("ascii"),
                )
                if platform_value.startswith("darwin-")
                else ()
            ),
        )
        if (
            any(marker not in readme for marker in required)
            or b"development artifact" in readme
        ):
            fail(
                "PACKAGE_GUIDANCE_MISMATCH",
                f"{implementation} standalone functional-alpha guidance is incomplete",
            )
        validate_omp_guidance_lines(
            readme,
            source=source,
            prerequisite=omp_prerequisite,
            markdown=False,
            guidance_expected="omp" in supported,
            command_expected="omp" in supported,
        )
        validate_documented_alternative_runs(
            readme,
            source=source,
            executable=f"$PWD/{extracted['rootName']}/prose",
            example=f"$PWD/{extracted['rootName']}/{HELLO_EXAMPLE_MEMBER}",
            harnesses=supported,
        )
        documented[f"direct-{implementation}"] = parse_documented_first_run(
            readme,
            source=source,
            executable=f"$PWD/{extracted['rootName']}/prose",
            example=f"$PWD/{extracted['rootName']}/{HELLO_EXAMPLE_MEMBER}",
        )

    meta_name = f"openprose-prose-cli-{version}.tgz"
    try:
        meta_members = benchmark.decode_archive_members(
            context["encoded"][meta_name], meta_name
        )
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    npm_readme = meta_members.get("package/README.md")
    if npm_readme is None:
        fail("PACKAGE_GUIDANCE_MISMATCH", "npm functional-alpha guidance is missing")
    validate_npm_platform_guidance(npm_readme[0], source="npm-meta/README.md")
    validate_npm_upgrade_guidance(npm_readme[0], source="npm-meta/README.md")
    validate_guidance_shell_variables(npm_readme[0], source="npm-meta/README.md")
    if npm_readme[0] != renderer.npm_readme("alpha", version):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            "npm functional-alpha guidance differs from canonical authority",
        )
    npm_hello = meta_members.get(f"package/{HELLO_EXAMPLE_MEMBER}")
    if npm_hello != (hello, 0o644):
        fail("PACKAGE_GUIDANCE_MISMATCH", "npm meta-package hello contract differs")
    required_npm = (
        *ALPHA_README_MARKERS,
        version.encode("ascii"),
        b"npm install --global --ignore-scripts",
        b"@openprose/prose-cli@",
        f'"$HOME/.local/openprose-cli-{version}/bin/prose" cli harness list'.encode(
            "ascii"
        ),
        b"macOS packages require macOS 13 or newer",
        b"bun-darwin-x64-baseline",
        b"bun-linux-x64-baseline",
        b"baseline CPU runtime variants",
        b"consumer compatibility floor is Node.js 22.22.3",
        b"release CI and admission use exactly Node.js 24.20.0",
        b"Prime: exact admitted versions are `0.7.0` and `0.8.1`",
        b"curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh -s -- 0.8.1",
        b"OMP: exact admitted version is `18.0.9`",
        b"requires Bun `1.3.14` or newer",
        omp_prerequisite["repairCommand"].encode("ascii"),
        b"Codex: exact admitted version is `0.149.0-alpha.4.1`",
        b"Claude: exact admitted version is `2.1.243`",
        b"exact functional-alpha allowlist",
        b"cli harness use prime --model openai-codex/gpt-5.4 --auth-profile prime-harness-login",
        b"cli harness use omp --model openai-codex/gpt-5.4 --auth-profile omp-harness-login",
        b"cli harness use claude",
        b"fully qualified model identifier supported by the selected harness and authentication route",
        b"The CLI does not verify the provider, account, or billing route",
        b"These profiles do not establish subscription billing",
        b"Windows | Omitted from the functional alpha",
        b"ad-hoc signed and not notarized",
        f'GATEKEEPER_PROSE="$HOME/.local/openprose-cli-{version}/lib/node_modules/@openprose/prose-cli-$PLATFORM_ID/bin/prose"'.encode(
            "ascii"
        ),
        b'xattr -d com.apple.quarantine "$GATEKEEPER_PROSE"',
        b"Registry install into a collision-safe, versioned prefix",
        b"Registry repair of the same version must name both the exact platform package and the meta package",
        f'"@openprose/prose-cli-$PLATFORM_ID@{version}" "@openprose/prose-cli@{version}"'.encode(
            "ascii"
        ),
        f'ASSET="openprose-prose-cli-$PLATFORM_ID-{version}.tgz"'.encode("ascii"),
        f'"./openprose-prose-cli-$PLATFORM_ID-{version}.tgz"'.encode("ascii"),
        b'"@openprose/prose-cli-$PLATFORM_ID"',
        b"Offline two-tarball repair or install",
        b"## Upgrade",
        b"## Uninstall",
    )
    if (
        any(marker not in npm_readme[0] for marker in required_npm)
        or b"development artifact" in npm_readme[0]
    ):
        fail("PACKAGE_GUIDANCE_MISMATCH", "npm functional-alpha guidance is incomplete")
    validate_omp_guidance_lines(
        npm_readme[0],
        source="npm-meta/README.md",
        prerequisite=omp_prerequisite,
        markdown=True,
        guidance_expected=True,
        command_expected=True,
    )
    validate_documented_alternative_runs(
        npm_readme[0],
        source="npm-meta/README.md",
        executable=f"$HOME/.local/openprose-cli-{version}/bin/prose",
        example=(
            f"$HOME/.local/openprose-cli-{version}/lib/node_modules/"
            f"@openprose/prose-cli/{HELLO_EXAMPLE_MEMBER}"
        ),
        harnesses=("prime", "omp", "codex", "claude"),
    )
    documented["npm-launcher"] = parse_documented_first_run(
        npm_readme[0],
        source="npm-meta/README.md",
        executable=f"$HOME/.local/openprose-cli-{version}/bin/prose",
        example=(
            f"$HOME/.local/openprose-cli-{version}/lib/node_modules/"
            f"@openprose/prose-cli/{HELLO_EXAMPLE_MEMBER}"
        ),
    )
    return documented


def parse_documented_first_run(
    encoded: bytes, *, source: str, executable: str, example: str
) -> dict[str, Any]:
    """Parse the copy-paste block as argv and refuse ambient command resolution."""

    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} is not UTF-8")
    try:
        start = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("First run with Codex 0.149.0-alpha.4.1")
            or line == "## First run with Codex 0.149.0-alpha.4.1"
        )
    except StopIteration:
        fail("PACKAGE_GUIDANCE_MISMATCH", f"{source} first-run command is missing")
    commands: list[list[str]] = []
    command_indexes: list[int] = []
    end = len(lines)
    for index, line in enumerate(lines[start + 1 :], start=start + 1):
        if not line.strip():
            if commands:
                end = index
                break
            continue
        if not line.startswith(("  ", "    ")):
            if commands:
                end = index
                break
            continue
        try:
            argv = shlex.split(line.strip(), posix=True)
        except ValueError:
            fail(
                "PACKAGE_GUIDANCE_MISMATCH", f"{source} first-run command is malformed"
            )
        if argv:
            commands.append(argv)
            command_indexes.append(index)
    prerequisite = "complete Codex sign-in before you continue"
    if prerequisite not in " ".join(lines[start : start + 3]):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} first-run Codex prerequisite is missing",
        )
    first_block = [line.strip() for line in lines[start + 1 : end]]
    if (
        first_block.count(DOCUMENTED_PROVIDER_CHARGE_BOUNDARY) != 1
        or not command_indexes
        or start
        + 1
        + first_block.index(DOCUMENTED_PROVIDER_CHARGE_BOUNDARY)
        >= command_indexes[0]
    ):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} first-run provider-cost boundary is missing or misplaced",
        )
    expected = [
        ["npm", "install", "--global", "@openai/codex@0.149.0-alpha.4.1"],
        ["codex", "login"],
        [executable, "cli", "harness", "list"],
        [executable, "cli", "harness", "use", "codex"],
        [executable, "cli", "doctor"],
        [executable, "run", example],
    ]
    if commands != expected or any(command[0] == "prose" for command in commands):
        fail(
            "PACKAGE_GUIDANCE_MISMATCH",
            f"{source} first-run command does not use the exact installed candidate",
        )
    return {"source": source, "commands": commands}


def validate_darwin_installed_signatures(
    benchmark: Any,
    installed: Mapping[str, Any],
    *,
    platform_value: str,
    timeout_seconds: float,
    deadline_monotonic: float,
) -> None:
    """Reverify every installed macOS executable after archive/npm installation."""

    if not platform_value.startswith("darwin-"):
        return
    codesign = Path("/usr/bin/codesign")
    if not codesign.is_file():
        fail("TOOL_UNAVAILABLE", "macOS alpha admission requires /usr/bin/codesign")
    try:
        _, _, npm_platform_root, _ = benchmark.npm_layout(
            Path(installed["npmPrefix"]), platform_value
        )
    except (KeyError, TypeError, ValueError, benchmark.BenchmarkError) as error:
        fail("INSTALL_FAILED", f"cannot resolve installed npm platform binary: {error}")
    targets = (
        ("rust", Path(installed["surfaces"]["direct-rust"]["executable"])),
        ("bun", Path(installed["surfaces"]["direct-bun"]["executable"])),
        ("npm Bun", Path(npm_platform_root) / "bin" / "prose"),
    )
    for label, executable in targets:
        try:
            outcome = benchmark.run_process_before_deadline(
                [str(codesign), "--verify", "--deep", "--strict", str(executable)],
                cwd=Path(installed["install"]),
                environment={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                timeout_seconds=min(timeout_seconds, 15),
                description=f"{label} installed macOS code-signature verification",
                deadline_monotonic=deadline_monotonic,
            )
        except benchmark.BenchmarkError as error:
            fail(error.code, error.message)
        if outcome.get("exitCode") != 0:
            fail(
                "CODE_SIGNATURE_INVALID",
                f"{label} installed binary failed macOS codesign --verify --deep --strict",
            )


def install_fixture(
    root: Path, source: bytes, surface: str, adapter: Mapping[str, Any]
) -> tuple[Path, Path, Path]:
    fixture_root = root / "fixture-harnesses" / surface / str(adapter["harness"])
    binary_root = fixture_root / "bin"
    try:
        binary_root.mkdir(parents=True, mode=0o700)
    except OSError as error:
        fail("FIXTURE_FAILED", f"cannot create fixture root: {error}")
    executable = binary_root / str(adapter["executable"])
    write_exclusive(executable, source, 0o500)
    if adapter.get("adapterId") == "omp/rpc":
        # The product admits OMP only after the first Bun on the same hostile
        # PATH reports a stable version at or above 1.3.14. Keep package
        # admission provider-free and self-contained: ambient Bun is neither
        # available nor an authority for this fixture journey.
        write_exclusive(binary_root / "bun", OMP_BUN_RUNTIME_FIXTURE, 0o500)
    shadow_marker = fixture_root / "ambient-prose-was-invoked"
    shadow = (
        "#!/bin/sh\n" f": > {shlex.quote(str(shadow_marker))}\n" "exit 97\n"
    ).encode("utf-8")
    write_exclusive(binary_root / "prose", shadow, 0o500)
    return binary_root, fixture_root / "observation.json", shadow_marker


def execute_documented_first_run(
    benchmark: Any,
    installed: Mapping[str, Any],
    *,
    surface: str,
    target: Mapping[str, Any],
    documented: Mapping[str, Any],
    observation_path: Path,
    shadow_marker: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    deadline: float,
) -> dict[str, Any]:
    commands = documented.get("commands")
    source = documented.get("source")
    if (
        not isinstance(commands, list)
        or len(commands) != 6
        or not isinstance(source, str)
    ):
        fail("PACKAGE_GUIDANCE_MISMATCH", "parsed first-run command closure differs")
    run_command = commands[-1]
    if (
        not isinstance(run_command, list)
        or len(run_command) != 3
        or run_command[1] != "run"
    ):
        fail("PACKAGE_GUIDANCE_MISMATCH", "parsed first-run command is malformed")
    if surface == "npm-launcher":
        _, meta_root, _, _ = benchmark.npm_layout(
            Path(installed["npmPrefix"]), str(installed["platform"])
        )
        packaged_example = meta_root / HELLO_EXAMPLE_MEMBER
    else:
        packaged_example = Path(target["executable"]).parent / HELLO_EXAMPLE_MEMBER
    if read_input(
        benchmark, packaged_example, f"{surface} documented packaged example"
    ) != hello_example_bytes(benchmark):
        fail("PACKAGE_GUIDANCE_MISMATCH", "documented packaged example differs")
    outcome = checked_step(
        benchmark,
        argv=[str(target["executable"]), "run", str(packaged_example)],
        workspace=installed["workspace"],
        environment=environment,
        timeout_seconds=timeout_seconds,
        label=f"functional-alpha {surface}/codex documented first run",
        deadline=deadline,
    )
    observation = strict_object(
        read_input(
            benchmark,
            observation_path,
            f"{surface}/codex documented first-run fixture observation",
        ),
        f"{surface}/codex documented first-run fixture observation",
    )
    if observation.get("adapterId") != "codex/exec-json" or os.path.lexists(
        shadow_marker
    ):
        fail(
            "EXECUTION_INVALID",
            f"{surface}/codex documented first run did not reach the exact fake harness",
        )
    try:
        observation_path.unlink()
    except OSError as error:
        fail(
            "FIXTURE_FAILED", f"cannot reset documented first-run observation: {error}"
        )
    return {
        "source": source,
        "command": ["$INSTALLED_CANDIDATE", "run", "$PACKAGED_EXAMPLE"],
        "shell": False,
        "hostilePathShadowed": True,
        "exitCode": outcome["exitCode"],
        "fixtureReached": True,
    }


def validate_result(
    result: Mapping[str, Any],
    *,
    adapter_contract: Mapping[str, Any],
    runner: str,
    version: str,
    source_sha: str,
    image: Mapping[str, Any],
    task_sha: str,
) -> dict[str, Any]:
    runner_value = result.get("runner")
    adapter = result.get("adapter")
    language_image = result.get("languageImage")
    terminal = result.get("terminal")
    semantic = result.get("semantic")
    billing = result.get("billing")
    digests = result.get("digests")
    image_keys = ("formatVersion", "version", "sha256")
    if (
        result.get("schema") != "openprose.runner-result/1"
        or runner_value != {"name": runner, "version": version, "commit": source_sha}
        or not isinstance(adapter, dict)
        or adapter.get("id") != adapter_contract["adapterId"]
        or adapter.get("harnessVersion") != adapter_contract["harnessVersion"]
        or not isinstance(language_image, dict)
        or {key: language_image.get(key) for key in image_keys}
        != {key: image[key] for key in image_keys}
        or not isinstance(digests, dict)
        or digests.get("taskSha256") != task_sha
        or not isinstance(terminal, dict)
        or terminal.get("classification") != "success"
        or terminal.get("transportCompleted") is not True
        or terminal.get("terminalEventObserved") is not True
        or not isinstance(semantic, dict)
        or semantic.get("status") != "not-applicable"
        or not isinstance(billing, dict)
        or billing.get("owner") != "user-provider"
        or result.get("runnerExitCode") != 0
    ):
        fail("EXECUTION_INVALID", f"{runner} functional-alpha result differs")
    return {
        "runner": dict(runner_value),
        "adapterId": adapter["id"],
        "harnessVersion": adapter["harnessVersion"],
        "image": {key: language_image[key] for key in image_keys},
        "taskSha256": task_sha,
        "terminal": {
            "classification": terminal["classification"],
            "transportCompleted": terminal["transportCompleted"],
            "terminalEventObserved": terminal["terminalEventObserved"],
        },
        "semanticStatus": semantic["status"],
        "billingOwner": billing["owner"],
        "runnerExitCode": result["runnerExitCode"],
    }


def validate_harness_selection(
    value: Mapping[str, Any], *, adapter: Mapping[str, Any], user_config: Path
) -> dict[str, Any]:
    if value != {
        "schema": "openprose.harness-selection/1",
        "harness": adapter["harness"],
        "scope": "user",
        "path": str(user_config),
        "changed": True,
    }:
        fail(
            "EXECUTION_INVALID",
            f"persisted {adapter['harness']} harness selection differs",
        )
    return {
        "schema": value["schema"],
        "harness": value["harness"],
        "scope": value["scope"],
        "path": "$USER_CONFIG",
        "changed": value["changed"],
    }


def validate_doctor_result(
    value: Mapping[str, Any],
    *,
    adapter: Mapping[str, Any],
    runner: str,
    version: str,
    source_sha: str,
    image: Mapping[str, Any],
    workspace: Path,
    user_config: Path,
) -> dict[str, Any]:
    configuration = value.get("configuration")
    values = configuration.get("values") if isinstance(configuration, dict) else None
    harness = values.get("harness") if isinstance(values, dict) else None
    model = values.get("model") if isinstance(values, dict) else None
    auth_profile = values.get("authProfile") if isinstance(values, dict) else None
    source = harness.get("source") if isinstance(harness, dict) else None
    configured_path = (
        configuration.get("userConfigPath") if isinstance(configuration, dict) else None
    )
    cwd = value.get("cwd")
    image_keys = ("formatVersion", "version", "sha256", "releaseEligible")
    try:
        cwd_matches = isinstance(cwd, str) and Path(cwd).resolve(
            strict=True
        ) == workspace.resolve(strict=True)
        config_matches = isinstance(configured_path, str) and Path(
            configured_path
        ).resolve(strict=True) == user_config.resolve(strict=True)
    except OSError:
        cwd_matches = False
        config_matches = False

    def user_value_matches(record: Any, expected: Any) -> bool:
        if not isinstance(record, dict) or record.get("value") != expected:
            return False
        record_source = record.get("source")
        if (
            not isinstance(record_source, dict)
            or record_source.get("kind") != "user-config"
        ):
            return False
        record_location = record_source.get("location")
        if not isinstance(record_location, str):
            return False
        location_path = re.sub(r":[0-9]+$", "", record_location)
        try:
            return Path(location_path).resolve(strict=True) == user_config.resolve(
                strict=True
            )
        except OSError:
            return False

    def default_value_matches(record: Any) -> bool:
        return (
            isinstance(record, dict)
            and record.get("value") is None
            and record.get("source") == {"kind": "default", "location": "built-in"}
        )

    location_matches = user_value_matches(harness, adapter["harness"])
    model_matches = (
        user_value_matches(model, adapter["model"])
        if adapter["model"] is not None
        else default_value_matches(model)
    )
    auth_profile_matches = (
        user_value_matches(auth_profile, adapter["authProfile"])
        if adapter["authProfile"] is not None
        else default_value_matches(auth_profile)
    )
    expected_runner = {"name": runner, "version": version, "commit": source_sha}
    expected_build = {"profile": "release", "testSeamsEnabled": False}
    if (
        value.get("schema") != "openprose.doctor-report/1"
        or value.get("runner") != expected_runner
        or value.get("build") != expected_build
        or value.get("ready") is not True
        or not cwd_matches
        or value.get("selectedHarness") != adapter["harness"]
        or value.get("selectedHarnessVersion") != adapter["harnessVersion"]
        or value.get("selectedTransport") != adapter["transport"]
        or value.get("selectedAdapterId") != adapter["adapterId"]
        or value.get("promptPlacement") != adapter["promptPlacement"]
        or value.get("isolation") != reported_isolation(adapter, runner)
        or value.get("authCategory") != "harness-managed"
        or value.get("billingOwner") != "user-provider"
        or not isinstance(value.get("image"), dict)
        or {key: value["image"].get(key) for key in image_keys}
        != {key: image[key] for key in image_keys}
        or value.get("problems") != []
        or not config_matches
        or not isinstance(harness, dict)
        or harness.get("value") != adapter["harness"]
        or not isinstance(source, dict)
        or source.get("kind") != "user-config"
        or not location_matches
        or not model_matches
        or not auth_profile_matches
    ):
        fail("EXECUTION_INVALID", f"{runner} persisted-harness doctor result differs")
    return {
        "runner": expected_runner,
        "build": expected_build,
        "ready": True,
        "selectedHarness": adapter["harness"],
        "selectedHarnessVersion": adapter["harnessVersion"],
        "selectedTransport": adapter["transport"],
        "selectedAdapterId": adapter["adapterId"],
        "promptPlacement": adapter["promptPlacement"],
        "isolation": reported_isolation(adapter, runner),
        "authCategory": "harness-managed",
        "billingOwner": "user-provider",
        "configurationHarness": {
            "value": adapter["harness"],
            "sourceKind": "user-config",
        },
        "problems": [],
    }


def checked_step(
    benchmark: Any,
    *,
    argv: list[str],
    workspace: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    label: str,
    deadline: float,
) -> Mapping[str, Any]:
    try:
        outcome = benchmark.run_process_before_deadline(
            argv, workspace, environment, timeout_seconds, label, deadline
        )
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    if outcome["exitCode"] != 0 or outcome["stderr"]:
        diagnostic = outcome["stderr"].decode("utf-8", "replace")[:1000]
        fail("EXECUTION_FAILED", f"{label} failed: {diagnostic}")
    return outcome


def step_record(
    outcome: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "exitCode": outcome["exitCode"],
        "stdout": digest_record(outcome["stdout"]),
        "stderr": digest_record(outcome["stderr"]),
        "result": dict(result),
    }


def image_payload_bytes(benchmark: Any, manifest_path: Path) -> bytes:
    manifest = strict_object(
        read_input(benchmark, manifest_path, "functional-alpha image manifest"),
        "functional-alpha image manifest",
    )
    payload = manifest.get("payload")
    if not isinstance(payload, list) or not payload:
        fail("IMAGE_MISMATCH", "functional-alpha image payload is unavailable")
    image_parts: list[bytes] = []
    for item in payload:
        if not isinstance(item, dict):
            fail("IMAGE_MISMATCH", "functional-alpha image payload is malformed")
        part = read_input(
            benchmark,
            image_member(manifest_path.parent, item.get("path"), "image payload"),
            "image payload",
        )
        if len(part) != item.get("byteLength") or sha256(part) != item.get("sha256"):
            fail("IMAGE_MISMATCH", "functional-alpha image payload differs")
        image_parts.append(part)
    return b"".join(image_parts)


def codex_wire(
    benchmark: Any, manifest_path: Path, task: Mapping[str, Any]
) -> tuple[bytes, str]:
    manifest = strict_object(
        read_input(benchmark, manifest_path, "functional-alpha image manifest"),
        "functional-alpha image manifest",
    )
    framing = manifest.get("oneFieldFraming")
    if not isinstance(framing, dict):
        fail("IMAGE_MISMATCH", "functional-alpha image framing is unavailable")
    image_bytes = image_payload_bytes(benchmark, manifest_path)
    framing_bytes = read_input(
        benchmark,
        image_member(manifest_path.parent, framing.get("path"), "one-field framing"),
        "one-field framing",
    )
    if sha256(framing_bytes) != framing.get("sha256"):
        fail("IMAGE_MISMATCH", "functional-alpha framing differs")
    try:
        template = framing_bytes.decode("utf-8")
        image_text = image_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        fail("IMAGE_MISMATCH", f"functional-alpha framing is not UTF-8: {error}")
    task_json = canonical_json(task).rstrip(b"\n")
    task_sha = sha256(task_json)
    replacements = {
        "{{IMAGE_SHA256}}": sha256(image_bytes),
        "{{IMAGE_BYTES}}": image_text,
        "{{TASK_SHA256}}": task_sha,
        "{{TASK_JSON}}": task_json.decode("utf-8"),
    }
    for marker, replacement in replacements.items():
        if template.count(marker) != 1:
            fail("IMAGE_MISMATCH", f"functional-alpha framing marker differs: {marker}")
        template = template.replace(marker, replacement)
    return template.encode("utf-8"), task_sha


def rpc_prompt_frame_valid(encoded: bytes, *, adapter_id: str, task_json: str) -> bool:
    frame = strict_object(encoded, "provider-free RPC prompt frame")
    invocation_id = frame.get("id")
    expected_order = ["id", "message", "type"]
    expected_bytes = (
        json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    base_invocation_id = invocation_id
    if adapter_id == "omp/rpc" and isinstance(invocation_id, str):
        base_invocation_id = invocation_id.removesuffix(".omp.prompt.1")
    return (
        list(frame) == expected_order
        and isinstance(invocation_id, str)
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            base_invocation_id,
        )
        is not None
        and (
            adapter_id != "omp/rpc"
            or invocation_id == f"{base_invocation_id}.omp.prompt.1"
        )
        and frame.get("type") == "prompt"
        and frame.get("message") == task_json
        and encoded == expected_bytes
    )


def omp_stdin_valid(encoded: bytes, *, task_json: str) -> bool:
    lines = encoded.splitlines(keepends=True)
    if len(lines) != 2 or any(not line.endswith(b"\n") for line in lines):
        return False
    try:
        state = strict_object(lines[0], "provider-free OMP state frame")
        prompt = strict_object(lines[1], "provider-free OMP prompt frame")
    except AdmissionError:
        return False
    state_id = state.get("id")
    prompt_id = prompt.get("id")
    if not isinstance(state_id, str) or not isinstance(prompt_id, str):
        return False
    base = state_id.removesuffix(".omp.state.1")
    return (
        list(state) == ["id", "type"]
        and state == {"id": f"{base}.omp.state.1", "type": "get_state"}
        and prompt_id == f"{base}.omp.prompt.1"
        and rpc_prompt_frame_valid(lines[1], adapter_id="omp/rpc", task_json=task_json)
    )


def validate_observation(
    encoded: bytes,
    *,
    adapter: Mapping[str, Any],
    fixture: Path,
    workspace: Path,
    prompt_root: Path,
    image_bytes: bytes,
    codex_stdin: bytes,
    task: Mapping[str, Any],
    task_sha: str,
) -> dict[str, Any]:
    value = strict_object(encoded, "fixture observation")
    exact_keys(
        value,
        {
            "files",
            "environmentNames",
            "stdin",
            "argv",
            "adapterId",
            "daemonSocket",
            "credentialConfig",
            "adapterControls",
        },
        "fixture observation",
    )
    argv = value.get("argv")
    environment_names = value.get("environmentNames")
    stdin = value.get("stdin")
    task_json = canonical_json(task).decode().strip()
    image_text = image_bytes.decode("utf-8")
    canonical_fixture = str(fixture.resolve(strict=True))
    canonical_workspace = str(workspace.resolve(strict=True))
    adapter_id = str(adapter["adapterId"])
    files = value.get("files")
    expected_file_specs = {
        "claude/print-stream-json": (
            ("--append-system-prompt-file", image_bytes, "$IMAGE_PATH"),
        ),
        "omp/rpc": (
            ("--append-system-prompt", image_bytes, "$IMAGE_PATH"),
            ("--config", OMP_CONFIG_BYTES, "$CONFIG_PATH"),
        ),
    }.get(adapter_id, ())
    normalized_files: list[dict[str, Any]] = []
    image_path: str | None = None
    config_path: str | None = None
    if not expected_file_specs:
        if files != []:
            fail(
                "FIXTURE_INVALID",
                "provider-free fixture created an unexpected prompt file",
            )
    else:
        if (
            not isinstance(files, list)
            or len(files) != len(expected_file_specs)
            or any(not isinstance(record, dict) for record in files)
        ):
            fail(
                "FIXTURE_INVALID",
                f"provider-free {adapter_id} fixture prompt file differs",
            )
        for prompt_file, (expected_flag, expected_bytes, normalized_path) in zip(
            files, expected_file_specs, strict=True
        ):
            if set(prompt_file) != {
                "flag",
                "path",
                "byteLength",
                "sha256",
                "base64",
                "mode",
            }:
                fail(
                    "FIXTURE_INVALID", "provider-free fixture prompt file is malformed"
                )
            try:
                decoded_file = base64.b64decode(
                    prompt_file.get("base64", ""), validate=True
                )
                resolved_file = Path(prompt_file["path"]).resolve(strict=False)
                resolved_file.relative_to(prompt_root.resolve(strict=True))
            except (KeyError, OSError, ValueError, TypeError):
                fail("FIXTURE_INVALID", "provider-free fixture prompt path is unsafe")
            if (
                prompt_file.get("flag") != expected_flag
                or decoded_file != expected_bytes
                or prompt_file.get("byteLength") != len(expected_bytes)
                or prompt_file.get("sha256") != sha256(expected_bytes)
                or prompt_file.get("mode") != "0600"
            ):
                fail(
                    "FIXTURE_INVALID", "provider-free fixture prompt file bytes differ"
                )
            if normalized_path == "$IMAGE_PATH":
                image_path = prompt_file["path"]
            else:
                config_path = prompt_file["path"]
            normalized_files.append({**prompt_file, "path": normalized_path})

    daemon_socket = value.get("daemonSocket")
    daemon_socket_path: str | None = None
    normalized_daemon_socket: dict[str, Any] | None = None
    if adapter_id == "prime/rpc":
        if not isinstance(daemon_socket, dict) or set(daemon_socket) != {
            "path",
            "absolute",
            "existedAtHarnessStart",
            "parentMode",
        }:
            fail("FIXTURE_INVALID", "Prime provider-free daemon socket is malformed")
        try:
            socket_path = Path(daemon_socket["path"])
            socket_path.resolve(strict=False).relative_to(
                prompt_root.resolve(strict=True)
            )
        except (KeyError, OSError, TypeError, ValueError):
            fail("FIXTURE_INVALID", "Prime provider-free daemon socket path is unsafe")
        if (
            not socket_path.is_absolute()
            or socket_path.name != "prime.sock"
            or daemon_socket.get("absolute") is not True
            or daemon_socket.get("existedAtHarnessStart") is not False
            or daemon_socket.get("parentMode") != "0700"
            or socket_path.exists()
            or socket_path.parent.exists()
        ):
            fail("FIXTURE_INVALID", "Prime provider-free daemon socket differs")
        daemon_socket_path = str(socket_path)
        normalized_daemon_socket = {
            "path": "$DAEMON_SOCKET",
            "absolute": True,
            "existedAtHarnessStart": False,
            "parentMode": "0700",
        }
    elif daemon_socket is not None:
        fail(
            "FIXTURE_INVALID",
            "provider-free fixture created an unexpected daemon socket",
        )

    credential_config = value.get("credentialConfig")
    if credential_config is not None:
        fail(
            "FIXTURE_INVALID",
            "provider-free harness-login unexpectedly redirected credential config",
        )

    expected_adapter_controls = (
        {"PRIME_AGENT_TELEMETRY": "0"} if adapter_id == "prime/rpc" else {}
    )
    if value.get("adapterControls") != expected_adapter_controls:
        fail("FIXTURE_INVALID", "provider-free adapter controls differ")

    expected_argv: list[str]
    if adapter_id == "codex/exec-json":
        expected_argv = [
            canonical_fixture,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--cd",
            canonical_workspace,
            "-",
        ]
    elif adapter_id == "claude/print-stream-json":
        expected_argv = [
            canonical_fixture,
            "--safe-mode",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--append-system-prompt-file",
            str(image_path),
            task_json,
        ]
    elif adapter_id == "prime/rpc":
        assert daemon_socket_path is not None
        expected_argv = [
            canonical_fixture,
            "--mode",
            "rpc",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--daemon-socket",
            daemon_socket_path,
            "--cwd",
            canonical_workspace,
            "--append-system-prompt",
            image_text,
            "--model",
            FIXTURE_MODEL,
        ]
    elif adapter_id == "omp/rpc":
        assert config_path is not None
        expected_argv = [
            canonical_fixture,
            "--mode",
            "rpc",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-rules",
            "--no-lsp",
            "--no-title",
            "--no-tools",
            "--append-system-prompt",
            str(image_path),
            "--model",
            FIXTURE_MODEL,
            "--config",
            config_path,
        ]
    else:  # pragma: no cover - closed ADAPTERS constant
        fail("FIXTURE_INVALID", "provider-free fixture adapter is unknown")

    try:
        executable_matches = (
            isinstance(argv, list)
            and argv
            and Path(argv[0]).resolve(strict=True) == fixture.resolve(strict=True)
        )
    except (OSError, TypeError):
        executable_matches = False
    if not executable_matches or argv != expected_argv:
        fail("FIXTURE_INVALID", "provider-free fixture argv differs")

    if not isinstance(stdin, dict) or set(stdin) != {"base64", "byteLength", "sha256"}:
        fail("FIXTURE_INVALID", "provider-free fixture stdin record is malformed")
    try:
        stdin_bytes = base64.b64decode(stdin.get("base64", ""), validate=True)
    except (TypeError, ValueError):
        fail("FIXTURE_INVALID", "provider-free fixture stdin is not canonical base64")
    if stdin != {
        "base64": base64.b64encode(stdin_bytes).decode("ascii"),
        "byteLength": len(stdin_bytes),
        "sha256": sha256(stdin_bytes),
    }:
        fail("FIXTURE_INVALID", "provider-free fixture stdin digest differs")
    if adapter_id == "codex/exec-json":
        if stdin_bytes != codex_stdin:
            fail("FIXTURE_INVALID", "Codex provider-free stdin differs")
    elif adapter_id == "claude/print-stream-json":
        if stdin_bytes:
            fail("FIXTURE_INVALID", "Claude provider-free stdin is not empty")
    elif adapter_id == "omp/rpc":
        if not omp_stdin_valid(stdin_bytes, task_json=task_json):
            fail("FIXTURE_INVALID", "provider-free OMP staged stdin differs")
    else:
        if not rpc_prompt_frame_valid(
            stdin_bytes, adapter_id=adapter_id, task_json=task_json
        ):
            fail("FIXTURE_INVALID", "provider-free RPC prompt frame differs")

    expected_credentials: set[str] = set()
    observed_credentials = PROVIDER_CREDENTIALS.intersection(environment_names or [])
    if (
        value.get("adapterId") != adapter_id
        or not isinstance(environment_names, list)
        or environment_names != sorted(environment_names)
        or any(not isinstance(name, str) for name in environment_names)
        or observed_credentials != expected_credentials
        or task_sha != sha256(canonical_json(task).rstrip(b"\n"))
    ):
        fail("FIXTURE_INVALID", "provider-free fixture observation differs")
    normalized_argv = [f"$FIXTURE/{adapter['executable']}", *argv[1:]]
    normalized_argv = [
        "$WORKSPACE" if value == canonical_workspace else value
        for value in normalized_argv
    ]
    if image_path is not None:
        normalized_argv = [
            "$IMAGE_PATH" if value == image_path else value for value in normalized_argv
        ]
    if config_path is not None:
        normalized_argv = [
            "$CONFIG_PATH" if value == config_path else value
            for value in normalized_argv
        ]
    if adapter_id == "prime/rpc":
        normalized_argv = [
            "$IMAGE_UTF8" if value == image_text else value for value in normalized_argv
        ]
        normalized_argv = [
            "$DAEMON_SOCKET" if value == daemon_socket_path else value
            for value in normalized_argv
        ]
    return {
        "adapterId": adapter_id,
        "argv": normalized_argv,
        "environmentNames": environment_names,
        "stdin": dict(stdin),
        "files": normalized_files,
        "daemonSocket": normalized_daemon_socket,
        "credentialConfig": None,
        "adapterControls": expected_adapter_controls,
        "taskSha256": task_sha,
    }


def validate_report_observation(
    observation: Mapping[str, Any],
    *,
    adapter: Mapping[str, Any],
    image_bytes: bytes,
    codex_stdin: bytes,
    task: Mapping[str, Any],
    task_sha: str,
) -> None:
    exact_keys(
        observation,
        {
            "adapterId",
            "argv",
            "environmentNames",
            "stdin",
            "files",
            "daemonSocket",
            "credentialConfig",
            "adapterControls",
            "taskSha256",
        },
        "alpha fixture observation",
    )
    adapter_id = str(adapter["adapterId"])
    task_json = canonical_json(task).decode().strip()
    expected_files = {
        "claude/print-stream-json": [
            {
                "flag": "--append-system-prompt-file",
                "path": "$IMAGE_PATH",
                "byteLength": len(image_bytes),
                "sha256": sha256(image_bytes),
                "base64": base64.b64encode(image_bytes).decode("ascii"),
                "mode": "0600",
            }
        ],
        "omp/rpc": [
            {
                "flag": "--append-system-prompt",
                "path": "$IMAGE_PATH",
                "byteLength": len(image_bytes),
                "sha256": sha256(image_bytes),
                "base64": base64.b64encode(image_bytes).decode("ascii"),
                "mode": "0600",
            },
            {
                "flag": "--config",
                "path": "$CONFIG_PATH",
                "byteLength": len(OMP_CONFIG_BYTES),
                "sha256": sha256(OMP_CONFIG_BYTES),
                "base64": base64.b64encode(OMP_CONFIG_BYTES).decode("ascii"),
                "mode": "0600",
            },
        ],
    }.get(adapter_id, [])
    if adapter_id == "codex/exec-json":
        expected_argv = [
            "$FIXTURE/codex",
            "exec",
            "--skip-git-repo-check",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--cd",
            "$WORKSPACE",
            "-",
        ]
    elif adapter_id == "claude/print-stream-json":
        expected_argv = [
            "$FIXTURE/claude",
            "--safe-mode",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--append-system-prompt-file",
            "$IMAGE_PATH",
            task_json,
        ]
    elif adapter_id == "prime/rpc":
        expected_argv = [
            "$FIXTURE/prime-agent",
            "--mode",
            "rpc",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--daemon-socket",
            "$DAEMON_SOCKET",
            "--cwd",
            "$WORKSPACE",
            "--append-system-prompt",
            "$IMAGE_UTF8",
            "--model",
            FIXTURE_MODEL,
        ]
    elif adapter_id == "omp/rpc":
        expected_argv = [
            "$FIXTURE/omp",
            "--mode",
            "rpc",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-rules",
            "--no-lsp",
            "--no-title",
            "--no-tools",
            "--append-system-prompt",
            "$IMAGE_PATH",
            "--model",
            FIXTURE_MODEL,
            "--config",
            "$CONFIG_PATH",
        ]
    else:  # pragma: no cover - closed ADAPTERS constant
        fail("REPORT_MISMATCH", "alpha adapter observation is unknown")

    stdin = observation.get("stdin")
    if not isinstance(stdin, dict) or set(stdin) != {"base64", "byteLength", "sha256"}:
        fail("REPORT_MISMATCH", "alpha adapter stdin is malformed")
    try:
        stdin_bytes = base64.b64decode(stdin.get("base64", ""), validate=True)
    except (TypeError, ValueError):
        fail("REPORT_MISMATCH", "alpha adapter stdin is not canonical base64")
    if stdin != {
        "base64": base64.b64encode(stdin_bytes).decode("ascii"),
        "byteLength": len(stdin_bytes),
        "sha256": sha256(stdin_bytes),
    }:
        fail("REPORT_MISMATCH", "alpha adapter stdin digest differs")
    if adapter_id == "codex/exec-json":
        stdin_valid = stdin_bytes == codex_stdin
    elif adapter_id == "claude/print-stream-json":
        stdin_valid = stdin_bytes == b""
    elif adapter_id == "omp/rpc":
        stdin_valid = omp_stdin_valid(stdin_bytes, task_json=task_json)
    else:
        stdin_valid = rpc_prompt_frame_valid(
            stdin_bytes, adapter_id=adapter_id, task_json=task_json
        )
    environment_names = observation.get("environmentNames")
    expected_credentials: set[str] = set()
    expected_daemon_socket = (
        {
            "path": "$DAEMON_SOCKET",
            "absolute": True,
            "existedAtHarnessStart": False,
            "parentMode": "0700",
        }
        if adapter_id == "prime/rpc"
        else None
    )
    expected_adapter_controls = (
        {"PRIME_AGENT_TELEMETRY": "0"} if adapter_id == "prime/rpc" else {}
    )
    if (
        observation.get("adapterId") != adapter_id
        or observation.get("argv") != expected_argv
        or observation.get("files") != expected_files
        or observation.get("daemonSocket") != expected_daemon_socket
        or observation.get("credentialConfig") is not None
        or observation.get("adapterControls") != expected_adapter_controls
        or observation.get("taskSha256") != task_sha
        or not stdin_valid
        or not isinstance(environment_names, list)
        or environment_names != sorted(environment_names)
        or any(not isinstance(name, str) for name in environment_names)
        or any(
            name not in ALLOWED_OBSERVED_ENVIRONMENT_NAMES for name in environment_names
        )
        or PROVIDER_CREDENTIALS.intersection(environment_names) != expected_credentials
    ):
        fail("REPORT_MISMATCH", "alpha adapter observation differs")


def run_surfaces(
    benchmark: Any,
    installed: Mapping[str, Any],
    *,
    root: Path,
    fixture_source: bytes,
    version: str,
    source_sha: str,
    image: Mapping[str, Any],
    image_manifest: Path,
    timeout_seconds: float,
    deadline: float,
    adapters: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    program = installed["workspace"] / "hello.prose.md"
    write_exclusive(program, hello_example_bytes(benchmark), 0o400)
    task = {
        "schema": "openprose.task-envelope/1",
        "argv": ["prose", "run", program.name],
        "interactionMode": "non-interactive",
    }
    expected_codex_stdin, task_sha = codex_wire(benchmark, image_manifest, task)
    image_bytes = image_payload_bytes(benchmark, image_manifest)
    results: list[dict[str, Any]] = []
    for surface in SURFACES:
        target = installed["surfaces"][surface]
        node_path = Path(installed["nodeTool"]["resolvedPath"]).parent
        shared_config_root = (
            root / "execution-environments" / surface / "saved-user-config"
        )
        shared_config_root.mkdir(parents=True, exist_ok=False)
        previous_persisted: bytes | None = None
        for adapter in adapters:
            harness = str(adapter["harness"])
            fixture_bin, observation_path, shadow_marker = install_fixture(
                root, fixture_source, surface, adapter
            )
            environment = benchmark.clean_environment(
                root / "execution-environments" / surface / harness,
                [fixture_bin, node_path],
            )
            environment["XDG_CONFIG_HOME"] = str(shared_config_root)
            if "PROSE_MODEL" in environment or "PROSE_AUTH_PROFILE" in environment:
                fail(
                    "EXECUTION_INVALID",
                    f"{surface}/{harness} route overrides escaped selection",
                )
            selection_options: list[str] = []
            if adapter["model"] is not None:
                selection_options.extend(["--model", str(adapter["model"])])
            if adapter["authProfile"] is not None:
                selection_options.extend(
                    ["--auth-profile", str(adapter["authProfile"])]
                )
            user_config = (
                Path(environment["XDG_CONFIG_HOME"]) / "openprose" / "cli.toml"
            )
            if previous_persisted is None:
                if user_config.exists():
                    fail(
                        "EXECUTION_INVALID",
                        f"{surface} saved selection did not start empty",
                    )
            elif (
                read_input(
                    benchmark,
                    user_config,
                    f"{surface}/{harness} previous persisted harness configuration",
                )
                != previous_persisted
            ):
                fail(
                    "EXECUTION_INVALID",
                    f"{surface}/{harness} did not inherit the prior saved selection",
                )
            selection_outcome = checked_step(
                benchmark,
                argv=[
                    str(target["executable"]),
                    "--output",
                    "json",
                    "cli",
                    "harness",
                    "use",
                    harness,
                    *selection_options,
                ],
                workspace=installed["workspace"],
                environment=environment,
                timeout_seconds=timeout_seconds,
                label=f"functional-alpha {surface}/{harness} harness selection",
                deadline=deadline,
            )
            selection = strict_object(
                selection_outcome["stdout"], f"{surface}/{harness} harness selection"
            )
            selection_projection = validate_harness_selection(
                selection, adapter=adapter, user_config=user_config
            )
            persisted = read_input(
                benchmark,
                user_config,
                f"{surface}/{harness} persisted harness configuration",
            )
            if persisted != persisted_config(harness):
                fail(
                    "EXECUTION_INVALID",
                    f"{surface}/{harness} persisted harness configuration differs",
                )
            previous_persisted = persisted

            doctor_outcome = checked_step(
                benchmark,
                argv=[
                    str(target["executable"]),
                    "--output",
                    "json",
                    "cli",
                    "doctor",
                ],
                workspace=installed["workspace"],
                environment=environment,
                timeout_seconds=timeout_seconds,
                label=f"functional-alpha {surface}/{harness} doctor",
                deadline=deadline,
            )
            doctor = strict_object(
                doctor_outcome["stdout"], f"{surface}/{harness} doctor result"
            )
            doctor_projection = validate_doctor_result(
                doctor,
                adapter=adapter,
                runner=target["runner"],
                version=version,
                source_sha=source_sha,
                image=image,
                workspace=installed["workspace"],
                user_config=user_config,
            )

            documented_first_run: dict[str, Any] | str = "not-applicable"
            if harness == "codex":
                documented_first_run = execute_documented_first_run(
                    benchmark,
                    installed,
                    surface=surface,
                    target=target,
                    documented=installed["documentedFirstRun"][surface],
                    observation_path=observation_path,
                    shadow_marker=shadow_marker,
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    deadline=deadline,
                )

            outcome = checked_step(
                benchmark,
                argv=[
                    str(target["executable"]),
                    "--output",
                    "json",
                    "run",
                    program.name,
                ],
                workspace=installed["workspace"],
                environment=environment,
                timeout_seconds=timeout_seconds,
                label=f"functional-alpha {surface}/{harness} persisted harness execution",
                deadline=deadline,
            )
            result = strict_object(
                outcome["stdout"], f"{surface}/{harness} runner result"
            )
            result_projection = validate_result(
                result,
                adapter_contract=adapter,
                runner=target["runner"],
                version=version,
                source_sha=source_sha,
                image=image,
                task_sha=task_sha,
            )
            observation = read_input(
                benchmark,
                observation_path,
                f"{surface}/{harness} fixture observation",
            )
            observation_projection = validate_observation(
                observation,
                adapter=adapter,
                fixture=fixture_bin / str(adapter["executable"]),
                workspace=installed["workspace"],
                prompt_root=Path(environment["TMPDIR"]),
                image_bytes=image_bytes,
                codex_stdin=expected_codex_stdin,
                task=task,
                task_sha=task_sha,
            )
            results.append(
                {
                    "surface": surface,
                    "harness": harness,
                    "exitCode": outcome["exitCode"],
                    "stdout": digest_record(outcome["stdout"]),
                    "stderr": digest_record(outcome["stderr"]),
                    "harnessSelection": {
                        **step_record(selection_outcome, selection_projection),
                        "config": digest_record(persisted),
                    },
                    "doctor": step_record(doctor_outcome, doctor_projection),
                    "result": result_projection,
                    "fixtureObservation": observation_projection,
                    "documentedFirstRun": documented_first_run,
                }
            )
    return results


def verify_installed_trees(
    benchmark: Any, installed: Mapping[str, Any]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    records = {item["surface"]: item for item in installed["installations"]}
    for surface in SURFACES:
        record = records[surface]
        identity = record["treeIdentity"]
        try:
            if surface == "npm-launcher":
                command, meta_root, _, _ = benchmark.npm_layout(
                    installed["npmPrefix"], installed["platform"]
                )
                links = (
                    {command: meta_root / "bin" / "prose.js"}
                    if command.is_symlink()
                    else {}
                )
                benchmark.verify_installed_tree(installed["npmPrefix"], identity, links)
            else:
                implementation = surface.removeprefix("direct-")
                benchmark.verify_installed_tree(
                    installed["extracted"][implementation]["destination"], identity
                )
        except benchmark.BenchmarkError as error:
            fail(error.code, error.message)
        result.append(
            {
                "surface": surface,
                "method": record["method"],
                "installedByteCount": record["installedByteCount"],
                "treeSha256": identity["digestSha256"],
            }
        )
    return result


def package_records(benchmark: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    sums = read_input(benchmark, Path(context["root"]) / "SHA256SUMS", "SHA256SUMS")
    files = [
        {"path": name, **digest_record(value)}
        for name, value in sorted(context["encoded"].items())
    ]
    files.append({"path": "SHA256SUMS", **digest_record(sums)})
    files.sort(key=lambda item: item["path"])
    return {
        "mode": context["release"]["mode"],
        "platform": context["platform"],
        "files": files,
        "sha256Sums": digest_record(sums),
        "releaseManifest": digest_record(context["encoded"]["release-manifest.json"]),
        "buildProfiles": context["release"]["buildProfiles"],
    }


def execution_toolchain(
    benchmark: Any,
    installed: Mapping[str, Any],
    *,
    timeout_seconds: float,
    deadline: float,
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name in ("node", "npm"):
        observed = installed[f"{name}Tool"]
        encoded = benchmark.safe_read(
            Path(observed["resolvedPath"]), benchmark.MAX_TOOL_BYTES
        )
        if sha256(encoded) != observed["sha256"]:
            fail("TOOL_MUTATED", f"{name} executable changed after package execution")
        records[name] = {"byteLength": len(encoded), "sha256": observed["sha256"]}
        if name == "node":
            try:
                outcome = benchmark.run_process_before_deadline(
                    [observed["resolvedPath"], "--version"],
                    cwd=Path(installed["install"]),
                    environment={
                        "PATH": str(Path(observed["resolvedPath"]).parent),
                        "LANG": "C",
                        "LC_ALL": "C",
                    },
                    timeout_seconds=min(timeout_seconds, 10),
                    description="alpha admission Node version evidence",
                    deadline_monotonic=deadline,
                )
            except benchmark.BenchmarkError as error:
                fail(error.code, error.message)
            try:
                version = outcome["stdout"].decode("ascii").strip()
            except UnicodeDecodeError:
                version = ""
            if (
                outcome["exitCode"] != 0
                or outcome["stderr"]
                or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version) is None
            ):
                fail("TOOL_UNAVAILABLE", "Node version evidence is malformed")
            records[name]["version"] = version
    return records


def surface_records(installed: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = {item["surface"]: item for item in installed["installations"]}
    result: dict[str, dict[str, Any]] = {}
    for surface in SURFACES:
        target = installed["surfaces"][surface]
        result[surface] = {
            "runner": target["runner"],
            "binarySha256": target["binarySha256"],
            "packageArtifactSha256": target["packageArtifactSha256"],
            "installationTreeSha256": records[surface]["treeIdentity"]["digestSha256"],
            "execution": "verified-provider-free-echo",
        }
        if surface == "npm-launcher":
            result[surface]["metaPackageSha256"] = target["metaPackageSha256"]
            result[surface]["launcherSourceSha256"] = target["launcherSourceSha256"]
    return result


def _validated_context(
    benchmark: Any,
    packages: Path,
    platform_value: str,
    version: str,
    source_sha: str,
    image: Mapping[str, Any],
) -> Any:
    try:
        context = benchmark.verify_package_output(
            packages, expected_platform=platform_value, purpose="alpha-invariants"
        )
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    release = context["release"]
    release_image = release.get("image")
    package_image = (
        {key: image[key] for key in release_image}
        if isinstance(release_image, dict)
        else None
    )
    if (
        release.get("mode") != "alpha"
        or release.get("version") != version
        or release.get("source")
        != {"revision": source_sha, "verification": "matched-product-doctor"}
        or release_image != package_image
        or release.get("bunRuntime")
        != ALPHA_BUN_RUNTIME_BY_PLATFORM.get(platform_value)
    ):
        fail(
            "IDENTITY_DIVERGENCE",
            "alpha package identity differs from admission inputs",
        )
    documented = validate_packaged_guidance(benchmark, context, version)
    return {**context, "documentedFirstRun": documented}


def run_admission(
    *,
    packages: Path,
    work_root: Path,
    out: Path,
    target_id: str,
    version: str,
    source_sha: str,
    image_manifest: Path,
    timeout_seconds: float,
    deadline_monotonic: float,
    benchmark_module: Any | None = None,
) -> dict[str, Any]:
    if target_id not in TARGET_PLATFORMS:
        fail("ARGUMENT_INVALID", "target-id is unsupported")
    if ALPHA_VERSION.fullmatch(version) is None:
        fail("ARGUMENT_INVALID", "version must be exact X.Y.Z-alpha.N SemVer")
    if FULL_SHA.fullmatch(source_sha) is None:
        fail("ARGUMENT_INVALID", "source-sha must be a full lowercase commit SHA")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or not 0 < float(timeout_seconds) <= 120
    ):
        fail("ARGUMENT_INVALID", "timeout-seconds must be finite and in (0,120]")
    if (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(float(deadline_monotonic))
        or deadline_monotonic <= time.monotonic()
    ):
        fail("ARGUMENT_INVALID", "deadline must be a finite future monotonic time")
    if os.path.lexists(out):
        fail("OUTPUT_UNSAFE", "output must not already exist")
    benchmark = benchmark_module or load_benchmark()
    platform_value = TARGET_PLATFORMS[target_id]
    if benchmark.current_platform_id() != platform_value:
        fail(
            "PLATFORM_UNSUPPORTED", "alpha admission requires the matching native host"
        )
    image, _ = image_identity(benchmark, image_manifest)
    adapter_manifest = adapter_manifest_identity(benchmark)
    fixture_source = read_input(
        benchmark, FAKE_HARNESS, "provider-free harness fixture"
    )
    context = _validated_context(
        benchmark, packages, platform_value, version, source_sha, image
    )
    owned_root = prepare_root(work_root)
    snapshot = snapshot_package(benchmark, context, owned_root / "package")
    try:
        installed = benchmark.install_verified_package_set(
            snapshot,
            owned_root / "install",
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    installed = {
        **installed,
        "documentedFirstRun": context["documentedFirstRun"],
    }
    validate_darwin_installed_signatures(
        benchmark,
        installed,
        platform_value=platform_value,
        timeout_seconds=timeout_seconds,
        deadline_monotonic=deadline_monotonic,
    )
    executions = run_surfaces(
        benchmark,
        installed,
        root=owned_root,
        fixture_source=fixture_source,
        version=version,
        source_sha=source_sha,
        image=image,
        image_manifest=image_manifest,
        timeout_seconds=timeout_seconds,
        deadline=deadline_monotonic,
        adapters=supported_adapters(platform_value),
    )
    installations = verify_installed_trees(benchmark, installed)
    try:
        benchmark.assert_package_output_unchanged(context)
        benchmark.assert_package_output_unchanged(snapshot)
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    if (
        read_input(benchmark, FAKE_HARNESS, "provider-free harness fixture")
        != fixture_source
    ):
        fail("INPUT_MUTATED", "provider-free harness fixture changed during execution")
    if adapter_manifest_identity(benchmark) != adapter_manifest:
        fail(
            "INPUT_MUTATED",
            "functional-alpha adapter manifest changed during execution",
        )
    report = {
        "schema": SCHEMA,
        "status": "passed-provider-free-functional-alpha",
        "targetId": target_id,
        "version": version,
        "sourceSha": source_sha,
        "image": image,
        "adapterManifest": adapter_manifest,
        "fixture": {
            "path": str(FAKE_HARNESS.relative_to(CLI.parent)),
            **digest_record(fixture_source),
        },
        "package": package_records(benchmark, snapshot),
        "installations": installations,
        "executionToolchain": execution_toolchain(
            benchmark,
            installed,
            timeout_seconds=timeout_seconds,
            deadline=deadline_monotonic,
        ),
        "surfaces": surface_records(installed),
        "executions": executions,
        "claims": CLAIMS,
    }
    publish_exclusive(out, canonical_json(report))
    return report


def validate_report_contract(
    *,
    report: Any,
    target_id: str,
    version: str,
    source_sha: str,
    image: Mapping[str, Any],
    adapter_manifest: Mapping[str, Any],
    fixture: Mapping[str, Any],
    expected_package: Mapping[str, Any],
    expected_artifacts: Mapping[str, str],
    image_manifest: Path,
    benchmark_module: Any | None = None,
    expected_surfaces: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate every report field without trusting captured report prose.

    ``expected_artifacts`` is the independently captured package-artifact map.
    Callers which can inspect the package payloads should also supply
    ``expected_surfaces`` to bind executable and launcher bytes exactly.  A
    release controller may omit that optional map; the remaining closed
    contract still binds every package digest, runner, installation tree,
    observation, and execution claim without accepting paths or free-form
    values from the report.
    """

    if (
        target_id not in TARGET_PLATFORMS
        or ALPHA_VERSION.fullmatch(version) is None
        or FULL_SHA.fullmatch(source_sha) is None
    ):
        fail("ARGUMENT_INVALID", "report identity is malformed")
    benchmark = benchmark_module or load_benchmark()
    if not isinstance(report, dict):
        fail("REPORT_MISMATCH", "alpha admission report is not an object")
    exact_keys(
        report,
        {
            "schema",
            "status",
            "targetId",
            "version",
            "sourceSha",
            "image",
            "adapterManifest",
            "fixture",
            "package",
            "installations",
            "executionToolchain",
            "surfaces",
            "executions",
            "claims",
        },
        "alpha admission report",
    )
    if (
        report.get("schema") != SCHEMA
        or report.get("status") != "passed-provider-free-functional-alpha"
        or report.get("targetId") != target_id
        or report.get("version") != version
        or report.get("sourceSha") != source_sha
        or report.get("claims") != CLAIMS
        or report.get("image") != image
        or report.get("adapterManifest") != adapter_manifest
        or report.get("fixture") != fixture
        or report.get("package") != expected_package
    ):
        fail("REPORT_MISMATCH", "alpha admission report authority differs")

    platform_value = TARGET_PLATFORMS[target_id]
    installations = report.get("installations")
    expected_methods = {
        "direct-rust": "validated-archive-extraction",
        "direct-bun": "validated-archive-extraction",
        "npm-launcher": "npm-global-offline-two-local-tarballs",
    }
    if not isinstance(installations, list) or len(installations) != 3:
        fail("REPORT_MISMATCH", "alpha admission installation closure differs")
    installation_map: dict[str, Mapping[str, Any]] = {}
    for item in installations:
        if not isinstance(item, dict):
            fail("REPORT_MISMATCH", "alpha admission installation is malformed")
        exact_keys(
            item,
            {"surface", "method", "installedByteCount", "treeSha256"},
            "alpha installation",
        )
        surface = item.get("surface")
        if (
            surface not in expected_methods
            or surface in installation_map
            or item.get("method") != expected_methods[surface]
            or isinstance(item.get("installedByteCount"), bool)
            or not isinstance(item.get("installedByteCount"), int)
            or item["installedByteCount"] < 1
            or not isinstance(item.get("treeSha256"), str)
            or SHA256.fullmatch(item["treeSha256"]) is None
        ):
            fail("REPORT_MISMATCH", "alpha admission installation differs")
        installation_map[surface] = item
    if tuple(installation_map) != SURFACES:
        fail("REPORT_MISMATCH", "alpha admission installation order differs")

    artifact_names = {
        "direct-rust": f"openprose-prose-cli-rust-{version}-{platform_value}.tar.gz",
        "direct-bun": f"openprose-prose-cli-bun-{version}-{platform_value}.tar.gz",
        "npm-launcher": f"openprose-prose-cli-{platform_value}-{version}.tgz",
        "npm-meta": f"openprose-prose-cli-{version}.tgz",
    }
    if set(expected_artifacts) != set(artifact_names.values()):
        fail("REPORT_MISMATCH", "alpha admission artifact authority differs")
    for name, digest in expected_artifacts.items():
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            fail("REPORT_MISMATCH", f"alpha package artifact differs: {name}")

    surfaces = report.get("surfaces")
    if not isinstance(surfaces, dict) or set(surfaces) != set(SURFACES):
        fail("REPORT_MISMATCH", "alpha admission surface closure differs")
    observed_surfaces: dict[str, dict[str, Any]] = {}
    for surface in SURFACES:
        value = surfaces.get(surface)
        keys = {
            "runner",
            "binarySha256",
            "packageArtifactSha256",
            "installationTreeSha256",
            "execution",
        }
        if surface == "npm-launcher":
            keys |= {"metaPackageSha256", "launcherSourceSha256"}
        if not isinstance(value, dict):
            fail("REPORT_MISMATCH", "alpha admission surface is malformed")
        exact_keys(value, keys, f"alpha surface {surface}")
        runner = "rust" if surface == "direct-rust" else "bun"
        if (
            value.get("runner") != runner
            or value.get("execution") != "verified-provider-free-echo"
            or value.get("installationTreeSha256")
            != installation_map[surface]["treeSha256"]
            or value.get("packageArtifactSha256")
            != expected_artifacts[artifact_names[surface]]
        ):
            fail("REPORT_MISMATCH", "alpha admission surface custody differs")
        for field in ("binarySha256", "launcherSourceSha256"):
            if field in value and (
                not isinstance(value[field], str)
                or SHA256.fullmatch(value[field]) is None
            ):
                fail("REPORT_MISMATCH", "alpha admission surface digest differs")
        if surface == "npm-launcher" and value.get("metaPackageSha256") != (
            expected_artifacts[artifact_names["npm-meta"]]
        ):
            fail("REPORT_MISMATCH", "alpha npm meta-package custody differs")
        observed_surfaces[surface] = value
    if (
        observed_surfaces["direct-bun"]["binarySha256"]
        != observed_surfaces["npm-launcher"]["binarySha256"]
    ):
        fail("REPORT_MISMATCH", "alpha Bun executable custody diverges")
    if expected_surfaces is not None and surfaces != expected_surfaces:
        fail("REPORT_MISMATCH", "alpha admission surface bytes differ")

    toolchain = report.get("executionToolchain")
    if not isinstance(toolchain, dict) or set(toolchain) != {"node", "npm"}:
        fail("REPORT_MISMATCH", "alpha admission toolchain closure differs")
    for name in ("node", "npm"):
        value = toolchain.get(name)
        expected_keys = (
            {"byteLength", "sha256", "version"}
            if name == "node"
            else {"byteLength", "sha256"}
        )
        if not isinstance(value, dict):
            fail("REPORT_MISMATCH", "alpha admission toolchain is malformed")
        exact_keys(value, expected_keys, f"executionToolchain.{name}")
        validate_digest_record(
            {key: value[key] for key in ("byteLength", "sha256")},
            f"executionToolchain.{name}",
            allow_empty=False,
        )
        if (
            name == "node"
            and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(value.get("version")))
            is None
        ):
            fail("REPORT_MISMATCH", "alpha admission Node version differs")

    task = {
        "schema": "openprose.task-envelope/1",
        "argv": ["prose", "run", "hello.prose.md"],
        "interactionMode": "non-interactive",
    }
    expected_stdin, task_sha = codex_wire(benchmark, image_manifest, task)
    image_bytes = image_payload_bytes(benchmark, image_manifest)
    executions = report.get("executions")
    expected_journeys = [
        (surface, adapter)
        for surface in SURFACES
        for adapter in supported_adapters(platform_value)
    ]
    if not isinstance(executions, list) or len(executions) != len(expected_journeys):
        fail("REPORT_MISMATCH", "alpha admission execution closure differs")
    for index, (surface, adapter) in enumerate(expected_journeys):
        harness = str(adapter["harness"])
        label = f"{surface}/{harness}"
        execution = executions[index]
        if not isinstance(execution, dict):
            fail("REPORT_MISMATCH", "alpha admission execution is malformed")
        exact_keys(
            execution,
            {
                "surface",
                "harness",
                "exitCode",
                "stdout",
                "stderr",
                "harnessSelection",
                "doctor",
                "result",
                "fixtureObservation",
                "documentedFirstRun",
            },
            "alpha execution",
        )
        validate_digest_record(
            execution.get("stdout"), f"{label} stdout", allow_empty=False
        )
        validate_digest_record(execution.get("stderr"), f"{label} stderr")
        if execution["stderr"] != digest_record(b""):
            fail("REPORT_MISMATCH", "alpha admission stderr differs")
        expected_result = {
            "runner": {
                "name": observed_surfaces[surface]["runner"],
                "version": version,
                "commit": source_sha,
            },
            "adapterId": adapter["adapterId"],
            "harnessVersion": adapter["harnessVersion"],
            "image": {
                key: image[key] for key in ("formatVersion", "version", "sha256")
            },
            "taskSha256": task_sha,
            "terminal": {
                "classification": "success",
                "transportCompleted": True,
                "terminalEventObserved": True,
            },
            "semanticStatus": "not-applicable",
            "billingOwner": "user-provider",
            "runnerExitCode": 0,
        }
        selection = execution.get("harnessSelection")
        if not isinstance(selection, dict):
            fail("REPORT_MISMATCH", "alpha harness selection evidence is malformed")
        exact_keys(
            selection,
            {"exitCode", "stdout", "stderr", "result", "config"},
            "alpha harness selection",
        )
        validate_digest_record(
            selection.get("stdout"), f"{label} selection stdout", allow_empty=False
        )
        validate_digest_record(selection.get("stderr"), f"{label} selection stderr")
        validate_digest_record(
            selection.get("config"), f"{label} persisted config", allow_empty=False
        )
        expected_selection = {
            "schema": "openprose.harness-selection/1",
            "harness": harness,
            "scope": "user",
            "path": "$USER_CONFIG",
            "changed": True,
        }
        if (
            selection.get("exitCode") != 0
            or selection.get("stderr") != digest_record(b"")
            or selection.get("result") != expected_selection
            or selection.get("config") != digest_record(persisted_config(harness))
        ):
            fail("REPORT_MISMATCH", "alpha harness selection evidence differs")

        doctor = execution.get("doctor")
        if not isinstance(doctor, dict):
            fail("REPORT_MISMATCH", "alpha doctor evidence is malformed")
        exact_keys(doctor, {"exitCode", "stdout", "stderr", "result"}, "alpha doctor")
        validate_digest_record(
            doctor.get("stdout"), f"{label} doctor stdout", allow_empty=False
        )
        validate_digest_record(doctor.get("stderr"), f"{label} doctor stderr")
        expected_doctor = {
            "runner": {
                "name": observed_surfaces[surface]["runner"],
                "version": version,
                "commit": source_sha,
            },
            "build": {"profile": "release", "testSeamsEnabled": False},
            "ready": True,
            "selectedHarness": harness,
            "selectedHarnessVersion": adapter["harnessVersion"],
            "selectedTransport": adapter["transport"],
            "selectedAdapterId": adapter["adapterId"],
            "promptPlacement": adapter["promptPlacement"],
            "isolation": reported_isolation(
                adapter, observed_surfaces[surface]["runner"]
            ),
            "authCategory": "harness-managed",
            "billingOwner": "user-provider",
            "configurationHarness": {"value": harness, "sourceKind": "user-config"},
            "problems": [],
        }
        if (
            doctor.get("exitCode") != 0
            or doctor.get("stderr") != digest_record(b"")
            or doctor.get("result") != expected_doctor
        ):
            fail("REPORT_MISMATCH", "alpha doctor evidence differs")
        observation = execution.get("fixtureObservation")
        if not isinstance(observation, dict):
            fail("REPORT_MISMATCH", "alpha admission observation is malformed")
        validate_report_observation(
            observation,
            adapter=adapter,
            image_bytes=image_bytes,
            codex_stdin=expected_stdin,
            task=task,
            task_sha=task_sha,
        )
        if (
            execution.get("surface") != surface
            or execution.get("harness") != harness
            or execution.get("exitCode") != 0
            or execution.get("result") != expected_result
        ):
            fail("REPORT_MISMATCH", "alpha admission execution evidence differs")
        expected_documented: dict[str, Any] | str = "not-applicable"
        if harness == "codex":
            expected_documented = {
                "source": (
                    "npm-meta/README.md"
                    if surface == "npm-launcher"
                    else f"{surface.removeprefix('direct-')}-standalone/README.txt"
                ),
                "command": ["$INSTALLED_CANDIDATE", "run", "$PACKAGED_EXAMPLE"],
                "shell": False,
                "hostilePathShadowed": True,
                "exitCode": 0,
                "fixtureReached": True,
            }
        if execution.get("documentedFirstRun") != expected_documented:
            fail("REPORT_MISMATCH", "documented first-run execution differs")
    return report


def validate_report(
    *,
    report_path: Path,
    packages: Path,
    target_id: str,
    version: str,
    source_sha: str,
    image_manifest: Path,
    benchmark_module: Any | None = None,
) -> dict[str, Any]:
    if (
        target_id not in TARGET_PLATFORMS
        or ALPHA_VERSION.fullmatch(version) is None
        or FULL_SHA.fullmatch(source_sha) is None
    ):
        fail("ARGUMENT_INVALID", "report identity is malformed")
    benchmark = benchmark_module or load_benchmark()
    report_bytes = read_input(benchmark, report_path, "alpha admission report")
    report = strict_object(report_bytes, "alpha admission report")
    if canonical_json(report) != report_bytes:
        fail("REPORT_MISMATCH", "alpha admission report is not canonical JSON")
    exact_keys(
        report,
        {
            "schema",
            "status",
            "targetId",
            "version",
            "sourceSha",
            "image",
            "adapterManifest",
            "fixture",
            "package",
            "installations",
            "executionToolchain",
            "surfaces",
            "executions",
            "claims",
        },
        "alpha admission report",
    )
    if (
        report.get("schema") != SCHEMA
        or report.get("status") != "passed-provider-free-functional-alpha"
        or report.get("targetId") != target_id
        or report.get("version") != version
        or report.get("sourceSha") != source_sha
        or report.get("claims") != CLAIMS
    ):
        fail("REPORT_MISMATCH", "alpha admission report identity or claims differ")
    image, _ = image_identity(benchmark, image_manifest)
    if report.get("image") != image:
        fail("REPORT_MISMATCH", "alpha admission report image differs")
    if report.get("adapterManifest") != adapter_manifest_identity(benchmark):
        fail("REPORT_MISMATCH", "alpha admission adapter manifest differs")
    fixture_source = read_input(
        benchmark, FAKE_HARNESS, "provider-free harness fixture"
    )
    if report.get("fixture") != {
        "path": str(FAKE_HARNESS.relative_to(CLI.parent)),
        **digest_record(fixture_source),
    }:
        fail("REPORT_MISMATCH", "alpha admission fixture identity differs")
    platform_value = TARGET_PLATFORMS[target_id]
    context = _validated_context(
        benchmark, packages, platform_value, version, source_sha, image
    )
    if report.get("package") != package_records(benchmark, context):
        fail("REPORT_MISMATCH", "alpha admission package closure differs")

    installations = report.get("installations")
    expected_methods = {
        "direct-rust": "validated-archive-extraction",
        "direct-bun": "validated-archive-extraction",
        "npm-launcher": "npm-global-offline-two-local-tarballs",
    }
    if not isinstance(installations, list) or len(installations) != 3:
        fail("REPORT_MISMATCH", "alpha admission installation closure differs")
    installation_map: dict[str, Mapping[str, Any]] = {}
    for item in installations:
        if not isinstance(item, dict):
            fail("REPORT_MISMATCH", "alpha admission installation is malformed")
        exact_keys(
            item,
            {"surface", "method", "installedByteCount", "treeSha256"},
            "alpha installation",
        )
        surface = item.get("surface")
        if (
            surface not in expected_methods
            or surface in installation_map
            or item.get("method") != expected_methods[surface]
            or isinstance(item.get("installedByteCount"), bool)
            or not isinstance(item.get("installedByteCount"), int)
            or item["installedByteCount"] < 1
            or not isinstance(item.get("treeSha256"), str)
            or SHA256.fullmatch(item["treeSha256"]) is None
        ):
            fail("REPORT_MISMATCH", "alpha admission installation differs")
        installation_map[surface] = item
    if tuple(installation_map) != SURFACES:
        fail("REPORT_MISMATCH", "alpha admission installation order differs")

    artifacts = {name: item["sha256"] for name, item in context["artifacts"].items()}
    rust_archive = f"openprose-prose-cli-rust-{version}-{platform_value}.tar.gz"
    bun_archive = f"openprose-prose-cli-bun-{version}-{platform_value}.tar.gz"
    npm_platform = f"openprose-prose-cli-{platform_value}-{version}.tgz"
    npm_meta = f"openprose-prose-cli-{version}.tgz"
    try:
        payloads = benchmark.validate_package_payloads(context)
    except benchmark.BenchmarkError as error:
        fail(error.code, error.message)
    expected_surfaces = {
        "direct-rust": {
            "runner": "rust",
            "binarySha256": payloads["extracted"]["rust"]["binarySha256"],
            "packageArtifactSha256": artifacts[rust_archive],
            "installationTreeSha256": installation_map["direct-rust"]["treeSha256"],
            "execution": "verified-provider-free-echo",
        },
        "direct-bun": {
            "runner": "bun",
            "binarySha256": payloads["extracted"]["bun"]["binarySha256"],
            "packageArtifactSha256": artifacts[bun_archive],
            "installationTreeSha256": installation_map["direct-bun"]["treeSha256"],
            "execution": "verified-provider-free-echo",
        },
        "npm-launcher": {
            "runner": "bun",
            "binarySha256": payloads["extracted"]["bun"]["binarySha256"],
            "packageArtifactSha256": artifacts[npm_platform],
            "installationTreeSha256": installation_map["npm-launcher"]["treeSha256"],
            "execution": "verified-provider-free-echo",
            "metaPackageSha256": artifacts[npm_meta],
            "launcherSourceSha256": sha256(payloads["npmPackage"]["launcher"]),
        },
    }
    if report.get("surfaces") != expected_surfaces:
        fail("REPORT_MISMATCH", "alpha admission surface custody differs")

    toolchain = report.get("executionToolchain")
    if not isinstance(toolchain, dict) or set(toolchain) != {"node", "npm"}:
        fail("REPORT_MISMATCH", "alpha admission toolchain closure differs")
    for name in ("node", "npm"):
        expected_keys = (
            {"byteLength", "sha256", "version"}
            if name == "node"
            else {
                "byteLength",
                "sha256",
            }
        )
        exact_keys(toolchain[name], expected_keys, f"executionToolchain.{name}")
        validate_digest_record(
            {key: toolchain[name][key] for key in ("byteLength", "sha256")},
            f"executionToolchain.{name}",
            allow_empty=False,
        )
        if (
            name == "node"
            and re.fullmatch(
                r"v[0-9]+\.[0-9]+\.[0-9]+", str(toolchain[name].get("version"))
            )
            is None
        ):
            fail("REPORT_MISMATCH", "alpha admission Node version differs")

    task = {
        "schema": "openprose.task-envelope/1",
        "argv": ["prose", "run", "hello.prose.md"],
        "interactionMode": "non-interactive",
    }
    expected_stdin, task_sha = codex_wire(benchmark, image_manifest, task)
    image_bytes = image_payload_bytes(benchmark, image_manifest)
    executions = report.get("executions")
    expected_journeys = [
        (surface, adapter)
        for surface in SURFACES
        for adapter in supported_adapters(platform_value)
    ]
    if not isinstance(executions, list) or len(executions) != len(expected_journeys):
        fail("REPORT_MISMATCH", "alpha admission execution closure differs")
    for index, (surface, adapter) in enumerate(expected_journeys):
        harness = str(adapter["harness"])
        label = f"{surface}/{harness}"
        execution = executions[index]
        if not isinstance(execution, dict):
            fail("REPORT_MISMATCH", "alpha admission execution is malformed")
        exact_keys(
            execution,
            {
                "surface",
                "harness",
                "exitCode",
                "stdout",
                "stderr",
                "harnessSelection",
                "doctor",
                "result",
                "fixtureObservation",
                "documentedFirstRun",
            },
            "alpha execution",
        )
        validate_digest_record(
            execution.get("stdout"), f"{label} stdout", allow_empty=False
        )
        validate_digest_record(execution.get("stderr"), f"{label} stderr")
        if execution["stderr"] != digest_record(b""):
            fail("REPORT_MISMATCH", "alpha admission stderr differs")
        expected_result = {
            "runner": {
                "name": expected_surfaces[surface]["runner"],
                "version": version,
                "commit": source_sha,
            },
            "adapterId": adapter["adapterId"],
            "harnessVersion": adapter["harnessVersion"],
            "image": {
                key: image[key] for key in ("formatVersion", "version", "sha256")
            },
            "taskSha256": task_sha,
            "terminal": {
                "classification": "success",
                "transportCompleted": True,
                "terminalEventObserved": True,
            },
            "semanticStatus": "not-applicable",
            "billingOwner": "user-provider",
            "runnerExitCode": 0,
        }
        selection = execution.get("harnessSelection")
        if not isinstance(selection, dict):
            fail("REPORT_MISMATCH", "alpha harness selection evidence is malformed")
        exact_keys(
            selection,
            {"exitCode", "stdout", "stderr", "result", "config"},
            "alpha harness selection",
        )
        validate_digest_record(
            selection.get("stdout"), f"{label} selection stdout", allow_empty=False
        )
        validate_digest_record(selection.get("stderr"), f"{label} selection stderr")
        validate_digest_record(
            selection.get("config"), f"{label} persisted config", allow_empty=False
        )
        expected_selection = {
            "schema": "openprose.harness-selection/1",
            "harness": harness,
            "scope": "user",
            "path": "$USER_CONFIG",
            "changed": True,
        }
        if (
            selection.get("exitCode") != 0
            or selection.get("stderr") != digest_record(b"")
            or selection.get("result") != expected_selection
            or selection.get("config") != digest_record(persisted_config(harness))
        ):
            fail("REPORT_MISMATCH", "alpha harness selection evidence differs")

        doctor = execution.get("doctor")
        if not isinstance(doctor, dict):
            fail("REPORT_MISMATCH", "alpha doctor evidence is malformed")
        exact_keys(doctor, {"exitCode", "stdout", "stderr", "result"}, "alpha doctor")
        validate_digest_record(
            doctor.get("stdout"), f"{label} doctor stdout", allow_empty=False
        )
        validate_digest_record(doctor.get("stderr"), f"{label} doctor stderr")
        expected_doctor = {
            "runner": {
                "name": expected_surfaces[surface]["runner"],
                "version": version,
                "commit": source_sha,
            },
            "build": {"profile": "release", "testSeamsEnabled": False},
            "ready": True,
            "selectedHarness": harness,
            "selectedHarnessVersion": adapter["harnessVersion"],
            "selectedTransport": adapter["transport"],
            "selectedAdapterId": adapter["adapterId"],
            "promptPlacement": adapter["promptPlacement"],
            "isolation": reported_isolation(
                adapter, expected_surfaces[surface]["runner"]
            ),
            "authCategory": "harness-managed",
            "billingOwner": "user-provider",
            "configurationHarness": {"value": harness, "sourceKind": "user-config"},
            "problems": [],
        }
        if (
            doctor.get("exitCode") != 0
            or doctor.get("stderr") != digest_record(b"")
            or doctor.get("result") != expected_doctor
        ):
            fail("REPORT_MISMATCH", "alpha doctor evidence differs")
        observation = execution.get("fixtureObservation")
        if not isinstance(observation, dict):
            fail("REPORT_MISMATCH", "alpha admission observation is malformed")
        validate_report_observation(
            observation,
            adapter=adapter,
            image_bytes=image_bytes,
            codex_stdin=expected_stdin,
            task=task,
            task_sha=task_sha,
        )
        if (
            execution.get("surface") != surface
            or execution.get("harness") != harness
            or execution.get("exitCode") != 0
            or execution.get("result") != expected_result
        ):
            fail("REPORT_MISMATCH", "alpha admission execution evidence differs")
        expected_documented: dict[str, Any] | str = "not-applicable"
        if harness == "codex":
            expected_documented = {
                "source": (
                    "npm-meta/README.md"
                    if surface == "npm-launcher"
                    else f"{surface.removeprefix('direct-')}-standalone/README.txt"
                ),
                "command": [
                    "$INSTALLED_CANDIDATE",
                    "run",
                    "$PACKAGED_EXAMPLE",
                ],
                "shell": False,
                "hostilePathShadowed": True,
                "exitCode": 0,
                "fixtureReached": True,
            }
        if execution.get("documentedFirstRun") != expected_documented:
            fail("REPORT_MISMATCH", "documented first-run execution differs")
    return validate_report_contract(
        report=report,
        target_id=target_id,
        version=version,
        source_sha=source_sha,
        image=image,
        adapter_manifest=adapter_manifest_identity(benchmark),
        fixture={
            "path": str(FAKE_HARNESS.relative_to(CLI.parent)),
            **digest_record(fixture_source),
        },
        expected_package=package_records(benchmark, context),
        expected_artifacts=artifacts,
        expected_surfaces=expected_surfaces,
        image_manifest=image_manifest,
        benchmark_module=benchmark,
    )


def publish_exclusive(path: Path, encoded: bytes) -> None:
    requested = Path(os.path.abspath(path))
    if os.path.lexists(requested):
        fail("OUTPUT_UNSAFE", "output must not already exist")
    if not requested.parent.is_dir():
        fail("OUTPUT_UNSAFE", "output parent must already exist")
    write_exclusive(requested, encoded, 0o400)


class ClosedParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        fail("ARGUMENT_INVALID", message)


def parser() -> argparse.ArgumentParser:
    result = ClosedParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)
    admit = subcommands.add_parser("admit")
    verify = subcommands.add_parser("verify-report")
    for command in (admit, verify):
        command.add_argument("--packages", type=Path, required=True)
        command.add_argument(
            "--target-id", choices=tuple(TARGET_PLATFORMS), required=True
        )
        command.add_argument("--version", required=True)
        command.add_argument("--source-sha", required=True)
        command.add_argument("--image-manifest", type=Path, required=True)
    admit.add_argument("--work-root", type=Path, required=True)
    admit.add_argument("--out", type=Path, required=True)
    admit.add_argument("--timeout-seconds", type=float, default=15)
    admit.add_argument("--budget-seconds", type=float, default=300)
    verify.add_argument("--report", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "admit":
            if (
                not math.isfinite(args.budget_seconds)
                or not 0 < args.budget_seconds <= 900
            ):
                fail("ARGUMENT_INVALID", "budget-seconds must be finite and in (0,900]")
            report = run_admission(
                packages=args.packages,
                work_root=args.work_root,
                out=args.out,
                target_id=args.target_id,
                version=args.version,
                source_sha=args.source_sha,
                image_manifest=args.image_manifest,
                timeout_seconds=args.timeout_seconds,
                deadline_monotonic=time.monotonic() + args.budget_seconds,
            )
        else:
            report = validate_report(
                report_path=args.report,
                packages=args.packages,
                target_id=args.target_id,
                version=args.version,
                source_sha=args.source_sha,
                image_manifest=args.image_manifest,
            )
        sys.stdout.buffer.write(canonical_json(report))
        return 0
    except AdmissionError as error:
        sys.stderr.buffer.write(
            canonical_json(
                {"schema": ERROR_SCHEMA, "code": error.code, "message": error.message}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
