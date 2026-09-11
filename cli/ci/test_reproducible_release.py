#!/usr/bin/env python3
"""Adversarial tests for the release-package reproducibility gate."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reproducible_release as subject  # noqa: E402


VERSION = "0.1.0-alpha.1"
REVISION = "1" * 40
ZERO_DIGEST = "0" * 64


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inventory(platform_id: str) -> list[str]:
    return sorted(
        [
            "SHA256SUMS",
            "dependency-evidence.json",
            "provenance.json",
            "release-manifest.json",
            "sbom.cdx.json",
            f"openprose-prose-cli-{VERSION}.tgz",
            f"openprose-prose-cli-{platform_id}-{VERSION}.tgz",
            f"openprose-prose-cli-bun-{VERSION}-{platform_id}.tar.gz",
            f"openprose-prose-cli-rust-{VERSION}-{platform_id}.tar.gz",
        ]
    )


def tool_record(path: str, data: bytes = b"tool") -> dict[str, object]:
    return {
        "byteLength": len(data),
        "path": path,
        "sha256": sha256(data),
        "version": "test-1",
    }


def receipt(
    tools: dict[str, dict[str, object]], platform_id: str = "linux-x64-gnu"
) -> dict[str, object]:
    return {
        "channel": "alpha",
        "image": {
            "formatVersion": "1",
            "manifestSha256": ZERO_DIGEST,
            "releaseEligible": True,
            "sha256": "2" * 64,
            "version": "openprose-build-image-v1",
        },
        "inventory": inventory(platform_id),
        "platform": platform_id,
        "runner": {
            "architecture": "arm64" if platform_id == "darwin-arm64" else "x64",
            "image": "macos-15"
            if platform_id.startswith("darwin-")
            else "ubuntu-24.04",
            "imageVersion": "20260801.1",
            "kernel": "test-kernel",
            "os": "macOS" if platform_id.startswith("darwin-") else "Linux",
        },
        "schema": "openprose.release-build-receipt/1",
        "sourceRevision": REVISION,
        "tools": tools,
        "version": VERSION,
    }


def make_readelf(path: Path, observed: str = "2.34") -> None:
    body = f"""#!{sys.executable}
