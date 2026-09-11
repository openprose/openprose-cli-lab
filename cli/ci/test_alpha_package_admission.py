from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "cli"
SCRIPT = CLI / "ci" / "alpha_package_admission.py"
PACKAGER = CLI / "ci" / "package_local.py"
IMAGE_MANIFEST = CLI / "shared" / "image" / "echo-v0" / "manifest.json"
LAUNCHER = CLI / "bun" / "npm" / "bin" / "prose.js"
HELLO_EXAMPLE = CLI / "conformance" / "live-alpha" / "hello.prose.md"
VERSION = "0.1.0-alpha.1"
SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"

spec = importlib.util.spec_from_file_location(
    "openprose_alpha_package_admission", SCRIPT
)
assert spec is not None and spec.loader is not None
ADMISSION = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ADMISSION)


JOURNEY_HEADINGS = {
    "prime": "Prime — macOS Apple silicon only:",
    "omp": "OMP — macOS Apple silicon or Linux x64 only:",
    "codex": "Codex — every supported functional-alpha platform:",
    "claude": "Claude — macOS Apple silicon only:",
}
JOURNEY_INSTALLS = {
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
PROVIDER_CHARGE_BOUNDARY = (
    "The run command contacts the selected provider and may incur charges under "
    "the signed-in account. The CLI cannot determine the account or billing route."
)


def documented_journey(harness: str) -> bytes:
    selection = f'  "/candidate" cli harness use {harness}'
    if harness in {"prime", "omp"}:
        selection += (
            " --model openai-codex/gpt-5.4 "
            f"--auth-profile {harness}-harness-login"
        )
    sign_in = "  codex login\n" if harness == "codex" else ""
    return (
        f"  {JOURNEY_HEADINGS[harness]}\n"
        "  Authentication is not automated. Complete the harness sign-in flow "
        "before you run `cli doctor` or `run`.\n"
        f"  {PROVIDER_CHARGE_BOUNDARY}\n"
        f"  {JOURNEY_INSTALLS[harness]}\n"
        f"{sign_in}"
        f"{selection}\n"
        '  "/candidate" cli doctor\n'
        '  "/candidate" run "/example.prose.md"\n'
    ).encode("utf-8")


def host() -> tuple[str, str]:
    systems = {"Darwin": "darwin", "Linux": "linux"}
    machines = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}
    system = systems.get(platform.system())
    machine = machines.get(platform.machine())
    if system is None or machine is None:
        raise unittest.SkipTest("functional-alpha admission is POSIX-only")
    platform_id = f"linux-{machine}-gnu" if system == "linux" else f"darwin-{machine}"
    target_id = {
        "linux-x64-gnu": "linux-x64",
        "linux-arm64-gnu": "linux-arm64",
        "darwin-arm64": "darwin-arm",
        "darwin-x64": "darwin-x64",
    }[platform_id]
    return target_id, platform_id


TARGET_ID, PLATFORM_ID = host()


def readelf_args() -> list[str]:
    if not PLATFORM_ID.startswith("linux-"):
        return []
    discovered = shutil.which("readelf")
    if discovered is None:
        raise unittest.SkipTest("Linux alpha package tests require readelf")
    return ["--readelf", str(Path(discovered).resolve(strict=True))]


def clean_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    config = root / "config"
    cache = root / "cache"
    scratch = root / "tmp"
    for directory in (home, config, cache, scratch):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CACHE_HOME": str(cache),
        "TMPDIR": str(scratch),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C",
        "LC_ALL": "C",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "",
    }


def image_identity() -> dict[str, object]:
    encoded = IMAGE_MANIFEST.read_bytes()
    value = json.loads(encoded)
    return {
        "formatVersion": value["imageFormatVersion"],
        "version": value["imageVersion"],
        "sha256": value["aggregateSha256"]["sha256"],
        "manifestSha256": hashlib.sha256(encoded).hexdigest(),
        "releaseEligible": True,
    }


