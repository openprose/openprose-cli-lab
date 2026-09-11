from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

import build_local
import package_lifecycle


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def package_digests(root: Path) -> dict[str, str]:
    return {
        path.name: digest(path.read_bytes())
        for path in sorted(root.iterdir())
        if path.is_file() and not path.is_symlink()
    }


class FakeBenchmarkError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CorruptionProbeBenchmark:
    MAX_EVIDENCE_BYTES = 1024

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def safe_read(path: Path, maximum: int) -> bytes:
        value = path.read_bytes()
        if not 0 < len(value) <= maximum:
            raise AssertionError("unsafe test read")
        return value

    def run_benchmark(self, package: Path, install: Path, **_kwargs):
        self.calls += 1
        self.asserted_install = install
        self.asserted_corruption = (package / "artifact.tar.gz").read_bytes()
        raise FakeBenchmarkError("CHECKSUM_MISMATCH")

    @staticmethod
    def live_owned_probe_descriptions() -> list[str]:
        return []


class PackageLifecycleTests(unittest.TestCase):
    def test_tree_identity_enumerates_incrementally_without_rglob_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nested").mkdir()
            (root / "nested" / "member.txt").write_text("bounded", "utf-8")
            with mock.patch.object(
                Path,
                "rglob",
                side_effect=AssertionError("rglob must not materialize the tree"),
            ):
                observed = package_lifecycle.tree_identity(root)
            self.assertRegex(observed, r"^[0-9a-f]{64}$")

    def test_corruption_probe_changes_only_owned_copy_and_refuses_before_install(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            package.mkdir()
            encoded = {
                "artifact.tar.gz": b"valid archive bytes",
                "evidence.json": b"{}\n",
            }
            for name, value in encoded.items():
                (package / name).write_bytes(value)
            sums = "".join(
                f"{digest(value)}  {name}\n" for name, value in sorted(encoded.items())
            ).encode("ascii")
            (package / "SHA256SUMS").write_bytes(sums)
            context = {
                "root": package,
                "platform": "linux-x64-gnu",
                "encoded": encoded,
                "checksums": {name: digest(value) for name, value in encoded.items()},
            }
            benchmark = CorruptionProbeBenchmark()
            result = package_lifecycle.probe_corrupted_member(
                benchmark,
                context,
                root / "owned-probe",
                "artifact.tar.gz",
                deadline_monotonic=time.monotonic() + 5,
                timeout_seconds=1,
            )
            self.assertEqual(result["errorCode"], "CHECKSUM_MISMATCH")
            self.assertEqual(result["executionStarted"], False)
            self.assertEqual(benchmark.calls, 1)
            self.assertEqual(benchmark.asserted_corruption, b"valid archive bytes\x00")
            self.assertFalse(benchmark.asserted_install.exists())
            self.assertEqual(
                (package / "artifact.tar.gz").read_bytes(), b"valid archive bytes"
            )

    def test_deadline_root_and_windows_boundaries_fail_before_package_work(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for invalid in (float("nan"), float("inf"), time.monotonic() - 1):
                with self.subTest(deadline=invalid), self.assertRaises(
                    package_lifecycle.LifecycleError
                ):
                    package_lifecycle.run_lifecycle(
                        root / "missing-package",
                        root / f"work-{len(str(invalid))}",
                        deadline_monotonic=invalid,
                    )
            occupied = root / "occupied"
            occupied.mkdir()
            with self.assertRaises(package_lifecycle.LifecycleError):
                package_lifecycle.prepare_owned_root(occupied)
            with mock.patch.object(package_lifecycle.os, "name", "nt"):
                with self.assertRaisesRegex(
                    package_lifecycle.LifecycleError, "Job Object"
                ):
                    package_lifecycle.run_lifecycle(
                        root / "missing-package",
                        root / "windows-work",
                        deadline_monotonic=time.monotonic() + 5,
                    )
            self.assertFalse((root / "windows-work").exists())

    def test_lifecycle_environment_is_closed_and_credential_free(self) -> None:
        class FakeBenchmark:
            @staticmethod
            def clean_environment(root: Path, tool_paths: list[Path]):
                return {
                    "HOME": str(root / "home"),
                    "PATH": os.pathsep.join(map(str, tool_paths)),
                    "LANG": "C",
                }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "secret-openai",
                    "ANTHROPIC_API_KEY": "secret-anthropic",
                    "NODE_AUTH_TOKEN": "secret-npm",
                },
                clear=False,
            ):
                environment = package_lifecycle.npm_environment(
                    FakeBenchmark(), root, [Path("/tool/bin")], root / "npm-cache"
                )
            encoded = json.dumps(environment, sort_keys=True)
            self.assertNotIn("secret-openai", encoded)
            self.assertNotIn("secret-anthropic", encoded)
            self.assertNotIn("secret-npm", encoded)
            self.assertEqual(environment["npm_config_offline"], "true")
            self.assertEqual(environment["npm_config_ignore_scripts"], "true")
            self.assertEqual(environment["npm_config_registry"], "http://127.0.0.1:9/")
            self.assertTrue(str(environment["npm_config_cache"]).startswith(str(root)))

    @unittest.skipIf(
        os.name == "nt", "Windows lifecycle is pre-spawn until Job Object authority"
    )
    def test_one_real_built_package_completes_the_owned_lifecycle(self) -> None:
        if not all(shutil.which(name) is not None for name in ("cargo", "bun")):
            self.skipTest("cargo and bun are required for the real package lifecycle")
        with tempfile.TemporaryDirectory(
            prefix="openprose-package-lifecycle-test-"
        ) as temporary:
            root = Path(temporary)
            package = root / "package"
            outside = root / "outside-sentinel"
            outside.write_bytes(b"must remain unchanged\n")
            clean_ambient = build_local.clean_environment(os.environ)
            build_local.build(
                selection="both",
                smoke=False,
                package=package,
                install_dir=None,
                ambient=clean_ambient,
                package_purpose=build_local.MOCK_PACKAGE_PURPOSE,
                executor=lambda argv, cwd, environment: build_local.execute_bounded(
                    argv, cwd, environment, timeout_seconds=600
                ),
            )
            package_before = package_digests(package)
            report = package_lifecycle.run_lifecycle(
                package,
                root / "lifecycle",
                timeout_seconds=10,
                deadline_monotonic=time.monotonic() + 180,
            )
            self.assertEqual(report["schema"], "openprose.package-lifecycle-report/1")
            self.assertEqual(
                report["status"], "passed-provider-free-development-lifecycle"
            )
            self.assertEqual(
                [check["name"] for check in report["checks"]],
                [
                    "fresh-install",
                    "non-overwrite-refusal",
                    "npm-uninstall-reinstall",
                    "corrupt-standalone-archive",
                    "corrupt-npm-tarball",
                ],
            )
            self.assertTrue(
                all(check["status"] == "passed" for check in report["checks"])
            )
            self.assertEqual(report["claims"]["releaseEligible"], False)
            self.assertEqual(report["claims"]["semanticEvaluation"], "not-performed")
            self.assertEqual(report["claims"]["providerCalls"], "none")
            self.assertEqual(
                report["claims"]["detachedDescendantContainment"],
                "not-enforced",
            )
            npm_check = next(
                check
                for check in report["checks"]
                if check["name"] == "npm-uninstall-reinstall"
            )
            self.assertRegex(npm_check["treeSha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(package_digests(package), package_before)
            self.assertEqual(outside.read_bytes(), b"must remain unchanged\n")
            self.assertEqual(
                package_lifecycle.load_benchmark().live_owned_probe_descriptions(), []
            )


if __name__ == "__main__":
    unittest.main()