print('Name: GLIBC_{observed}')
""".encode()
    path.write_bytes(body)
    path.chmod(0o755)


def create_tool_receipts(
    root: Path, platform_id: str, otool: Path | None
) -> dict[str, dict[str, object]]:
    root.mkdir()
    names = ["bun", "cargo", "linker", "node", "npm", "python", "rustc"]
    if platform_id.startswith("linux-"):
        names.append("readelf")
    if platform_id.startswith("darwin-"):
        names.append("codesign")
    tools: dict[str, dict[str, object]] = {}
    for name in names:
        path = root / name
        if name == "readelf":
            make_readelf(path)
            data = path.read_bytes()
        else:
            data = f"#!/bin/sh\n# exact {name} fixture\nexit 0\n".encode()
            path.write_bytes(data)
            path.chmod(0o755)
        tools[name] = tool_record(str(path), data)
    if otool is not None:
        tools["otool"] = tool_record(str(otool), otool.read_bytes())
    return tools


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_bytes())


def refresh_package_checksums(root: Path) -> None:
    names = sorted(path.name for path in root.iterdir() if path.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_bytes(
        "".join(
            f"{sha256((root / name).read_bytes())}  {name}\n" for name in names
        ).encode()
    )


def tar_gz(entries: list[tuple[str, bytes, str]]) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, data, kind in entries:
                item = tarfile.TarInfo(name)
                item.mtime = 0
                item.uid = item.gid = 0
                item.uname = item.gname = ""
                if kind == "file":
                    item.mode = 0o755
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
                elif kind == "symlink":
                    item.type = tarfile.SYMTYPE
                    item.linkname = "prose"
                    archive.addfile(item)
                else:
                    raise AssertionError(kind)
    return raw.getvalue()


def package_bytes(
    platform_id: str, *, unsafe_archive: str | None = None
) -> dict[str, bytes]:
    files = {name: f"fixture:{name}\n".encode() for name in inventory(platform_id)}
    if platform_id.startswith(("darwin-", "linux-")):
        rust_name = f"openprose-prose-cli-rust-{VERSION}-{platform_id}"
        bun_name = f"openprose-prose-cli-bun-{VERSION}-{platform_id}"
        rust_member = f"{rust_name}/prose"
        bun_member = f"{bun_name}/prose"
        npm_platform = f"openprose-prose-cli-{platform_id}-{VERSION}.tgz"
        rust_binary = (
            b"RUST-MACHO" if platform_id.startswith("darwin-") else b"RUST-ELF"
        )
        bun_binary = b"BUN-MACHO" if platform_id.startswith("darwin-") else b"BUN-ELF"
        rust_entries = [(rust_member, rust_binary, "file")]
        if unsafe_archive == "traversal":
            rust_entries.append((f"{rust_name}/../escape", b"bad", "file"))
        if unsafe_archive == "symlink":
            rust_entries[0] = (rust_member, b"", "symlink")
        files[f"{rust_name}.tar.gz"] = tar_gz(rust_entries)
        files[f"{bun_name}.tar.gz"] = tar_gz([(bun_member, bun_binary, "file")])
        npm_binary = b"OTHER-BINARY" if unsafe_archive == "npm-drift" else bun_binary
        npm_entries = [("package/bin/prose", npm_binary, "file")]
        if platform_id.startswith("linux-"):
            npm_entries.append(
                (
                    "package/package.json",
                    canonical_json(
                        {
                            "openproseLinuxExecutionEvidence": "ubuntu-22.04-only",
                            "openproseMinimumGlibc": "2.34",
                            "openproseRequiredGlibcMaximum": "2.34",
                        }
                    ),
                    "file",
                )
            )
        files[npm_platform] = tar_gz(npm_entries)
    return files


def capturable_package_bytes(
    platform_id: str = "linux-x64-gnu", *, unsafe_archive: str | None = None
) -> dict[str, bytes]:
    files = package_bytes(platform_id, unsafe_archive=unsafe_archive)
    artifact_names = [
        f"openprose-prose-cli-{VERSION}.tgz",
        f"openprose-prose-cli-{platform_id}-{VERSION}.tgz",
        f"openprose-prose-cli-bun-{VERSION}-{platform_id}.tar.gz",
        f"openprose-prose-cli-rust-{VERSION}-{platform_id}.tar.gz",
    ]
    kinds = {
        artifact_names[0]: ("bun", "npm-meta", None),
        artifact_names[1]: ("bun", "npm-platform", platform_id),
        artifact_names[2]: ("bun", "standalone-archive", platform_id),
        artifact_names[3]: ("rust", "standalone-archive", platform_id),
    }
    artifacts = []
    for name in artifact_names:
        implementation, kind, artifact_platform = kinds[name]
        artifacts.append(
            {
                "byteLength": len(files[name]),
                "implementation": implementation,
                "kind": kind,
                "path": name,
                "platform": artifact_platform,
                "sha256": sha256(files[name]),
            }
        )
    manifest = {
        "artifacts": artifacts,
        "buildProfiles": {"bun": {}, "rust": {}},
        "bunRuntime": {},
        "claims": {},
        "dependencyEvidence": {},
        "externalGates": {},
        "image": {
            "formatVersion": "1",
            "manifestSha256": ZERO_DIGEST,
            "purpose": "functional-alpha-placeholder",
            "releaseEligible": True,
            "sha256": "2" * 64,
            "version": "openprose-build-image-v1",
        },
        "linuxRuntime": {
            "executionEvidence": "ubuntu-22.04-only",
            "minimumGlibc": "2.34",
            "requiredGlibcMaximum": {"bun": "2.34", "rust": "2.34"},
        }
        if platform_id.startswith("linux-")
        else "not-applicable",
        "lockfiles": {},
        "mode": "alpha",
        "platform": platform_id,
        "promotion": {},
        "publicationAuthorized": False,
        "releaseEligible": False,
        "schema": "openprose.local-release-manifest/1",
        "source": {"revision": REVISION, "verification": "matched-product-doctor"},
        "sourceDateEpoch": 0,
        "toolchains": {},
        "version": VERSION,
        "windowsJobObjectReleaseAdmission": False,
        "windowsProcessHost": "not-applicable",
    }
    files["release-manifest.json"] = canonical_json(manifest)
    checksummed = sorted(name for name in files if name != "SHA256SUMS")
    files["SHA256SUMS"] = "".join(
        f"{sha256(files[name])}  {name}\n" for name in checksummed
    ).encode()
    return files


def write_package(path: Path, files: dict[str, bytes]) -> None:
    path.mkdir()
    for name, data in files.items():
        (path / name).write_bytes(data)


def write_receipt(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_json(value))


def make_otool(
    path: Path,
    *,
    rust_minos: str = "11.0",
    bun_minos: str = "13.0",
    platform_value: str = "MACOS",
    malformed: bool = False,
) -> None:
    body = f"""#!{sys.executable}
import pathlib
import sys
p = pathlib.Path(sys.argv[2])
if sys.argv[1] != '-l':
    raise SystemExit(9)
if {malformed!r}:
    print(str(p) + ':')
    print('not a load command')