def write_candidate(path: Path, runner: str) -> None:
    image = image_identity()
    manifest = json.loads(IMAGE_MANIFEST.read_text("utf-8"))
    image_bytes = b"".join(
        (IMAGE_MANIFEST.parent / item["path"]).read_bytes()
        for item in manifest["payload"]
    )
    framing = (IMAGE_MANIFEST.parent / manifest["oneFieldFraming"]["path"]).read_text(
        "utf-8"
    )
    source = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import hashlib, json, os, subprocess, sys
        from pathlib import Path

        VERSION = {VERSION!r}
        SOURCE = {SOURCE_SHA!r}
        RUNNER = {runner!r}
        IMAGE = {image!r}
        IMAGE_TEXT = {image_bytes.decode('utf-8')!r}
        FRAMING = {framing!r}

        def compact(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        args = sys.argv[1:]
        config_path = Path(os.environ["XDG_CONFIG_HOME"]) / "openprose" / "cli.toml"
        if "--version" in args:
            print(f"prose {{VERSION}} ({{RUNNER}})")
            raise SystemExit(0)
        if args[-4:] == ["cli", "harness", "use", "codex"]:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            changed = not config_path.exists() or config_path.read_text("utf-8") != 'harness = "codex"\\n'
            config_path.write_text('harness = "codex"\\n', encoding="utf-8")
            print(compact({{
                "schema": "openprose.harness-selection/1",
                "harness": "codex",
                "scope": "user",
                "path": str(config_path),
                "changed": changed,
            }}))
            raise SystemExit(0)
        if args[-2:] == ["cli", "doctor"]:
            selected = config_path.exists() and config_path.read_text("utf-8") == 'harness = "codex"\\n'
            version_probe = subprocess.run(["codex", "--version"], capture_output=True, text=True, check=False) if selected else None
            ready = selected and version_probe.returncode == 0
            print(compact({{
                "schema": "openprose.doctor-report/1",
                "runner": {{"name": RUNNER, "version": VERSION, "commit": SOURCE}},
                "build": {{"profile": "release", "testSeamsEnabled": False}},
                "image": {{key: IMAGE[key] for key in ("formatVersion", "version", "sha256", "releaseEligible")}},
                "ready": ready,
                "cwd": str(Path.cwd()),
                "selectedHarness": "codex" if selected else "openprose",
                "selectedHarnessVersion": version_probe.stdout.strip() if ready else None,
                "selectedTransport": "exec-json" if selected else "hosted",
                "selectedAdapterId": "codex/exec-json" if selected else "openprose/hosted",
                "promptPlacement": "user-prefix-framed" if selected else None,
                "isolation": "unsupported",
                "authCategory": "harness-managed" if selected else "openprose-account",
                "billingOwner": "user-provider" if selected else "openprose",
                "configuration": {{
                    "userConfigPath": str(config_path),
                    "values": {{
                        "harness": {{
                            "value": "codex" if selected else "openprose",
                            "source": {{
                                "kind": "user-config" if selected else "default",
                                "location": str(config_path) if selected else "built-in",
                            }},
                        }},
                    }},
                }},
                "harnesses": [],
                "problems": [] if ready else [{{"code": "HOSTED_UNAVAILABLE"}}],
            }}))
            raise SystemExit(0 if ready else 10)
        try:
            index = args.index("run")
            program = args[index + 1]
        except (ValueError, IndexError):
            raise SystemExit(2)
        if (
            "--harness" in args
            or "--transport" in args
            or not config_path.exists()
            or config_path.read_text("utf-8") != 'harness = "codex"\\n'
        ):
            raise SystemExit(2)
        version_probe = subprocess.run(["codex", "--version"], capture_output=True, text=True, check=False)
        task = {{
            "schema": "openprose.task-envelope/1",
            "argv": ["prose", "run", program],
            "interactionMode": "non-interactive",
        }}
        task_bytes = compact(task)
        wire = (FRAMING
            .replace("{{{{IMAGE_SHA256}}}}", hashlib.sha256(IMAGE_TEXT.encode()).hexdigest())
            .replace("{{{{IMAGE_BYTES}}}}", IMAGE_TEXT)
            .replace("{{{{TASK_SHA256}}}}", hashlib.sha256(task_bytes.encode()).hexdigest())
            .replace("{{{{TASK_JSON}}}}", task_bytes)
            .encode())
        harness = subprocess.run(
            ["codex", "exec", "--json", compact(task)],
            input=wire,
            capture_output=True,
            check=False,
        )
        if version_probe.returncode != 0 or harness.returncode != 0:
            raise SystemExit(10)
        result = {{
            "schema": "openprose.runner-result/1",
            "runner": {{"name": RUNNER, "version": VERSION, "commit": SOURCE}},
            "adapter": {{"id": "codex/exec-json", "harnessVersion": version_probe.stdout.strip()}},
            "languageImage": {{key: IMAGE[key] for key in ("formatVersion", "version", "sha256")}},
            "digests": {{"taskSha256": hashlib.sha256(task_bytes.encode()).hexdigest()}},
            "terminal": {{"classification": "success", "transportCompleted": True, "terminalEventObserved": True}},
            "semantic": {{"status": "not-applicable"}},
            "billing": {{"owner": "user-provider"}},
            "runnerExitCode": 0,
        }}
        print(compact(result))
        """
    ).encode("utf-8")
    path.write_bytes(source)
    path.chmod(0o755)


def build_real_release_products(root: Path) -> tuple[Path, Path]:
    cargo = shutil.which("cargo")
    bun = shutil.which("bun")
    if cargo is None or bun is None:
        raise unittest.SkipTest("Cargo and Bun are required for alpha package tests")
    environment = clean_environment(root / "build-environment")
    environment.update(
        {
            "CARGO_HOME": os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")),
            "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
            "OPENPROSE_BUILD_COMMIT": SOURCE_SHA,
            "OPENPROSE_BUILD_VERSION": VERSION,
            "OPENPROSE_REQUIRE_RELEASE_IMAGE": "1",
            "OPENPROSE_IMAGE_SOURCE_DIR": str(IMAGE_MANIFEST.parent),
            "OPENPROSE_IMAGE_BUNDLE": str(
                CLI / "shared" / "image" / "embedded" / "current.bundle.bin"
            ),
            "OPENPROSE_IMAGE_BUNDLE_CHECKSUM": str(
                CLI / "shared" / "image" / "embedded" / "current.bundle.sha256"
            ),
            "CARGO_TARGET_DIR": str(root / "rust-target"),
        }
    )
    rust_build = subprocess.run(
        [
            cargo,
            "build",
            "--manifest-path",
            str(CLI / "rust" / "Cargo.toml"),
            "--release",
            "--locked",
            "--offline",
            "-p",
            "prose-cli",
            "--bin",
            "prose",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    rust = root / "rust-target" / "release" / "prose"
    if rust_build.returncode != 0 or not rust.is_file():
        raise AssertionError(f"Rust alpha build failed: {rust_build.stderr}")
    bun_product = root / "bun-prose"
    bun_build = subprocess.run(
        [
            bun,
            "--no-env-file",
            "--config=cli/bun/config/empty-bunfig.toml",
            "run",
            "cli/bun/scripts/image-bundle.ts",
            "build",
            "--image-dir",
            str(IMAGE_MANIFEST.parent),
            "--bundle",
            str(CLI / "shared" / "image" / "embedded" / "current.bundle.bin"),
            "--checksum",
            str(CLI / "shared" / "image" / "embedded" / "current.bundle.sha256"),
            "--outfile",
            str(bun_product),
            "--require-release-eligible",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if bun_build.returncode != 0 or not bun_product.is_file():
        raise AssertionError(f"Bun alpha build failed: {bun_build.stderr}")
    return rust, bun_product


def rewrite_checksum(package: Path, name: str) -> None:
    sums = package / "SHA256SUMS"
    records: dict[str, str] = {}
    for line in sums.read_text("ascii").splitlines():
        digest, member = line.split("  ", 1)
        records[member] = digest
    records[name] = hashlib.sha256((package / name).read_bytes()).hexdigest()
    sums.write_text(
        "".join(f"{records[member]}  {member}\n" for member in sorted(records)), "ascii"
    )


class AdmissionOracleUnitTests(unittest.TestCase):
    def test_omp_fixture_installs_an_exact_self_contained_bun_runtime(self) -> None:
        fixture_source = b"#!/bin/sh\nexit 0\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for adapter in ADMISSION.ADAPTERS:
                fixture_bin, _, _ = ADMISSION.install_fixture(
                    root, fixture_source, "direct-rust", adapter
                )
                bun = fixture_bin / "bun"
                if adapter["adapterId"] != "omp/rpc":
                    self.assertFalse(bun.exists())
                    continue
                self.assertEqual(bun.read_bytes(), ADMISSION.OMP_BUN_RUNTIME_FIXTURE)
                completed = subprocess.run(
                    [str(bun), "--version"],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    env={"PATH": str(fixture_bin), "LANG": "C", "LC_ALL": "C"},
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, b"1.3.14\n")
                self.assertEqual(completed.stderr, b"")
                self.assertNotIn(os.environ.get("PATH", "").encode(), completed.stdout)

    def test_platform_harness_support_and_repair_contract_are_exact(self) -> None:
        self.assertEqual(
            ADMISSION.ALPHA_HARNESS_SUPPORT,
            {
                "darwin-arm64": ("prime", "omp", "codex", "claude"),
                "darwin-x64": ("codex",),
                "linux-x64-gnu": ("codex", "omp"),
                "linux-arm64-gnu": ("codex",),
            },
        )
        self.assertEqual(
            ADMISSION.HARNESS_ADMISSION,
            {
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
            },
        )
        self.assertEqual(
            ADMISSION.ALPHA_BUN_RUNTIME_BY_PLATFORM,
            {
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
            },
        )
        for platform_value, harnesses in ADMISSION.ALPHA_HARNESS_SUPPORT.items():
            self.assertEqual(
                tuple(
                    item["harness"]
                    for item in ADMISSION.supported_adapters(platform_value)
                ),
                harnesses,
            )

    def test_omp_runtime_prerequisite_is_structurally_bound_without_provider_calls(
        self,
    ) -> None:
        benchmark = ADMISSION.load_benchmark()
        identity = ADMISSION.adapter_manifest_identity(benchmark)
        self.assertEqual(
            identity["ompRuntimePrerequisite"],
            {
                "runtime": "bun",
                "versionRange": ">=1.3.14",
                "repairCommand": "npm install --global bun@1.3.14 @oh-my-pi/pi-coding-agent@18.0.9",
            },
        )
        self.assertEqual(
            identity["ompRecipe"]["path"],
            "cli/shared/capabilities/adapters/recipes/omp-rpc.v1.json",
        )

        manifest = json.loads(ADMISSION.ADAPTER_MANIFEST.read_text("utf-8"))
        recipe = json.loads(ADMISSION.OMP_ADAPTER_RECIPE.read_text("utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "functional-alpha.json"
            recipe_path = root / "omp-recipe.json"
            omp = next(
                item for item in manifest["adapters"] if item["adapterId"] == "omp/rpc"
            )
            omp["runtimePrerequisites"][0]["versionRange"] = ">=1.3.13"
            manifest_path.write_text(json.dumps(manifest), "utf-8")
            recipe_path.write_text(json.dumps(recipe), "utf-8")
            with mock.patch.object(
                ADMISSION, "ADAPTER_MANIFEST", manifest_path
            ), mock.patch.object(
                ADMISSION, "OMP_ADAPTER_RECIPE", recipe_path
            ), self.assertRaisesRegex(
                ADMISSION.AdmissionError, "runtime prerequisite"
            ):
                ADMISSION.adapter_manifest_identity(benchmark)

            manifest = json.loads(ADMISSION.ADAPTER_MANIFEST.read_text("utf-8"))
            recipe["support"][
                "repairCommand"
            ] = "npm install --global @oh-my-pi/pi-coding-agent@18.0.9"
            manifest_path.write_text(json.dumps(manifest), "utf-8")
            recipe_path.write_text(json.dumps(recipe), "utf-8")
            with mock.patch.object(
                ADMISSION, "ADAPTER_MANIFEST", manifest_path
            ), mock.patch.object(
                ADMISSION, "OMP_ADAPTER_RECIPE", recipe_path
            ), self.assertRaisesRegex(
                ADMISSION.AdmissionError, "recipe"
            ):
                ADMISSION.adapter_manifest_identity(benchmark)

            recipe = json.loads(ADMISSION.OMP_ADAPTER_RECIPE.read_text("utf-8"))
            recipe["launch"]["controls"]["ownedConfigOverlay"][
                "bytes"
            ] = "retry:\n  enabled: true\n"
            recipe_path.write_text(json.dumps(recipe), "utf-8")
            with mock.patch.object(
                ADMISSION, "ADAPTER_MANIFEST", manifest_path
            ), mock.patch.object(
                ADMISSION, "OMP_ADAPTER_RECIPE", recipe_path
            ), self.assertRaisesRegex(
                ADMISSION.AdmissionError, "control overlay"
            ):
                ADMISSION.adapter_manifest_identity(benchmark)

    def test_functional_alpha_platform_inventory_omits_windows_everywhere(self) -> None:
        self.assertNotIn("win32-x64", ADMISSION.TARGET_PLATFORMS.values())
        self.assertNotIn("win32-x64", ADMISSION.ALPHA_HARNESS_SUPPORT)
        self.assertNotIn("win32-x64", ADMISSION.ALPHA_BUN_RUNTIME_BY_PLATFORM)

    def test_persisted_selection_bytes_are_exact_for_every_alpha_adapter(self) -> None:
        self.assertEqual(
            [
                ADMISSION.persisted_config(adapter["harness"])
                for adapter in ADMISSION.ADAPTERS
            ],
            [
                (
                    b'auth_profile = "prime-harness-login"\n'
                    b'harness = "prime"\n'
                    b'model = "fixture/model"\n'
                ),
                (
                    b'auth_profile = "omp-harness-login"\n'
                    b'harness = "omp"\n'
                    b'model = "fixture/model"\n'
                ),
                b'harness = "codex"\n',
                b'harness = "claude"\n',
            ],
        )
        self.assertEqual(
            [adapter["authProfile"] for adapter in ADMISSION.ADAPTERS],
            ["prime-harness-login", "omp-harness-login", None, None],
        )
        self.assertEqual(
            [adapter["model"] for adapter in ADMISSION.ADAPTERS],
            ["fixture/model", "fixture/model", None, None],
        )

    def test_codex_only_guidance_requires_no_prime_or_omp_alternative(self) -> None:
        ADMISSION.validate_documented_alternative_runs(
            documented_journey("codex"),
            source="fixture",
            executable="/candidate",
            example="/example.prose.md",
            harnesses=("codex",),
        )
        with self.assertRaisesRegex(
            ADMISSION.AdmissionError, "unsupported prime documented journey"
        ):
            ADMISSION.validate_documented_alternative_runs(
                documented_journey("prime"),
                source="fixture",
                executable="/candidate",
                example="/example.prose.md",
                harnesses=("codex",),
            )

    def test_alternative_doctor_and_run_must_follow_selection(self) -> None:
        journey = documented_journey("prime")
        doctor = b'  "/candidate" cli doctor\n'
        without_ordered_doctor = journey.replace(doctor, b"") + doctor
        with self.assertRaisesRegex(
            ADMISSION.AdmissionError, "documented prime journey"
        ):
            ADMISSION.validate_documented_alternative_runs(
                without_ordered_doctor,
                source="fixture",
                executable="/candidate",
                example="/example.prose.md",
                harnesses=("prime",),
            )
        ADMISSION.validate_documented_alternative_runs(
            journey,
            source="fixture",
            executable="/candidate",
            example="/example.prose.md",
            harnesses=("prime",),
        )

    def test_every_alternative_requires_its_own_doctor_and_run(self) -> None:
        prime = documented_journey("prime")
        omp = documented_journey("omp")
        codex = documented_journey("codex")
        claude = documented_journey("claude")
        with self.assertRaisesRegex(
            ADMISSION.AdmissionError, "documented omp journey"
        ):
            ADMISSION.validate_documented_alternative_runs(
                prime
                + omp.replace(
                    b'  "/candidate" run "/example.prose.md"\n', b""
                )
                + codex
                + claude,
                source="fixture",
                executable="/candidate",
                example="/example.prose.md",
                harnesses=("prime", "omp", "codex", "claude"),
            )
        ADMISSION.validate_documented_alternative_runs(
            prime + omp + codex + claude,
            source="fixture",
            executable="/candidate",
            example="/example.prose.md",
            harnesses=("prime", "omp", "codex", "claude"),
        )

    def test_every_journey_requires_exact_heading_and_install_command(self) -> None:
        for harness in JOURNEY_HEADINGS:
            with self.subTest(harness=harness, mutation="heading"):
                with self.assertRaisesRegex(
                    ADMISSION.AdmissionError, f"documented {harness} journey"
                ):
                    ADMISSION.validate_documented_alternative_runs(
                        documented_journey(harness).replace(
                            JOURNEY_HEADINGS[harness].encode("utf-8"),
                            f"Unlabeled {harness}".encode("utf-8"),
                        ),
                        source="fixture",
                        executable="/candidate",
                        example="/example.prose.md",
                        harnesses=(harness,),
                    )
            with self.subTest(harness=harness, mutation="install"):
                with self.assertRaisesRegex(
                    ADMISSION.AdmissionError, f"documented {harness} journey"
                ):
                    ADMISSION.validate_documented_alternative_runs(
                        documented_journey(harness).replace(
                            JOURNEY_INSTALLS[harness].encode("utf-8"),
                            b"install-something-else",
                        ),
                        source="fixture",
                        executable="/candidate",
                        example="/example.prose.md",
                        harnesses=(harness,),
                    )

    def test_every_journey_requires_cost_boundary_before_run(self) -> None:
        for harness in JOURNEY_HEADINGS:
            journey = documented_journey(harness)
            for mutation in (
                journey.replace(
                    f"  {PROVIDER_CHARGE_BOUNDARY}\n".encode("utf-8"), b""
                ),
                journey.replace(
                    f"  {PROVIDER_CHARGE_BOUNDARY}\n".encode("utf-8"), b""
                )
                + f"  {PROVIDER_CHARGE_BOUNDARY}\n".encode("utf-8"),
            ):
                with self.subTest(harness=harness), self.assertRaisesRegex(
                    ADMISSION.AdmissionError, f"documented {harness} journey"
                ):
                    ADMISSION.validate_documented_alternative_runs(
                        mutation,
                        source="fixture",
                        executable="/candidate",
                        example="/example.prose.md",
                        harnesses=(harness,),
                    )
    def test_codex_journey_requires_login_between_install_and_selection(self) -> None:
        journey = documented_journey("codex")
        for mutation in (
            journey.replace(b"  codex login\n", b""),
            journey.replace(b"  codex login\n", b"") + b"  codex login\n",
        ):
            with self.assertRaisesRegex(
                ADMISSION.AdmissionError, "documented codex journey"
            ):
                ADMISSION.validate_documented_alternative_runs(
                    mutation,
                    source="fixture",
                    executable="/candidate",
                    example="/example.prose.md",
                    harnesses=("codex",),
                )

    def test_codex_first_run_binds_install_login_selection_doctor_and_run(self) -> None:
        commands = (
            "  npm install --global @openai/codex@0.149.0-alpha.4.1\n"
            "  codex login\n"
            '  "/candidate" cli harness list\n'
            '  "/candidate" cli harness use codex\n'
            '  "/candidate" cli doctor\n'
            '  "/candidate" run "/example.prose.md"\n'
        )
        standalone = (
            "First run with Codex 0.149.0-alpha.4.1 (install that exact version "
            "and complete Codex sign-in before you continue):\n"
            f"{PROVIDER_CHARGE_BOUNDARY}\n"
            + commands
        ).encode("utf-8")
        npm = (
            "## First run with Codex 0.149.0-alpha.4.1\n\n"
            "Install that exact version and complete Codex sign-in before you "
            "continue.\n\n"
            f"{PROVIDER_CHARGE_BOUNDARY}\n\n"
            + commands.replace("  ", "    ", 6)
        ).encode("utf-8")
        for source, documented in (("standalone", standalone), ("npm", npm)):
            with self.subTest(source=source):
                parsed = ADMISSION.parse_documented_first_run(
                    documented,
                    source=source,
                    executable="/candidate",
                    example="/example.prose.md",
                )
                self.assertEqual(
                    parsed["commands"][:2],
                    [
                        [
                            "npm",
                            "install",
                            "--global",
                            "@openai/codex@0.149.0-alpha.4.1",
                        ],
                        ["codex", "login"],
                    ],
                )
                for mutation, error in (
                    (
                        documented.replace(b"codex login", b"codex auth"),
                        "exact installed candidate",
                    ),
                    (
                        documented.replace(
                            b"codex login\n",
                            b'"/candidate" cli harness use codex\ncodex login\n',
                        ),
                        "exact installed candidate",
                    ),
                    (
                        documented.replace(
                            PROVIDER_CHARGE_BOUNDARY.encode("utf-8"),
                            b"provider cost is unknown",
                        ),
                        "provider-cost boundary",
                    ),
                ):
                    with self.assertRaisesRegex(
                        ADMISSION.AdmissionError, error
                    ):
                        ADMISSION.parse_documented_first_run(
                            mutation,
                            source=source,
                            executable="/candidate",
                            example="/example.prose.md",
                        )

    def test_npm_guidance_resolves_platform_without_shell_metacharacters(self) -> None:
        renderer = ADMISSION.load_package_renderer()
        rendered = renderer.npm_readme("alpha", VERSION)
        ADMISSION.validate_npm_platform_guidance(rendered, source="npm-meta/README.md")
        block = renderer.alpha_platform_resolution_shell("    ").encode("utf-8")
        self.assertEqual(rendered.count(block), 4)
        repair_line = next(
            line
            for line in rendered.splitlines(keepends=True)
            if b'"@openprose/prose-cli-$PLATFORM_ID@' in line
        )
        for mutation in (
            rendered.replace(
                b"Linux:x86_64) PLATFORM_ARCH=linux-x64 ;;",
                b"Linux:x86_64) PLATFORM_ID=linux-x64-gnu ;;",
                1,
            ),
            rendered.replace(
                b"GLIBC_VERSION=$(getconf GNU_LIBC_VERSION 2>/dev/null)",
                b"GLIBC_VERSION='glibc 2.36'",
                1,
            ),
            rendered.replace(block, b"", 1),
            rendered.replace(block + repair_line, repair_line + block, 1),
            rendered.replace(b"$PLATFORM_ID", b"<platform>", 1),
        ):
            with self.assertRaisesRegex(
                ADMISSION.AdmissionError, "platform resolution"
            ):
                ADMISSION.validate_npm_platform_guidance(
                    mutation, source="npm-meta/README.md"
                )

    def test_standalone_uninstall_guidance_binds_exact_root_before_removal(
        self,
    ) -> None:
        renderer = ADMISSION.load_package_renderer()
        root_name = f"openprose-prose-cli-bun-{VERSION}-darwin-arm64"
        rendered = renderer.standalone_readme(
            mode="alpha",
            implementation="bun",
            version=VERSION,
            platform_identifier="darwin-arm64",
            archive_name=f"{root_name}.tar.gz",
            root_name=root_name,
            linux_runtime="not-applicable",
        )
        ADMISSION.validate_standalone_uninstall_guidance(
            rendered,
            source="bun-standalone/README.txt",
            root_name=root_name,
            executable="prose",
        )
        block = renderer.standalone_uninstall_shell(root_name, "prose", "  ").encode(
            "utf-8"
        )
        self.assertEqual(rendered.count(block), 1)
        rm_line = b'  rm -rf -- "$UNINSTALL_ROOT"\n'
        hello_line = (
            b'  test -f "$UNINSTALL_ROOT/examples/hello.prose.md" '
            b'&& test ! -L "$UNINSTALL_ROOT/examples/hello.prose.md"'
        )
        for mutation in (
            rendered.replace(
                block, b'  rm -rf -- "$PWD/' + root_name.encode() + b'"\n'
            ),
            rendered.replace(
                b"  UNINSTALL_ROOT=\n", b"  UNINSTALL_ROOT='<absolute-root>'\n", 1
            ),
            rendered.replace(b"    /*) ;;", b"    *) ;;", 1),
            rendered.replace(b' && test ! -L "$UNINSTALL_ROOT/README.txt"', b"", 1),
            rendered.replace(rm_line, b"", 1).replace(
                hello_line, rm_line.rstrip(b"\n") + b"\n" + hello_line, 1
            ),
        ):
            with self.assertRaisesRegex(
                ADMISSION.AdmissionError, "standalone uninstall"
            ):
                ADMISSION.validate_standalone_uninstall_guidance(
                    mutation,
                    source="bun-standalone/README.txt",
                    root_name=root_name,
                    executable="prose",
                )

    def test_npm_upgrade_guidance_binds_validation_before_prefix_and_npm(
        self,
    ) -> None:
        renderer = ADMISSION.load_package_renderer()
        rendered = renderer.npm_readme("alpha", VERSION)
        ADMISSION.validate_npm_upgrade_guidance(rendered, source="npm-meta/README.md")
        block = renderer.npm_alpha_upgrade_shell("    ").encode("utf-8")
        self.assertEqual(rendered.count(block), 1)
        npm_line = (
            b"    npm install --global --ignore-scripts --prefix "
            b'"$HOME/.local/openprose-cli-$NEW_VERSION" '
            b'"@openprose/prose-cli@$NEW_VERSION"\n'
        )
        core_line = b"    NEW_CORE=${NEW_VERSION%-alpha.*}\n"
        for mutation in (
            rendered.replace(
                block,
                b"    NEW_VERSION='replace-with-exact-version'\n" + npm_line,
            ),
            rendered.replace(
                b'    test "$NEW_CORE-alpha.$NEW_ALPHA_NUMBER" = "$NEW_VERSION"',
                b"    :",
                1,
            ),
            rendered.replace(
                b"      0|[1-9]|[1-9][0-9]*) ;;",
                b"      [0-9]*) ;;",
                1,
            ),
            rendered.replace(npm_line, b"", 1).replace(
                core_line, npm_line + core_line, 1
            ),
        ):
            with self.assertRaisesRegex(ADMISSION.AdmissionError, "npm upgrade"):
                ADMISSION.validate_npm_upgrade_guidance(
                    mutation, source="npm-meta/README.md"
                )

    def test_admission_guidance_oracles_cover_every_alpha_platform(self) -> None:
        renderer = ADMISSION.load_package_renderer()
        prerequisite = renderer.omp_runtime_prerequisite()
        display = {
            "prime": "Prime",
            "omp": "OMP",
            "codex": "Codex",
            "claude": "Claude",
        }
        version_markers = {
            "prime": "Prime: exact admitted versions are 0.7.0 and 0.8.1.",
            "omp": "OMP: exact admitted version is 18.0.9. It requires Bun 1.3.14 or newer.",
            "codex": "Codex: exact admitted version is 0.149.0-alpha.4.1.",
            "claude": "Claude: exact admitted version is 2.1.243.",
        }
        for platform_value, supported in ADMISSION.ALPHA_HARNESS_SUPPORT.items():
            root = f"root-{platform_value}"
            rendered = renderer.standalone_readme(
                mode="alpha",
                implementation="bun",
                version=VERSION,
                platform_identifier=platform_value,
                archive_name=f"artifact-{platform_value}.tar.gz",
                root_name=root,
                linux_runtime=(
                    {
                        "minimumGlibc": "2.34",
                        "requiredGlibcMaximum": {"rust": "2.34", "bun": "2.34"},
                    }
                    if platform_value.startswith("linux-")
                    else None
                ),
            )
            text = rendered.decode("utf-8")
            names = [display[harness] for harness in supported]
            if len(names) == 1:
                support_text = names[0]
            elif len(names) == 2:
                support_text = f"{names[0]} and {names[1]}"
            else:
                support_text = f"{', '.join(names[:-1])}, and {names[-1]}"
            with self.subTest(platform=platform_value):
                self.assertIn(
                    f"Functional-alpha harness support on this platform: {support_text}",
                    text,
                )
                for harness, marker in version_markers.items():
                    self.assertEqual(marker in text, harness in supported)
                self.assertEqual(
                    text.count(PROVIDER_CHARGE_BOUNDARY), 1 + len(supported)
                )
                self.assertIn(
                    "https://github.com/openprose/prose/issues/new?template=openprose-cli-bug.yml",
                    text,
                )
                self.assertIn(
                    "https://github.com/openprose/prose/security/advisories/new",
                    text,
                )
                self.assertIn(
                    "https://github.com/openprose/prose/issues/new?"
                    "template=openprose-cli-harness-model.yml",
                    text,
                )
                self.assertIn(
                    "https://github.com/openprose/prose/issues/new?"
                    "template=openprose-cli-benchmark-profile.yml",
                    text,
                )
                ADMISSION.validate_omp_guidance_lines(
                    rendered,
                    source=platform_value,
                    prerequisite=prerequisite,
                    markdown=False,
                    guidance_expected="omp" in supported,
                    command_expected="omp" in supported,
                )
                ADMISSION.validate_documented_alternative_runs(
                    rendered,
                    source=platform_value,
                    executable=f"$PWD/{root}/prose",
                    example=f"$PWD/{root}/examples/hello.prose.md",
                    harnesses=supported,
                )
                ADMISSION.parse_documented_first_run(
                    rendered,
                    source=platform_value,
                    executable=f"$PWD/{root}/prose",
                    example=f"$PWD/{root}/examples/hello.prose.md",
                )

        npm_text = renderer.npm_readme("alpha", VERSION).decode("utf-8")
        self.assertIn(
            "https://github.com/openprose/prose/issues/new?template=openprose-cli-bug.yml",
            npm_text,
        )
        self.assertIn(
            "https://github.com/openprose/prose/security/advisories/new",
            npm_text,
        )
        self.assertIn(
            "https://github.com/openprose/prose/issues/new?"
            "template=openprose-cli-harness-model.yml",
            npm_text,
        )
        self.assertIn(
            "https://github.com/openprose/prose/issues/new?"
            "template=openprose-cli-benchmark-profile.yml",
            npm_text,
        )
        self.assertIn("Linux packages require glibc 2.34 or newer", npm_text)
        self.assertIn(
            "Execution evidence is Ubuntu 22.04 only; other Linux distributions "
            "are unverified",
            npm_text,
        )

    def test_rpc_prompt_oracle_rejects_reordering_and_task_tampering(self) -> None:
        invocation_id = "01234567-89ab-7cde-8fed-0123456789ab"
        task_json = '{"argv":["prose","run","hello.prose.md"]}'
        prime = ADMISSION.canonical_json(
            {"id": invocation_id, "message": task_json, "type": "prompt"}
        )
        omp = ADMISSION.canonical_json(
            {
                "id": f"{invocation_id}.omp.prompt.1",
                "message": task_json,
                "type": "prompt",
            }
        )
        self.assertTrue(
            ADMISSION.rpc_prompt_frame_valid(
                prime, adapter_id="prime/rpc", task_json=task_json
            )
        )
        self.assertTrue(
            ADMISSION.rpc_prompt_frame_valid(
                omp, adapter_id="omp/rpc", task_json=task_json
            )
        )
        self.assertFalse(
            ADMISSION.rpc_prompt_frame_valid(
                omp, adapter_id="prime/rpc", task_json=task_json
            )
        )
        self.assertFalse(
            ADMISSION.rpc_prompt_frame_valid(
                prime,
                adapter_id="prime/rpc",
                task_json='{"argv":["prose","run","forged.prose.md"]}',
            )
        )

    def test_darwin_admission_reverifies_every_installed_binary_signature(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="openprose-codesign-unit-"
        ) as temporary:
            root = Path(temporary)
            benchmark = mock.Mock()
            benchmark.BenchmarkError = RuntimeError
            benchmark.run_process_before_deadline.return_value = {"exitCode": 0}
            benchmark.npm_layout.return_value = (
                root / "npm-prose",
                root / "meta",
                root / "platform",
                None,
            )
            installed = {
                "install": root,
                "npmPrefix": root / "prefix",
                "surfaces": {
                    "direct-rust": {"executable": root / "rust-prose"},
                    "direct-bun": {"executable": root / "bun-prose"},
                },
            }
            with mock.patch.object(Path, "is_file", return_value=True):
                ADMISSION.validate_darwin_installed_signatures(
                    benchmark,
                    installed,
                    platform_value="darwin-arm64",
                    timeout_seconds=15,
                    deadline_monotonic=time.monotonic() + 30,
                )
            self.assertEqual(benchmark.run_process_before_deadline.call_count, 3)
            for call in benchmark.run_process_before_deadline.call_args_list:
                self.assertEqual(
                    call.args[0][:4],
                    ["/usr/bin/codesign", "--verify", "--deep", "--strict"],
                )

            benchmark.run_process_before_deadline.reset_mock()
            benchmark.run_process_before_deadline.return_value = {"exitCode": 1}
            with mock.patch.object(
                Path, "is_file", return_value=True
            ), self.assertRaisesRegex(
                ADMISSION.AdmissionError, "failed macOS codesign"
            ):
                ADMISSION.validate_darwin_installed_signatures(
                    benchmark,
                    installed,
                    platform_value="darwin-arm64",
                    timeout_seconds=15,
                    deadline_monotonic=time.monotonic() + 30,
                )


class AlphaPackageAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.name == "nt":
            raise unittest.SkipTest("functional-alpha admission is POSIX-only")
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="openprose-alpha-admission-test-"
        )
        cls.root = Path(cls.temporary.name)
        cls.rust, cls.bun = build_real_release_products(cls.root)
        cls.package = cls.root / "package"
        completed = subprocess.run(
            [
                sys.executable,
                str(PACKAGER),
                "--mode",
                "alpha",
                "--version",
                VERSION,
                "--source-revision",
                SOURCE_SHA,
                "--source-date-epoch",
                "0",
                "--rust-binary",
                str(cls.rust),
                "--bun-binary",
                str(cls.bun),
                "--image-manifest",
                str(IMAGE_MANIFEST),
                *readelf_args(),
                "--out",
                str(cls.package),
            ],
            cwd=ROOT,
            env=clean_environment(cls.root / "package-environment"),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if completed.returncode != 0:
            raise AssertionError(f"alpha package fixture failed: {completed.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def admit(self, name: str, package: Path | None = None) -> dict[str, object]:
        return ADMISSION.run_admission(
            packages=package or self.package,
            work_root=self.root / f"{name}-work",
            out=self.root / f"{name}.json",
            target_id=TARGET_ID,
            version=VERSION,
            source_sha=SOURCE_SHA,
            image_manifest=IMAGE_MANIFEST,
            timeout_seconds=15,
            deadline_monotonic=time.monotonic() + 180,
        )

    def test_accepts_exact_alpha_package_and_executes_persisted_first_run_journey(
        self,
    ) -> None:
        report = self.admit("accepted")
        self.assertEqual(report["schema"], ADMISSION.SCHEMA)
        self.assertEqual(report["status"], "passed-provider-free-functional-alpha")
        self.assertEqual(report["package"]["mode"], "alpha")
        expected_journeys = [
            (surface, adapter)
            for surface in ADMISSION.SURFACES
            for adapter in ADMISSION.supported_adapters(PLATFORM_ID)
        ]
        self.assertEqual(
            [(item["surface"], item["harness"]) for item in report["executions"]],
            [(surface, adapter["harness"]) for surface, adapter in expected_journeys],
        )
        self.assertTrue(
            all(item["result"]["runnerExitCode"] == 0 for item in report["executions"])
        )
        documented = [
            item for item in report["executions"] if item["harness"] == "codex"
        ]
        self.assertEqual(len(documented), len(ADMISSION.SURFACES))
        for item in documented:
            self.assertEqual(
                item["documentedFirstRun"],
                {
                    "source": (
                        "npm-meta/README.md"
                        if item["surface"] == "npm-launcher"
                        else f"{item['surface'].removeprefix('direct-')}-standalone/README.txt"
                    ),
                    "command": ["$INSTALLED_CANDIDATE", "run", "$PACKAGED_EXAMPLE"],
                    "shell": False,
                    "hostilePathShadowed": True,
                    "exitCode": 0,
                    "fixtureReached": True,
                },
            )
        for execution, (_, adapter) in zip(
            report["executions"], expected_journeys, strict=True
        ):
            harness = adapter["harness"]
            self.assertEqual(
                execution["harnessSelection"]["result"],
                {
                    "schema": "openprose.harness-selection/1",
                    "harness": harness,
                    "scope": "user",
                    "path": "$USER_CONFIG",
                    "changed": True,
                },
            )
            self.assertEqual(
                execution["harnessSelection"]["config"],
                ADMISSION.digest_record(ADMISSION.persisted_config(harness)),
            )
            self.assertEqual(
                execution["doctor"]["result"],
                {
                    "runner": {
                        "name": execution["result"]["runner"]["name"],
                        "version": VERSION,
                        "commit": SOURCE_SHA,
                    },
                    "build": {"profile": "release", "testSeamsEnabled": False},
                    "ready": True,
                    "selectedHarness": harness,
                    "selectedHarnessVersion": adapter["harnessVersion"],
                    "selectedTransport": adapter["transport"],
                    "selectedAdapterId": adapter["adapterId"],
                    "promptPlacement": adapter["promptPlacement"],
                    "isolation": ADMISSION.reported_isolation(
                        adapter, execution["result"]["runner"]["name"]
                    ),
                    "authCategory": "harness-managed",
                    "billingOwner": "user-provider",
                    "configurationHarness": {
                        "value": harness,
                        "sourceKind": "user-config",
                    },
                    "problems": [],
                },
            )
            expected_daemon_socket = (
                {
                    "path": "$DAEMON_SOCKET",
                    "absolute": True,
                    "existedAtHarnessStart": False,
                    "parentMode": "0700",
                }
                if harness == "prime"
                else None
            )
            self.assertEqual(
                execution["fixtureObservation"]["daemonSocket"],
                expected_daemon_socket,
            )
            self.assertEqual(
                execution["fixtureObservation"]["adapterControls"],
                {"PRIME_AGENT_TELEMETRY": "0"} if harness == "prime" else {},
            )
        self.assertEqual(report["claims"], ADMISSION.CLAIMS)
        verified = ADMISSION.validate_report(
            report_path=self.root / "accepted.json",
            packages=self.package,
            target_id=TARGET_ID,
            version=VERSION,
            source_sha=SOURCE_SHA,
            image_manifest=IMAGE_MANIFEST,
        )
        self.assertEqual(verified, report)

    def test_rejects_package_checksum_and_coherent_evidence_tampering(self) -> None:
        mutations: list[tuple[str, object]] = []

        def archive_bytes(package: Path) -> None:
            name = next(
                item.name for item in package.iterdir() if item.name.endswith(".tar.gz")
            )
            (package / name).write_bytes((package / name).read_bytes() + b"tamper")

        mutations.append(("archive-checksum", archive_bytes))

        def release_artifact(package: Path) -> None:
            path = package / "release-manifest.json"
            value = json.loads(path.read_text("utf-8"))
            value["artifacts"][0]["sha256"] = "0" * 64
            path.write_bytes(ADMISSION.canonical_json(value))
            rewrite_checksum(package, path.name)

        mutations.append(("release-artifact", release_artifact))

        def bun_runtime(package: Path) -> None:
            path = package / "release-manifest.json"
            value = json.loads(path.read_text("utf-8"))
            value["bunRuntime"]["compileTarget"] = "bun-linux-x64"
            path.write_bytes(ADMISSION.canonical_json(value))
            rewrite_checksum(package, path.name)

        mutations.append(("bun-runtime", bun_runtime))

        def sbom_dependency(package: Path) -> None:
            path = package / "sbom.cdx.json"
            value = json.loads(path.read_text("utf-8"))
            dependency = next(
                item
                for item in value["components"]
                if any(
                    prop.get("name") == "openprose:kind"
                    and prop.get("value") == "resolved-dependency"
                    for prop in item.get("properties", [])
                )
            )
            dependency["version"] = f"{dependency['version']}-tampered"
            path.write_bytes(ADMISSION.canonical_json(value))
            rewrite_checksum(package, path.name)

        mutations.append(("sbom-dependency", sbom_dependency))

        def provenance_subject(package: Path) -> None:
            path = package / "provenance.json"
            value = json.loads(path.read_text("utf-8"))
            value["subject"][0]["digest"]["sha256"] = "0" * 64
            path.write_bytes(ADMISSION.canonical_json(value))
            rewrite_checksum(package, path.name)

        mutations.append(("provenance-subject", provenance_subject))

        def dependency_policy(package: Path) -> None:
            dependency_path = package / "dependency-evidence.json"
            dependency = json.loads(dependency_path.read_text("utf-8"))
            dependency["releasePolicy"]["passed"] = True
            dependency_bytes = ADMISSION.canonical_json(dependency)
            dependency_path.write_bytes(dependency_bytes)
            dependency_sha = hashlib.sha256(dependency_bytes).hexdigest()

            release_path = package / "release-manifest.json"
            release = json.loads(release_path.read_text("utf-8"))
            release["dependencyEvidence"]["byteLength"] = len(dependency_bytes)
            release["dependencyEvidence"]["sha256"] = dependency_sha
            release_path.write_bytes(ADMISSION.canonical_json(release))

            sbom_path = package / "sbom.cdx.json"
            sbom = json.loads(sbom_path.read_text("utf-8"))
            property_record = next(
                item
                for item in sbom["properties"]
                if item["name"] == "openprose:dependency-evidence-sha256"
            )
            property_record["value"] = dependency_sha
            sbom_path.write_bytes(ADMISSION.canonical_json(sbom))

            provenance_path = package / "provenance.json"
            provenance = json.loads(provenance_path.read_text("utf-8"))
            resolved = next(
                item
                for item in provenance["predicate"]["buildDefinition"][
                    "resolvedDependencies"
                ]
                if item["uri"] == "openprose:dependency-evidence"
            )
            resolved["digest"]["sha256"] = dependency_sha
            provenance_path.write_bytes(ADMISSION.canonical_json(provenance))
            for path in (dependency_path, release_path, sbom_path, provenance_path):
                rewrite_checksum(package, path.name)

        mutations.append(("dependency-policy", dependency_policy))

        for index, (name, mutate) in enumerate(mutations):
            package = self.root / f"tampered-package-{index}"
            shutil.copytree(self.package, package)
            mutate(package)
            with self.subTest(name=name), self.assertRaises(ADMISSION.AdmissionError):
                self.admit(f"tampered-package-{index}", package)

    def test_rejects_tampered_execution_report_during_assembly_validation(self) -> None:
        report = self.admit("report-source")
        mutations = []
        missing_surface = copy.deepcopy(report)
        missing_surface["executions"] = missing_surface["executions"][:-1]
        mutations.append(missing_surface)
        changed_result = copy.deepcopy(report)
        changed_result["executions"][0]["result"]["adapterId"] = "forged/adapter"
        mutations.append(changed_result)
        changed_selection = copy.deepcopy(report)
        changed_selection["executions"][0]["harnessSelection"]["result"][
            "harness"
        ] = "claude"
        mutations.append(changed_selection)
        changed_config = copy.deepcopy(report)
        changed_config["executions"][1]["harnessSelection"][
            "config"
        ] = ADMISSION.digest_record(b'harness = "codex"\n')
        mutations.append(changed_config)
        changed_doctor = copy.deepcopy(report)
        changed_doctor["executions"][0]["doctor"]["result"]["ready"] = False
        mutations.append(changed_doctor)
        changed_harness = copy.deepcopy(report)
        changed_harness["executions"][3]["harness"] = "prime"
        mutations.append(changed_harness)
        changed_observation = copy.deepcopy(report)
        changed_observation["executions"][2]["fixtureObservation"]["argv"][
            -1
        ] = "forged/model"
        mutations.append(changed_observation)
        credential_leak = copy.deepcopy(report)
        credential_leak["executions"][1]["fixtureObservation"][
            "environmentNames"
        ].append("OPENAI_API_KEY")
        credential_leak["executions"][1]["fixtureObservation"][
            "environmentNames"
        ].sort()
        mutations.append(credential_leak)
        changed_daemon_socket = copy.deepcopy(report)
        changed_daemon_socket["executions"][0]["fixtureObservation"]["daemonSocket"][
            "parentMode"
        ] = "0755"
        mutations.append(changed_daemon_socket)
        redirected_credentials = copy.deepcopy(report)
        redirected_credentials["executions"][0]["fixtureObservation"][
            "credentialConfig"
        ] = {
            "name": "PRIME_AGENT_CODING_AGENT_DIR",
            "path": "/tmp/forged-prime-credentials",
            "absolute": True,
            "existsAtHarnessStart": True,
            "mode": "0700",
        }
        mutations.append(redirected_credentials)
        missing_adapter_controls = copy.deepcopy(report)
        del missing_adapter_controls["executions"][0]["fixtureObservation"][
            "adapterControls"
        ]
        mutations.append(missing_adapter_controls)
        conflicting_adapter_controls = copy.deepcopy(report)
        conflicting_adapter_controls["executions"][0]["fixtureObservation"][
            "adapterControls"
        ]["PRIME_AGENT_TELEMETRY"] = "1"
        mutations.append(conflicting_adapter_controls)
        extra_adapter_controls = copy.deepcopy(report)
        extra_adapter_controls["executions"][1]["fixtureObservation"][
            "adapterControls"
        ]["UNEXPECTED_CONTROL"] = "0"
        mutations.append(extra_adapter_controls)
        changed_adapter_manifest = copy.deepcopy(report)
        changed_adapter_manifest["adapterManifest"]["adapterIds"] = list(
            reversed(changed_adapter_manifest["adapterManifest"]["adapterIds"])
        )
        mutations.append(changed_adapter_manifest)
        changed_surface = copy.deepcopy(report)
        changed_surface["surfaces"]["direct-rust"]["binarySha256"] = "0" * 64
        mutations.append(changed_surface)
        changed_package = copy.deepcopy(report)
        changed_package["package"]["files"][0]["byteLength"] += 1
        mutations.append(changed_package)
        for index, tampered in enumerate(mutations):
            path = self.root / f"report-tampered-{index}.json"
            path.write_bytes(ADMISSION.canonical_json(tampered))
            with self.subTest(index=index), self.assertRaises(ADMISSION.AdmissionError):
                ADMISSION.validate_report(
                    report_path=path,
                    packages=self.package,
                    target_id=TARGET_ID,
                    version=VERSION,
                    source_sha=SOURCE_SHA,
                    image_manifest=IMAGE_MANIFEST,
                )

    def test_requires_numbered_alpha_prerelease_before_reading_inputs(self) -> None:
        for value in ("0.1.0", "0.1.0-alpha", "0.1.0-beta.1", "01.1.0-alpha.1"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ADMISSION.AdmissionError, "X.Y.Z-alpha.N"
            ):
                ADMISSION.run_admission(
                    packages=Path("/does/not/exist"),
                    work_root=Path("/does/not/exist"),
                    out=Path("/does/not/exist"),
                    target_id=TARGET_ID,
                    version=value,
                    source_sha=SOURCE_SHA,
                    image_manifest=IMAGE_MANIFEST,
                    timeout_seconds=15,
                    deadline_monotonic=time.monotonic() + 30,
                )

    def test_canonical_npm_launcher_digest_is_bound_to_source_template(self) -> None:
        benchmark = ADMISSION.load_benchmark()
        self.assertEqual(
            benchmark.CANONICAL_LAUNCHER_TEMPLATE_SHA256,
            hashlib.sha256(LAUNCHER.read_bytes()).hexdigest(),
        )

    def test_admission_rejects_incomplete_packaged_first_use_guidance(self) -> None:
        benchmark = ADMISSION.load_benchmark()
        context = benchmark.verify_package_output(
            self.package,
            expected_platform=PLATFORM_ID,
            purpose="alpha-invariants",
        )
        original = benchmark.decode_archive_members

        def incomplete_readme(
            encoded: bytes, label: str
        ) -> dict[str, tuple[bytes, int]]:
            members = original(encoded, label)
            for name, (_, mode) in tuple(members.items()):
                if name.endswith(("/README.txt", "/README.md")):
                    members[name] = (b"OpenProse CLI\n", mode)
            return members

        with mock.patch.object(
            benchmark, "decode_archive_members", side_effect=incomplete_readme
        ), self.assertRaisesRegex(
            ADMISSION.AdmissionError,
            "standalone uninstall is not the canonical exact-root block",
        ):
            ADMISSION.validate_packaged_guidance(benchmark, context, VERSION)

    def test_admission_rejects_regressed_canonical_alternative_run_guidance(
        self,
    ) -> None:
        benchmark = ADMISSION.load_benchmark()
        context = benchmark.verify_package_output(
            self.package,
            expected_platform=PLATFORM_ID,
            purpose="alpha-invariants",
        )
        original_decode = benchmark.decode_archive_members
        renderer = ADMISSION.load_package_renderer()

        for replacement, expected_error in (
            (b'"$EXAMPLE"', "unresolved shell variable EXAMPLE"),
            (
                b'"/not-the-packaged-example.prose.md"',
                "documented prime journey does not bind install, selection, doctor, "
                "and the exact packaged example",
            ),
        ):
            with self.subTest(replacement=replacement):

                def regress(value: bytes) -> bytes:
                    lines = []
                    alternative_selected = False
                    for line in value.splitlines(keepends=True):
                        if (
                            line.startswith((b"  ", b"    "))
                            and b" cli harness use " in line
                            and (
                                b" prime " in line
                                or b" omp " in line
                                or b" claude" in line
                            )
                        ):
                            alternative_selected = True
                        elif (
                            alternative_selected
                            and line.startswith((b"  ", b"    "))
                            and b" run " in line
                        ):
                            line = (
                                line[: line.rfind(b" run ")]
                                + b" run "
                                + replacement
                                + b"\n"
                            )
                            alternative_selected = False
                        lines.append(line)
                    return b"".join(lines)

                def regressed_members(
                    encoded: bytes, label: str
                ) -> dict[str, tuple[bytes, int]]:
                    members = original_decode(encoded, label)
                    for name, (value, mode) in tuple(members.items()):
                        if name.endswith(("/README.txt", "/README.md")):
                            members[name] = (regress(value), mode)
                    return members

                regressed_renderer = mock.Mock(wraps=renderer)
                regressed_renderer.standalone_readme.side_effect = (
                    lambda **kwargs: regress(renderer.standalone_readme(**kwargs))
                )
                regressed_renderer.npm_readme.side_effect = (
                    lambda mode, version: regress(renderer.npm_readme(mode, version))
                )

                with mock.patch.object(
                    benchmark, "decode_archive_members", side_effect=regressed_members
                ), mock.patch.object(
                    ADMISSION, "load_package_renderer", return_value=regressed_renderer
                ), self.assertRaisesRegex(
                    ADMISSION.AdmissionError, expected_error
                ):
                    ADMISSION.validate_packaged_guidance(benchmark, context, VERSION)

    def test_admission_rejects_contradictory_or_inert_omp_repair_guidance(
        self,
    ) -> None:
        benchmark = ADMISSION.load_benchmark()
        context = benchmark.verify_package_output(
            self.package,
            expected_platform=PLATFORM_ID,
            purpose="alpha-invariants",
        )
        original = benchmark.decode_archive_members
        exact = b"npm install --global bun@1.3.14 " b"@oh-my-pi/pi-coding-agent@18.0.9"
        wrong = b"npm install --global bun@0.0.1 " b"@oh-my-pi/pi-coding-agent@latest"

        def mutate_readmes(mode: str):
            def mutated(encoded: bytes, label: str) -> dict[str, tuple[bytes, int]]:
                members = original(encoded, label)
                for name, (value, member_mode) in tuple(members.items()):
                    if not name.endswith(("/README.txt", "/README.md")):
                        continue
                    if mode == "misplaced-token":
                        value = value.replace(exact, wrong)
                        value += b"\nUnrelated audit token: " + exact + b"\n"
                    elif mode == "contradictory-line":
                        value += (
                            b"\nOMP: ignore the admitted repair and instead run "
                            + wrong
                            + b"\n"
                        )
                    elif mode == "any-bun":
                        value += b"\nThis package supports OMP with any installed Bun version.\n"
                    elif mode == "old-bun":
                        value += b"\nFor OMP use Bun 1.0.0; no upgrade is needed.\n"
                    else:
                        value += (
                            b"\nThe runtime may ignore the minimum version above.\n"
                        )
                    members[name] = (value, member_mode)
                return members

            return mutated

        for mode in (
            "misplaced-token",
            "contradictory-line",
            "any-bun",
            "old-bun",
            "ignore-minimum",
        ):
            with self.subTest(mode=mode), mock.patch.object(
                benchmark,
                "decode_archive_members",
                side_effect=mutate_readmes(mode),
            ), self.assertRaisesRegex(
                ADMISSION.AdmissionError, "guidance differs from canonical authority"
            ):
                ADMISSION.validate_packaged_guidance(benchmark, context, VERSION)

    def test_admission_rejects_an_ambient_prose_first_run_command(self) -> None:
        benchmark = ADMISSION.load_benchmark()
        context = benchmark.verify_package_output(
            self.package,
            expected_platform=PLATFORM_ID,
            purpose="alpha-invariants",
        )
        original = benchmark.decode_archive_members

        def shadowable_readme(
            encoded: bytes, label: str
        ) -> dict[str, tuple[bytes, int]]:
            members = original(encoded, label)
            for name, (value, mode) in tuple(members.items()):
                if name.endswith(("/README.txt", "/README.md")):
                    members[name] = (
                        value.replace(b'"$PWD/', b'prose # "$PWD/', 1).replace(
                            f'"$HOME/.local/openprose-cli-{VERSION}/bin/prose"'.encode(),
                            b"prose",
                            1,
                        ),
                        mode,
                    )
            return members

        with mock.patch.object(
            benchmark, "decode_archive_members", side_effect=shadowable_readme
        ), self.assertRaisesRegex(
            ADMISSION.AdmissionError, "guidance differs from canonical authority"
        ):
            ADMISSION.validate_packaged_guidance(benchmark, context, VERSION)

    def test_admission_binds_exact_packaged_hello_contract_bytes(self) -> None:
        benchmark = ADMISSION.load_benchmark()
        context = benchmark.verify_package_output(
            self.package,
            expected_platform=PLATFORM_ID,
            purpose="alpha-invariants",
        )
        original = benchmark.decode_archive_members

        def forged_example(encoded: bytes, label: str) -> dict[str, tuple[bytes, int]]:
            members = original(encoded, label)
            for name, (_, mode) in tuple(members.items()):
                if name.endswith("/examples/hello.prose.md"):
                    members[name] = (b"# not the admitted contract\n", mode)
            return members

        with mock.patch.object(
            benchmark, "decode_archive_members", side_effect=forged_example
        ), self.assertRaisesRegex(ADMISSION.AdmissionError, "hello contract"):
            ADMISSION.validate_packaged_guidance(benchmark, context, VERSION)
        self.assertEqual(
            HELLO_EXAMPLE.read_bytes(), ADMISSION.hello_example_bytes(benchmark)
        )

    def test_tool_identity_bound_accepts_setup_node_size_and_refuses_unsafe_targets(
        self,
    ) -> None:
        benchmark = ADMISSION.load_benchmark()
        tools = self.root / "large-tool-fixtures"
        tools.mkdir()
        large = tools / "node-large"
        with large.open("wb") as output:
            output.truncate(33 * 1024 * 1024)
        large.chmod(0o500)
        with mock.patch.object(benchmark.shutil, "which", return_value=str(large)):
            identity = benchmark.resolve_tool("node")
        self.assertIsNotNone(identity)
        self.assertEqual(
            identity["sha256"], hashlib.sha256(large.read_bytes()).hexdigest()
        )

        linked = tools / "node-linked"
        linked.symlink_to(large.name)
        with mock.patch.object(benchmark.shutil, "which", return_value=str(linked)):
            linked_identity = benchmark.resolve_tool("node")
        self.assertEqual(linked_identity, identity)

        broken_link = tools / "node-broken-link"
        broken_link.symlink_to("missing-node")
        with mock.patch.object(
            benchmark.shutil, "which", return_value=str(broken_link)
        ):
            self.assertIsNone(benchmark.resolve_tool("node"))

        nonregular = tools / "node-directory"
        nonregular.mkdir()
        with mock.patch.object(benchmark.shutil, "which", return_value=str(nonregular)):
            self.assertIsNone(benchmark.resolve_tool("node"))

        oversized = tools / "node-oversized"
        with oversized.open("wb") as output:
            output.truncate(benchmark.MAX_TOOL_BYTES + 1)
        oversized.chmod(0o500)
        with mock.patch.object(benchmark.shutil, "which", return_value=str(oversized)):
            self.assertIsNone(benchmark.resolve_tool("node"))


class RealProductAlphaAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.name == "nt":
            raise unittest.SkipTest("functional-alpha admission is POSIX-only")
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="openprose-real-alpha-admission-"
        )
        cls.root = Path(cls.temporary.name)
        cls.rust, cls.bun = build_real_release_products(cls.root)
        cls.package = cls.root / "package"
        packaged = subprocess.run(
            [
                sys.executable,
                str(PACKAGER),
                "--mode",
                "alpha",
                "--version",
                VERSION,
                "--source-revision",
                SOURCE_SHA,
                "--source-date-epoch",
                "0",
                "--rust-binary",
                str(cls.rust),
                "--bun-binary",
                str(cls.bun),
                "--image-manifest",
                str(IMAGE_MANIFEST),
                *readelf_args(),
                "--out",
                str(cls.package),
            ],
            cwd=ROOT,
            env=clean_environment(cls.root / "package-environment"),
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        if packaged.returncode != 0:
            raise AssertionError(f"real alpha packaging failed: {packaged.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_real_release_profile_packages_install_and_echo_on_all_surfaces(
        self,
    ) -> None:
        report = ADMISSION.run_admission(
            packages=self.package,
            work_root=self.root / "admission-work",
            out=self.root / "admission.json",
            target_id=TARGET_ID,
            version=VERSION,
            source_sha=SOURCE_SHA,
            image_manifest=IMAGE_MANIFEST,
            timeout_seconds=15,
            deadline_monotonic=time.monotonic() + 300,
        )
        self.assertEqual(
            [item["result"]["runner"]["name"] for item in report["executions"]],
            ["rust"] * len(ADMISSION.ADAPTERS)
            + ["bun"] * (2 * len(ADMISSION.ADAPTERS)),
        )
        self.assertTrue(all(item["exitCode"] == 0 for item in report["executions"]))
        verified = ADMISSION.validate_report(
            report_path=self.root / "admission.json",
            packages=self.package,
            target_id=TARGET_ID,
            version=VERSION,
            source_sha=SOURCE_SHA,
            image_manifest=IMAGE_MANIFEST,
        )
        self.assertEqual(verified, report)


if __name__ == "__main__":
    unittest.main()
