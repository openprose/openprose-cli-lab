from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODULE_PATH = HERE / "check_registry_lineage.py"
AUTHORITY = ROOT / "cli" / "release" / "npm-registry-lineage.v1.json"
RELEASE_README = ROOT / "cli" / "release" / "README.md"
CLI_README = ROOT / "cli" / "README.md"
MIGRATION_RUNBOOK = ROOT / "cli" / "release" / "MIGRATION_AND_ROLLBACK.md"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "openprose_check_registry_lineage", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RegistryLineageTests(unittest.TestCase):
    def test_pinned_public_lineage_admits_only_the_numbered_successor_alpha(self) -> None:
        checker = load_module()
        for version in (None, "0.15.0-alpha.1", "0.15.0-alpha.2", "0.15.0-alpha.99"):
            with self.subTest(version=version):
                report = checker.assess(AUTHORITY, version)
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["canonicalFirstVersion"], "0.15.0-alpha.1")
                self.assertEqual(report["preexistingLatest"], "0.14.0")
                self.assertEqual(report["distributionTag"], "alpha")
                self.assertTrue(report["liveRegistryRevalidationRequired"])
                self.assertFalse(report["networkUsed"])
                self.assertEqual(report["failures"], [])

        for version in (
            "0.1.0-alpha.1",
            "0.14.1-alpha.1",
            "0.15.0",
            "0.15.0-alpha.0",
            "0.15.0-alpha.01",
            "0.15.0-beta.1",
            "0.15.1-alpha.1",
            "1.0.0-alpha.1",
        ):
            with self.subTest(version=version):
                report = checker.assess(AUTHORITY, version)
                self.assertEqual(report["status"], "fail")
                self.assertTrue(report["failures"])

    def test_authority_binds_the_observed_existing_package_and_unused_platform_names(self) -> None:
        checker = load_module()
        authority = checker.load_authority(AUTHORITY)
        checker.validate_authority(authority)
        self.assertEqual(
            authority["metaPackage"]["publishedVersions"],
            [
                "0.1.0",
                "0.1.1",
                "0.1.2",
                "0.1.3",
                "0.1.4",
                "0.2.5",
                "0.13.0",
                "0.13.1",
                "0.14.0",
            ],
        )
        self.assertEqual(
            authority["metaPackage"]["owners"],
            ["jose_at_prose", "dan_openprose"],
        )
        self.assertEqual(
            [record["name"] for record in authority["platformPackages"]],
            [
                "@openprose/prose-cli-darwin-arm64",
                "@openprose/prose-cli-darwin-x64",
                "@openprose/prose-cli-linux-arm64-gnu",
                "@openprose/prose-cli-linux-x64-gnu",
            ],
        )
        self.assertTrue(
            all(
                record["status"] == "not-found"
                for record in authority["platformPackages"]
            )
        )

    def test_lineage_mutations_fail_closed(self) -> None:
        checker = load_module()
        original = json.loads(AUTHORITY.read_text("utf-8"))
        mutations = {
            "retrograde-latest": lambda value: value["metaPackage"].__setitem__(
                "latest", "0.13.1"
            ),
            "missing-history": lambda value: value["metaPackage"][
                "publishedVersions"
            ].pop(),
            "bad-integrity": lambda value: value["metaPackage"].__setitem__(
                "latestIntegrity", "sha512-not-an-integrity"
            ),
            "owner-drift": lambda value: value["metaPackage"].__setitem__(
                "owners", ["unobserved-owner"]
            ),
            "used-platform": lambda value: value["platformPackages"][0].__setitem__(
                "status", "published"
            ),
            "wrong-successor": lambda value: value["successor"].__setitem__(
                "firstVersion", "0.14.1-alpha.1"
            ),
            "unsafe-tag": lambda value: value["successor"].__setitem__(
                "distributionTag", "latest"
            ),
            "network-not-required": lambda value: value["releaseRequirements"].__setitem__(
                "liveRegistryRevalidation", False
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    value = json.loads(json.dumps(original))
                    mutate(value)
                    path = root / f"{name}.json"
                    path.write_text(json.dumps(value), "utf-8")
                    report = checker.assess(path, "0.15.0-alpha.1")
                    self.assertEqual(report["status"], "fail")
                    self.assertTrue(report["failures"])

    def test_duplicate_json_keys_and_symlink_authorities_are_rejected(self) -> None:
        checker = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"schema":"one","schema":"two"}', "utf-8")
            report = checker.assess(duplicate, "0.15.0-alpha.1")
            self.assertEqual(report["status"], "fail")
            self.assertIn("duplicate", " ".join(report["failures"]).lower())

            alias = root / "alias.json"
            alias.symlink_to(AUTHORITY)
            report = checker.assess(alias, "0.15.0-alpha.1")
            self.assertEqual(report["status"], "fail")
            self.assertIn("regular file", " ".join(report["failures"]).lower())

    def test_cli_is_deterministic_provider_free_and_machine_readable(self) -> None:
        command = [
            sys.executable,
            str(MODULE_PATH),
            "--authority",
            str(AUTHORITY),
            "--version",
            "0.15.0-alpha.1",
        ]
        first = subprocess.run(command, capture_output=True, check=False, timeout=10)
        second = subprocess.run(command, capture_output=True, check=False, timeout=10)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stderr, b"")
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report["schema"], "openprose.registry-lineage-admission/1")
        self.assertEqual(report["status"], "pass")

        rejected = subprocess.run(
            [*command[:-1], "0.1.0-alpha.1"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stderr, b"")
        self.assertEqual(json.loads(rejected.stdout)["status"], "fail")

        source = MODULE_PATH.read_text("utf-8")
        for forbidden in (
            "import urllib",
            "import requests",
            "import socket",
            "import subprocess",
            "os.system(",
            "os.popen(",
        ):
            self.assertNotIn(forbidden, source)

    def test_release_docs_follow_the_registry_lineage_and_rollback_policy(self) -> None:
        readme = RELEASE_README.read_text("utf-8")
        cli_readme = CLI_README.read_text("utf-8")
        runbook = MIGRATION_RUNBOOK.read_text("utf-8")

        self.assertIn("0.15.0-alpha.1", readme)
        self.assertNotIn("0.1.0-alpha.1", readme)
        self.assertIn("npm-registry-lineage.v1.json", readme)
        self.assertIn("MIGRATION_AND_ROLLBACK.md", readme)
        self.assertNotIn("0.15.0-alpha.1", cli_readme)
        self.assertIn('ALPHA_PREFIX="$HOME/.local/openprose-cli-$ALPHA_VERSION"', cli_readme)
        self.assertNotIn("0.1.0-alpha.1", cli_readme)

        # This is the separate development-mode packaging example, not a
        # functional-alpha release identity.
        normalized_readme = " ".join(readme.split())
        self.assertIn(
            "With `VERSION=0.1.0`, the development meta-package filename is "
            "exactly `openprose-prose-cli-0.1.0.tgz`.",
            normalized_readme,
        )

        for required in (
            "0.14.0",
            "0.15.0-alpha.1",
            "platform packages before the meta package",
            "preserve `latest`",
            "deprecate",
            "never reuse",
            "live registry",
            "Public-alpha-ready",
        ):
            with self.subTest(required=required):
                self.assertIn(required, runbook)
        normalized_runbook = " ".join(runbook.split())
        for required in (
            "Source code and workflow tests do not prove these external controls",
            "The repository's alpha release environment",
            "have been independently verified",
        ):
            with self.subTest(stage_neutral_required=required):
                self.assertIn(required, normalized_runbook)
        self.assertNotIn("No publication has been performed", runbook)

        functional_alpha = readme.split("## Functional-alpha package", 1)[1].split(
            "## Development package", 1
        )[0]
        self.assertIn(
            '"$PACKAGE_DIR/openprose-prose-cli-$VERSION.tgz"', functional_alpha
        )
        self.assertNotIn("openprose-prose-cli-0.15.0-alpha.1.tgz", functional_alpha)
        self.assertNotIn("openprose-cli-0.15.0-alpha.1", functional_alpha)
        self.assertEqual(functional_alpha.count('PROSE="$INSTALL_PREFIX/bin/prose"'), 5)
        self.assertEqual(
            functional_alpha.count(
                'EXAMPLE="$INSTALL_PREFIX/lib/node_modules/@openprose/prose-cli/examples/hello.prose.md"'
            ),
            5,
        )
        future_alpha = functional_alpha.replace(
            "VERSION=0.15.0-alpha.1", "VERSION=0.15.0-alpha.37"
        )
        self.assertNotIn("openprose-cli-0.15.0-alpha.1", future_alpha)
        self.assertIn('INSTALL_PREFIX="$HOME/.local/openprose-cli-$VERSION"', future_alpha)
        journey_blocks = [
            block.split("```", 1)[0]
            for block in functional_alpha.split("```sh")[1:]
            if 'PROSE="$INSTALL_PREFIX/bin/prose"' in block.split("```", 1)[0]
        ]
        self.assertEqual(len(journey_blocks), 5)
        for index, block in enumerate(journey_blocks):
            prefix_lines: list[str] = []
            for line in block.splitlines():
                prefix_lines.append(line)
                if line.startswith("EXAMPLE="):
                    break
            result = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    "\n".join(prefix_lines)
                    + "\nprintf '%s\\n%s\\n' \"$PROSE\" \"$EXAMPLE\"\n",
                ],
                text=True,
                capture_output=True,
                env={"HOME": "/future-home", "VERSION": "0.15.0-alpha.37"},
                check=False,
            )
            with self.subTest(journey=index):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines(),
                    [
                        "/future-home/.local/openprose-cli-0.15.0-alpha.37/bin/prose",
                        "/future-home/.local/openprose-cli-0.15.0-alpha.37/lib/node_modules/@openprose/prose-cli/examples/hello.prose.md",
                    ],
                )
        self.assertNotIn(
            "No tag-creation, push, npm/Cargo publication, or promotion stage exists",
            readme,
        )
        self.assertIn(
            "move the release entries out of `[Unreleased]` and into the exact dated section required by the CLI changelog before creating the version tag",
            " ".join(readme.split()),
        )


if __name__ == "__main__":
    unittest.main()