else:
    minimum = {rust_minos!r} if p.name == 'rust-prose' else {bun_minos!r}
    print(str(p) + ':')
    print('Load command 0')
    print('      cmd LC_BUILD_VERSION')
    print('  cmdsize 32')
    print(' platform ' + {platform_value!r})
    print('    minos ' + minimum)
    print('      sdk 15.0')
""".encode()
    path.write_bytes(body)
    path.chmod(0o755)


class ReproducibleReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="openprose-repro-")
        self.root = Path(self.temp.name).resolve()
        self.fixture_number = 0

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fixture(
        self,
        platform_id: str = "linux-x64-gnu",
        *,
        otool: Path | None = None,
        unsafe_archive: str | None = None,
    ):
        self.fixture_number += 1
        scope = self.root / f"fixture-{self.fixture_number}"
        scope.mkdir()
        left = scope / "left"
        right = scope / "right"
        write_package(
            left,
            capturable_package_bytes(platform_id, unsafe_archive=unsafe_archive),
        )
        write_package(
            right,
            capturable_package_bytes(platform_id, unsafe_archive=unsafe_archive),
        )
        tools = create_tool_receipts(scope / "tools", platform_id, otool)
        left_receipt = scope / "left-receipt.json"
        right_receipt = scope / "right-receipt.json"
        write_receipt(left_receipt, receipt(tools, platform_id))
        write_receipt(right_receipt, receipt(tools, platform_id))
        return left, right, left_receipt, right_receipt

    def run_gate(self, fixture, **kwargs):
        receipt_value = load_json(fixture[2])
        platform_value = receipt_value.get("platform")
        tools = receipt_value.get("tools")
        readelf_record = tools.get("readelf") if isinstance(tools, dict) else None
        if (
            isinstance(platform_value, str)
            and platform_value.startswith("linux-")
            and isinstance(readelf_record, dict)
            and isinstance(readelf_record.get("path"), str)
            and "readelf_path" not in kwargs
        ):
            kwargs["readelf_path"] = Path(readelf_record["path"])
        return subject.compare_release_packages(*fixture, **kwargs)

    def capture_fixture(self):
        self.fixture_number += 1
        scope = self.root / f"capture-{self.fixture_number}"
        scope.mkdir()
        package = scope / "package"
        write_package(package, capturable_package_bytes())
        tools = create_tool_receipts(scope / "tools", "linux-x64-gnu", None)
        output = scope / "receipt.json"
        runner = {
            "architecture": "x64",
            "image": "ubuntu-24.04",
            "imageVersion": "20260801.1",
            "kernel": "test-kernel",
            "os": "Linux",
        }
        return package, output, tools, runner

    def capture_args(self, fixture) -> list[str]:
        package, output, tools, runner = fixture
        arguments = [
            "capture",
            "--package",
            str(package),
            "--receipt",
            str(output),
            "--source-revision",
            REVISION,
            "--version",
            VERSION,
            "--platform",
            "linux-x64-gnu",
            "--runner-os",
            runner["os"],
            "--runner-architecture",
            runner["architecture"],
            "--runner-image",
            runner["image"],
            "--runner-image-version",
            runner["imageVersion"],
            "--runner-kernel",
            runner["kernel"],
        ]
        for name in sorted(tools):
            arguments.extend(["--tool", f"{name}={tools[name]['path']}"])
            arguments.extend(["--tool-version", f"{name}=test-1"])
        return arguments

    def test_exact_bytes_pass_with_deterministic_per_file_hashes(self) -> None:
        fixture = self.fixture()
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_OK)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["classification"], "byte-for-byte-reproducible")
        self.assertEqual(
            [row["path"] for row in report["files"]], inventory("linux-x64-gnu")
        )
        self.assertTrue(all(row["equal"] for row in report["files"]))
        self.assertEqual(
            subject.render_report(report),
            subject.render_report(self.run_gate(fixture)[1]),
        )

    def test_verify_rejects_aliased_roots_receipts_and_package_members(self) -> None:
        fixture = self.fixture()
        for label, candidate in (
            (
                "same-package-root",
                (fixture[0], fixture[0], fixture[2], fixture[3]),
            ),
            (
                "same-receipt-file",
                (fixture[0], fixture[1], fixture[2], fixture[2]),
            ),
        ):
            with self.subTest(label=label):
                code, report = self.run_gate(candidate)
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(report["classification"], "input-rejected")
                self.assertEqual(
                    report["errors"][0]["code"], "ALIASED_BUILD_COHORT"
                )

        fixture = self.fixture()
        fixture[3].unlink()
        fixture[3].hardlink_to(fixture[2])
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "ALIASED_BUILD_COHORT")

        fixture = self.fixture()
        right_member = fixture[1] / "provenance.json"
        right_member.unlink()
        right_member.hardlink_to(fixture[0] / "provenance.json")
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "ALIASED_BUILD_COHORT")

        for label, receipt_index, package_index in (
            ("left-receipt-to-right-member", 2, 1),
            ("right-receipt-to-left-member", 3, 0),
        ):
            with self.subTest(label=label):
                fixture = self.fixture()
                receipt_bytes = fixture[2].read_bytes()
                for package in fixture[:2]:
                    (package / "provenance.json").write_bytes(receipt_bytes)
                    refresh_package_checksums(package)
                aliased_receipt = fixture[receipt_index]
                aliased_receipt.unlink()
                aliased_receipt.hardlink_to(
                    fixture[package_index] / "provenance.json"
                )
                code, report = self.run_gate(fixture)
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(
                    report["errors"][0]["code"], "ALIASED_BUILD_COHORT"
                )

    def test_verify_reauthenticates_cohort_paths_after_snapshot(self) -> None:
        fixture = self.fixture()
        original_snapshot = subject._snapshot_package

        def snapshot_and_mutate_receipt(root, expected, side):
            result = original_snapshot(root, expected, side)
            if side == "right":
                fixture[3].write_bytes(fixture[3].read_bytes() + b" ")
            return result

        with mock.patch.object(
            subject, "_snapshot_package", side_effect=snapshot_and_mutate_receipt
        ):
            code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "RECEIPT_CHANGED")

        fixture = self.fixture()

        def snapshot_and_mutate_package(root, expected, side):
            result = original_snapshot(root, expected, side)
            if side == "right":
                target = fixture[1] / "provenance.json"
                target.write_bytes(target.read_bytes() + b"mutated")
            return result

        with mock.patch.object(
            subject, "_snapshot_package", side_effect=snapshot_and_mutate_package
        ):
            code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "PACKAGE_CHANGED")

    def test_cli_emits_only_the_canonical_report_and_uses_documented_exit(self) -> None:
        fixture = self.fixture()
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(subject.__file__).resolve()),
                "--left-package",
                str(fixture[0]),
                "--right-package",
                str(fixture[1]),
                "--left-receipt",
                str(fixture[2]),
                "--right-receipt",
                str(fixture[3]),
                "--readelf",
                str(load_json(fixture[2])["tools"]["readelf"]["path"]),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, subject.EXIT_OK)
        self.assertEqual(completed.stderr, b"")
        decoded = json.loads(completed.stdout)
        self.assertEqual(completed.stdout, subject.render_report(decoded))

        explicit = subprocess.run(
            [
                sys.executable,
                str(Path(subject.__file__).resolve()),
                "verify",
                *completed.args[2:],
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(explicit.returncode, subject.EXIT_OK)
        self.assertEqual(explicit.stdout, completed.stdout)
        self.assertEqual(explicit.stderr, b"")

    def test_capture_cli_writes_one_canonical_receipt_without_package_paths(
        self,
    ) -> None:
        fixture = self.capture_fixture()
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(subject.__file__).resolve()),
                *self.capture_args(fixture),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, subject.EXIT_OK)
        self.assertEqual(completed.stderr, b"")
        self.assertEqual(completed.stdout, fixture[1].read_bytes())
        captured = json.loads(completed.stdout)
        self.assertEqual(captured["schema"], "openprose.release-build-receipt/1")
        self.assertEqual(captured["inventory"], inventory("linux-x64-gnu"))
        manifest_image = load_json(fixture[0] / "release-manifest.json")["image"]
        self.assertEqual(
            captured["image"],
            {key: manifest_image[key] for key in subject.IMAGE_KEYS},
        )
        self.assertNotIn(str(fixture[0]), completed.stdout.decode())

    def test_capture_refuses_existing_output_without_changing_it(self) -> None:
        fixture = self.capture_fixture()
        fixture[1].write_bytes(b"pre-existing\n")
        code, report = subject.capture_release_receipt(
            fixture[0],
            fixture[1],
            source_revision=REVISION,
            version=VERSION,
            platform_id="linux-x64-gnu",
            runner=fixture[3],
            tool_paths={
                name: Path(record["path"]) for name, record in fixture[2].items()
            },
            tool_versions={name: "test-1" for name in fixture[2]},
        )
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "OUTPUT_EXISTS")
        self.assertEqual(fixture[1].read_bytes(), b"pre-existing\n")

    def test_capture_refuses_unexpected_tree_manifest_and_checksum(self) -> None:
        for mutation, expected_code in (
            ("extra", "PACKAGE_INVENTORY_MISMATCH"),
            ("manifest", "PACKAGE_IDENTITY_MISMATCH"),
            ("malformed", "MALFORMED_PACKAGE_METADATA"),
            ("checksum", "CHECKSUM_MISMATCH"),
        ):
            with self.subTest(mutation=mutation):
                fixture = self.capture_fixture()
                if mutation == "extra":
                    (fixture[0] / "unexpected").write_bytes(b"x")
                elif mutation == "manifest":
                    manifest = load_json(fixture[0] / "release-manifest.json")
                    manifest["version"] = "9.9.9"
                    (fixture[0] / "release-manifest.json").write_bytes(
                        canonical_json(manifest)
                    )
                    refresh_package_checksums(fixture[0])
                elif mutation == "malformed":
                    (fixture[0] / "release-manifest.json").write_bytes(
                        b'{"schema":"first","schema":"second"}\n'
                    )
                    refresh_package_checksums(fixture[0])
                else:
                    (fixture[0] / "SHA256SUMS").write_bytes(b"forged\n")
                code, report = subject.capture_release_receipt(
                    fixture[0],
                    fixture[1],
                    source_revision=REVISION,
                    version=VERSION,
                    platform_id="linux-x64-gnu",
                    runner=fixture[3],
                    tool_paths={
                        name: Path(record["path"])
                        for name, record in fixture[2].items()
                    },
                    tool_versions={name: "test-1" for name in fixture[2]},
                )
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(report["errors"][0]["code"], expected_code)
                self.assertFalse(fixture[1].exists())

    def test_capture_refuses_package_and_tool_mutation(self) -> None:
        for mutation, expected_code in (
            ("package", "PACKAGE_CHANGED"),
            ("tool", "TOOL_CHANGED"),
        ):
            with self.subTest(mutation=mutation):
                fixture = self.capture_fixture()
                original_snapshot = subject._snapshot_package

                def snapshot_and_mutate(root, expected, side):
                    result = original_snapshot(root, expected, side)
                    if mutation == "package":
                        (root / "provenance.json").write_bytes(b"mutated")
                    else:
                        tool = Path(fixture[2]["rustc"]["path"])
                        tool.write_bytes(b"#!/bin/sh\n# changed\nexit 0\n")
                        tool.chmod(0o755)
                    return result

                with mock.patch.object(
                    subject, "_snapshot_package", side_effect=snapshot_and_mutate
                ):
                    code, report = subject.capture_release_receipt(
                        fixture[0],
                        fixture[1],
                        source_revision=REVISION,
                        version=VERSION,
                        platform_id="linux-x64-gnu",
                        runner=fixture[3],
                        tool_paths={
                            name: Path(record["path"])
                            for name, record in fixture[2].items()
                        },
                        tool_versions={name: "test-1" for name in fixture[2]},
                    )
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(report["errors"][0]["code"], expected_code)
                self.assertFalse(fixture[1].exists())

    def test_capture_help_and_missing_explicit_inputs_are_actionable(self) -> None:
        script = str(Path(subject.__file__).resolve())
        for arguments, markers in (
            (["--help"], ("capture", "verify", "legacy")),
            (["capture", "--help"], ("--runner-image-version", "--tool-version")),
            (["verify", "--help"], ("--left-package", "--right-receipt")),
        ):
            completed = subprocess.run(
                [sys.executable, script, *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 0)
            rendered = completed.stdout.decode()
            for marker in markers:
                self.assertIn(marker, rendered)

        incomplete = subprocess.run(
            [sys.executable, script, "capture", "--package", str(self.root)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(incomplete.returncode, 2)
        self.assertIn(b"--receipt", incomplete.stderr)

    def test_capture_rejects_duplicate_and_unpaired_explicit_tools(self) -> None:
        fixture = self.capture_fixture()
        duplicate_arguments = self.capture_args(fixture)
        duplicate_arguments.extend(["--tool", f"rustc={fixture[2]['rustc']['path']}"])
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(subject.__file__).resolve()),
                *duplicate_arguments,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"unique", completed.stderr)
        self.assertFalse(fixture[1].exists())

        tool_paths = {name: Path(record["path"]) for name, record in fixture[2].items()}
        tool_versions = {name: "test-1" for name in fixture[2] if name != "rustc"}
        code, report = subject.capture_release_receipt(
            fixture[0],
            fixture[1],
            source_revision=REVISION,
            version=VERSION,
            platform_id="linux-x64-gnu",
            runner=fixture[3],
            tool_paths=tool_paths,
            tool_versions=tool_versions,
        )
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_CAPTURE_INPUT")
        self.assertFalse(fixture[1].exists())

        linked_fixture = self.capture_fixture()
        rustc = Path(linked_fixture[2]["rustc"]["path"])
        direct = rustc.with_name("rustc-direct")
        direct.write_bytes(rustc.read_bytes())
        direct.chmod(0o755)
        rustc.unlink()
        rustc.symlink_to(direct)
        code, report = subject.capture_release_receipt(
            linked_fixture[0],
            linked_fixture[1],
            source_revision=REVISION,
            version=VERSION,
            platform_id="linux-x64-gnu",
            runner=linked_fixture[3],
            tool_paths={
                name: Path(record["path"]) for name, record in linked_fixture[2].items()
            },
            tool_versions={name: "test-1" for name in linked_fixture[2]},
        )
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "UNSAFE_TOOL")
        self.assertFalse(linked_fixture[1].exists())

    def test_byte_mismatch_is_not_an_identity_failure(self) -> None:
        fixture = self.fixture()
        (fixture[1] / "provenance.json").write_bytes(b"mutated")
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_BYTE_MISMATCH)
        self.assertEqual(report["classification"], "byte-mismatch")
        mismatch = [row for row in report["files"] if not row["equal"]]
        self.assertEqual([row["path"] for row in mismatch], ["provenance.json"])
        self.assertNotEqual(
            mismatch[0]["left"]["sha256"], mismatch[0]["right"]["sha256"]
        )

    def test_runner_tool_and_build_identity_differences_are_non_comparable(
        self,
    ) -> None:
        for field, mutate, expected_code in (
            (
                "runner",
                lambda r: r["runner"].__setitem__("imageVersion", "other"),
                "RUNNER_IDENTITY_MISMATCH",
            ),
            (
                "tools",
                lambda r: r["tools"]["rustc"].__setitem__("version", "other"),
                "TOOL_IDENTITY_MISMATCH",
            ),
            (
                "image",
                lambda r: r["image"].__setitem__("sha256", "3" * 64),
                "BUILD_IDENTITY_MISMATCH",
            ),
        ):
            with self.subTest(field=field):
                fixture = self.fixture()
                right = load_json(fixture[3])
                mutate(right)
                write_receipt(fixture[3], right)
                if field == "tools":
                    Path(right["tools"]["rustc"]["path"]).unlink()
                code, report = self.run_gate(fixture)
                self.assertEqual(code, subject.EXIT_NON_COMPARABLE)
                self.assertEqual(report["classification"], "build-identity-mismatch")
                self.assertIn(
                    expected_code, [item["code"] for item in report["differences"]]
                )
                self.assertNotIn("files", report)

    def test_comparable_receipts_reject_lying_tool_hash_and_length(self) -> None:
        for field, mutate in (
            ("sha256", lambda value: "f" * 64),
            ("byteLength", lambda value: value + 1),
        ):
            with self.subTest(field=field):
                fixture = self.fixture()
                for receipt_path in fixture[2:]:
                    value = load_json(receipt_path)
                    record = value["tools"]["rustc"]
                    record[field] = mutate(record[field])
                    write_receipt(receipt_path, value)
                code, report = self.run_gate(fixture)
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(report["errors"][0]["code"], "TOOL_IDENTITY_MISMATCH")

    def test_comparable_receipts_reject_symlinked_and_non_executable_tools(
        self,
    ) -> None:
        for mutation, expected_code in (
            ("symlink", "UNSAFE_TOOL"),
            ("parent-symlink", "UNSAFE_TOOL"),
            ("non-executable", "UNSAFE_TOOL"),
        ):
            with self.subTest(mutation=mutation):
                fixture = self.fixture()
                value = load_json(fixture[2])
                tool = Path(value["tools"]["rustc"]["path"])
                if mutation == "symlink":
                    replacement = tool.with_name("rustc-real")
                    replacement.write_bytes(tool.read_bytes())
                    replacement.chmod(0o755)
                    tool.unlink()
                    tool.symlink_to(replacement)
                elif mutation == "parent-symlink":
                    tools = tool.parent
                    replacement = tools.with_name("tools-real")
                    tools.rename(replacement)
                    tools.symlink_to(replacement, target_is_directory=True)
                else:
                    tool.chmod(0o644)
                code, report = self.run_gate(fixture)
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(report["errors"][0]["code"], expected_code)

    def test_tool_mutation_during_package_comparison_is_rejected(self) -> None:
        fixture = self.fixture()
        value = load_json(fixture[2])
        tool = Path(value["tools"]["rustc"]["path"])
        original_snapshot = subject._snapshot_package

        def snapshot_and_mutate(root, expected, side):
            result = original_snapshot(root, expected, side)
            if side == "right":
                tool.write_bytes(b"#!/bin/sh\n# mutated\nexit 0\n")
                tool.chmod(0o755)
            return result

        with mock.patch.object(
            subject, "_snapshot_package", side_effect=snapshot_and_mutate
        ):
            code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "TOOL_CHANGED")

    def test_real_direct_executable_tool_fixtures_are_all_authenticated(self) -> None:
        fixture = self.fixture()
        declared = load_json(fixture[2])["tools"]
        self.assertEqual(
            sorted(declared),
            ["bun", "cargo", "linker", "node", "npm", "python", "readelf", "rustc"],
        )
        self.assertTrue(
            all(
                Path(record["path"]).is_file() and not Path(record["path"]).is_symlink()
                for record in declared.values()
            )
        )
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_OK)
        self.assertEqual(report["status"], "pass")

    def test_linux_receipts_require_exact_readelf_custody(self) -> None:
        fixture = self.fixture()
        value = load_json(fixture[2])
        del value["tools"]["readelf"]
        write_receipt(fixture[2], value)
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_RECEIPT")
        self.assertIn("readelf", report["errors"][0]["message"])

    def test_linux_verify_requires_the_exact_declared_readelf(self) -> None:
        fixture = self.fixture()
        code, report = subject.compare_release_packages(*fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "READELF_REQUIRED")

        code, report = subject.compare_release_packages(
            *fixture, readelf_path=self.root / "other-readelf"
        )
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "READELF_IDENTITY_MISMATCH")

    def test_linux_runtime_claim_is_independently_checked_against_exact_elfs(
        self,
    ) -> None:
        fixture = self.fixture()
        for package in fixture[:2]:
            manifest_path = package / "release-manifest.json"
            manifest = load_json(manifest_path)
            manifest["linuxRuntime"]["requiredGlibcMaximum"]["rust"] = "2.33"
            manifest_path.write_bytes(canonical_json(manifest))
            refresh_package_checksums(package)
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "LINUX_RUNTIME_CLAIM_MISMATCH")

        fixture = self.fixture()
        for package in fixture[:2]:
            manifest_path = package / "release-manifest.json"
            manifest = load_json(manifest_path)
            manifest["linuxRuntime"] = {}
            manifest_path.write_bytes(canonical_json(manifest))
            refresh_package_checksums(package)
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_RECEIPT")

    def test_linux_observed_glibc_above_floor_fails_even_if_manifest_is_forged(
        self,
    ) -> None:
        fixture = self.fixture()
        readelf = Path(load_json(fixture[2])["tools"]["readelf"]["path"])
        make_readelf(readelf, "2.35")
        for receipt_path in fixture[2:]:
            value = load_json(receipt_path)
            value["tools"]["readelf"] = tool_record(str(readelf), readelf.read_bytes())
            write_receipt(receipt_path, value)
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "LINUX_GLIBC_FLOOR_EXCEEDED")

    def test_closed_inventory_rejects_missing_extra_and_receipt_drift(self) -> None:
        mutations = ("missing", "extra", "receipt")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = self.fixture()
                if mutation == "missing":
                    (fixture[0] / "sbom.cdx.json").unlink()
                elif mutation == "extra":
                    (fixture[0] / "extra.txt").write_bytes(b"x")
                else:
                    value = load_json(fixture[2])
                    value["inventory"] = value["inventory"][:-1]
                    write_receipt(fixture[2], value)
                code, report = self.run_gate(fixture)
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertIn("INVENTORY", report["errors"][0]["code"])

    def test_symlinks_and_non_regular_members_are_rejected(self) -> None:
        fixture = self.fixture()
        target = fixture[0] / "provenance.json"
        target.unlink()
        target.symlink_to(fixture[1] / "provenance.json")
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "UNSAFE_PACKAGE_MEMBER")

        root_link = self.root / "left-link"
        root_link.symlink_to(fixture[0], target_is_directory=True)
        code, report = subject.compare_release_packages(
            root_link, fixture[1], fixture[2], fixture[3]
        )
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "UNSAFE_PACKAGE_ROOT")

        parent_alias = self.root / "parent-alias"
        parent_alias.symlink_to(self.root, target_is_directory=True)
        code, report = subject.compare_release_packages(
            parent_alias / "left", fixture[1], fixture[2], fixture[3]
        )
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "UNSAFE_PACKAGE_ROOT")

        target.unlink()
        target.mkdir()
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "UNSAFE_PACKAGE_MEMBER")

    def test_receipts_reject_duplicate_keys_noncanonical_json_and_unsafe_tool_paths(
        self,
    ) -> None:
        fixture = self.fixture()
        fixture[2].write_text('{"schema":"a","schema":"b"}\n', encoding="utf-8")
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_RECEIPT_JSON")

        fixture[2].write_text(
            json.dumps(load_json(fixture[3]), indent=2) + "\n", encoding="utf-8"
        )
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "NONCANONICAL_RECEIPT")

        value = load_json(fixture[3])
        value["tools"]["rustc"]["path"] = "/toolchain/../escape"
        write_receipt(fixture[2], value)
        code, report = self.run_gate(fixture)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_RECEIPT")

    def test_non_darwin_never_runs_otool_and_darwin_requires_a_darwin_host(
        self,
    ) -> None:
        fixture = self.fixture()
        code, report = self.run_gate(fixture, otool_path=Path("/does/not/exist"))
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "OTOOL_FORBIDDEN")

        otool = self.root / "otool"
        make_otool(otool)
        fixture = self.fixture("darwin-arm64", otool=otool)
        with mock.patch.object(subject.host_platform, "system", return_value="Linux"):
            code, report = self.run_gate(fixture, otool_path=otool)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "DARWIN_HOST_REQUIRED")

    @unittest.skipUnless(
        sys.platform == "darwin",
        "structural otool admission is intentionally macOS-only",
    )
    def test_darwin_alpha_admits_exact_minos_at_or_below_thirteen(self) -> None:
        for platform_value in ("MACOS", "1"):
            with self.subTest(platform_value=platform_value):
                otool = self.root / f"otool-{platform_value}"
                make_otool(
                    otool,
                    rust_minos="11.0",
                    bun_minos="13.0",
                    platform_value=platform_value,
                )
                fixture = self.fixture("darwin-arm64", otool=otool)
                code, report = self.run_gate(fixture, otool_path=otool)
                self.assertEqual(code, subject.EXIT_OK)
                self.assertEqual(
                    [
                        (row["implementation"], row["minimumOs"])
                        for row in report["darwinMachO"]
                    ],
                    [("bun", "13.0"), ("rust", "11.0")],
                )

    @unittest.skipUnless(
        sys.platform == "darwin",
        "structural otool admission is intentionally macOS-only",
    )
    def test_darwin_rejects_non_macos_numeric_build_platform(self) -> None:
        otool = self.root / "otool-non-macos"
        make_otool(otool, platform_value="2")
        fixture = self.fixture("darwin-arm64", otool=otool)
        code, report = self.run_gate(fixture, otool_path=otool)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_OTOOL_OUTPUT")

    @unittest.skipUnless(
        sys.platform == "darwin",
        "structural otool admission is intentionally macOS-only",
    )
    def test_darwin_alpha_rejects_minos_above_thirteen(self) -> None:
        otool = self.root / "otool"
        make_otool(otool, bun_minos="13.1")
        fixture = self.fixture("darwin-arm64", otool=otool)
        code, report = self.run_gate(fixture, otool_path=otool)
        self.assertEqual(code, subject.EXIT_DARWIN_POLICY)
        self.assertEqual(report["classification"], "darwin-minimum-os-exceeded")
        self.assertEqual(
            report["violations"],
            [{"implementation": "bun", "minimumOs": "13.1", "maximumOs": "13.0"}],
        )

    @unittest.skipUnless(
        sys.platform == "darwin",
        "structural otool admission is intentionally macOS-only",
    )
    def test_darwin_rejects_unsafe_archive_and_bun_npm_drift(self) -> None:
        for mutation, expected in (
            ("traversal", "UNSAFE_ARCHIVE"),
            ("symlink", "UNSAFE_ARCHIVE"),
            ("npm-drift", "BUN_SURFACE_MISMATCH"),
        ):
            with self.subTest(mutation=mutation):
                otool = self.root / f"otool-{mutation}"
                make_otool(otool)
                fixture = self.fixture(
                    "darwin-arm64", otool=otool, unsafe_archive=mutation
                )
                code, report = self.run_gate(fixture, otool_path=otool)
                self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
                self.assertEqual(report["errors"][0]["code"], expected)

    @unittest.skipUnless(
        sys.platform == "darwin",
        "structural otool admission is intentionally macOS-only",
    )
    def test_darwin_rejects_malformed_output_and_otool_identity_drift(self) -> None:
        otool = self.root / "otool"
        make_otool(otool, malformed=True)
        fixture = self.fixture("darwin-arm64", otool=otool)
        code, report = self.run_gate(fixture, otool_path=otool)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "INVALID_OTOOL_OUTPUT")

        make_otool(otool, malformed=False)
        code, report = self.run_gate(fixture, otool_path=otool)
        self.assertEqual(code, subject.EXIT_INPUT_REJECTED)
        self.assertEqual(report["errors"][0]["code"], "TOOL_IDENTITY_MISMATCH")


if __name__ == "__main__":
    unittest.main()
