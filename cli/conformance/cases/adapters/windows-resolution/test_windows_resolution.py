from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import jsonschema
from referencing import Registry, Resource

import resolver_model
from resolver_model import (
    ResolutionError,
    build_command,
    load_json_strict,
    load_oracle,
    resolve_profile,
    validate_profile,
    validate_version_output,
)


ROOT = Path(__file__).resolve().parent
ORACLE_PATH = ROOT / "oracle.v1.json"
ORACLE_SCHEMA_PATH = ROOT / "oracle.schema.json"
PROFILE_SCHEMA_PATH = ROOT / "launch-profile.schema.json"
HOSTILE_FIXTURES = ROOT / "fixtures" / "hostile-bin"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pe(machine: bytes = b"\x64\x86", payload: bytes = b"fixture") -> bytes:
    # The provider-free model checks only the bounded shape needed by this
    # oracle. Native product tests remain responsible for full PE parsing.
    data = bytearray(128)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (64).to_bytes(4, "little")
    data[64:68] = b"PE\0\0"
    data[68:70] = machine
    data.extend(payload)
    return bytes(data)


class WindowsResolutionOracleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.oracle = load_oracle(ORACLE_PATH)

    def test_oracle_is_closed_blocker_honest_and_non_admitting(self) -> None:
        self.assertEqual("openprose.windows-resolution-oracle/1", self.oracle["schema"])
        self.assertFalse(self.oracle["claims"]["productAdmission"])
        self.assertFalse(self.oracle["claims"]["strictWrapperAdmission"])
        self.assertFalse(self.oracle["claims"]["semanticConformance"])
        self.assertFalse(self.oracle["claims"]["releaseEligibility"])
        self.assertEqual(
            {"codex/exec-json", "claude/print-stream-json"},
            {profile["adapterId"] for profile in self.oracle["researchProfiles"]},
        )
        self.assertTrue(all(profile["status"] == "research-feasible-non-admitted" for profile in self.oracle["researchProfiles"]))
        self.assertEqual(
            {"prime/rpc", "omp/rpc"},
            {blocker["adapterId"] for blocker in self.oracle["blockedAdapters"]},
        )
        self.assertTrue(all(blocker["windowsLaunchFeasible"] is False for blocker in self.oracle["blockedAdapters"]))
        for profile in self.oracle["researchProfiles"] + self.oracle["providerFreeProfiles"]:
            validate_profile(profile)

    def test_machine_readable_schemas_are_closed_and_no_process_api_exists(self) -> None:
        oracle_schema = load_json_strict(ORACLE_SCHEMA_PATH.read_bytes())
        profile_schema = load_json_strict(PROFILE_SCHEMA_PATH.read_bytes())
        for schema in (oracle_schema, profile_schema):
            self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
            self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(3, len(profile_schema["properties"]["resolution"]["oneOf"]))
        self.assertEqual(False, oracle_schema["properties"]["claims"]["properties"]["productAdmission"]["const"])

        tree = ast.parse((ROOT / "resolver_model.py").read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({"subprocess", "asyncio"}.isdisjoint(imported))
        forbidden_calls = {"system", "popen", "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "execv", "execve"}
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(forbidden_calls.isdisjoint(called_attributes))

    def test_oracle_validates_against_local_schemas_without_network_retrieval(self) -> None:
        oracle_schema = load_json_strict(ORACLE_SCHEMA_PATH.read_bytes())
        profile_schema = load_json_strict(PROFILE_SCHEMA_PATH.read_bytes())
        retrievals: list[str] = []

        def reject_network(uri: str) -> Resource:
            retrievals.append(uri)
            raise AssertionError(f"unexpected schema retrieval: {uri}")

        registry = Registry(retrieve=reject_network).with_resource(
            "https://openprose.dev/schemas/windows-launch-profile-v1.json",
            Resource.from_contents(profile_schema),
        )
        validator = jsonschema.Draft202012Validator(
            oracle_schema,
            registry=registry,
            format_checker=jsonschema.FormatChecker(),
        )

        validator.validate(self.oracle)
        self.assertEqual([], retrievals)

    def test_direct_native_resolution_ignores_hostile_shell_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "SHELL_EXECUTED"
            for fixture in HOSTILE_FIXTURES.iterdir():
                shutil.copyfile(fixture, bin_dir / fixture.name)
            application = pe(payload=b"direct")
            (bin_dir / "agent.exe").write_bytes(application)
            profile = self._direct_profile(application)

            opened: list[Path] = []
            original_open = os.open

            def observed_open(path: os.PathLike[str] | str, flags: int, *args: object) -> int:
                opened.append(Path(path))
                return original_open(path, flags, *args)

            with patch.object(resolver_model.os, "open", side_effect=observed_open):
                resolved = resolve_profile(profile, [bin_dir], marker)

            self.assertEqual((bin_dir / "agent.exe").resolve(), Path(resolved["application"]))
            self.assertEqual([], resolved["argvPrefix"])
            self.assertFalse(marker.exists())
            self.assertTrue(all(not item.lower().endswith((".cmd", ".bat", ".ps1")) for item in resolved["readPaths"]))
            self.assertTrue(opened)
            self.assertTrue(all(item.suffix.lower() not in {".cmd", ".bat", ".ps1"} for item in opened))

    def test_native_package_resolution_authenticates_package_not_search_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            package = bin_dir / "node_modules" / "example-native-agent"
            package.mkdir(parents=True)
            application = pe(payload=b"package-native")
            manifest = canonical_json({"name": "example-native-agent", "version": "1.2.3"})
            (package / "agent.exe").write_bytes(application)
            (package / "package.json").write_bytes(manifest)
            profile = self._native_package_profile(application, manifest)

            resolved = resolve_profile(profile, [bin_dir], root / "marker")

            self.assertEqual((package / "agent.exe").resolve(), Path(resolved["application"]))
            self.assertEqual({"EXAMPLE_PACKAGE_ROOT": str(package.resolve())}, resolved["fixedEnvironment"])

    def test_runtime_entrypoint_binds_runtime_manifest_environment_and_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            package = bin_dir / "node_modules" / "example-runtime-agent"
            entrypoint = package / "dist" / "cli.js"
            entrypoint.parent.mkdir(parents=True)
            runtime = pe(payload=b"runtime")
            entry = b"export const fixture = true;\n"
            manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"})
            (bin_dir / "node.exe").write_bytes(runtime)
            entrypoint.write_bytes(entry)
            (package / "package.json").write_bytes(manifest)
            profile = self._runtime_profile(runtime, entry, manifest)

            resolved = resolve_profile(profile, [bin_dir], root / "marker")

            self.assertEqual((bin_dir / "node.exe").resolve(), Path(resolved["application"]))
            self.assertEqual([str(entrypoint.resolve())], resolved["argvPrefix"])
            self.assertEqual({"OPENPROSE_RUNTIME_PROFILE": "fixture-v1"}, resolved["fixedEnvironment"])
            self.assertEqual(["USERPROFILE"], resolved["inheritedEnvironmentNames"])
            self.assertEqual(["OPENPROSE_TOKEN"], resolved["strippedEnvironmentNames"])
            self.assertEqual(
                [str((bin_dir / "node.exe").resolve()), str(entrypoint.resolve()), "--version"],
                build_command(resolved, ["--version"]),
            )
            self.assertEqual(
                build_command(resolved, ["--version"])[0:2],
                build_command(resolved, ["--mode", "rpc"])[0:2],
            )
            self.assertTrue(validate_version_output(profile, "agent 1.2.3"))
            self.assertFalse(validate_version_output(profile, "agent 1.2.4"))

    def test_authorized_package_root_junction_is_canonicalized(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            store = root / "store" / "example-runtime-agent"
            (store / "dist").mkdir(parents=True)
            (bin_dir / "node_modules").mkdir(parents=True)
            runtime = pe(payload=b"junction-runtime")
            entry = b"export {};\n"
            manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"})
            (bin_dir / "node.exe").write_bytes(runtime)
            (store / "dist" / "cli.js").write_bytes(entry)
            (store / "package.json").write_bytes(manifest)
            try:
                (bin_dir / "node_modules" / "example-runtime-agent").symlink_to(store, target_is_directory=True)
            except OSError as caught:
                self.skipTest(f"directory links unavailable: {caught}")

            resolved = resolve_profile(self._runtime_profile(runtime, entry, manifest), [bin_dir], root / "marker")
            self.assertEqual([str((store / "dist" / "cli.js").resolve())], resolved["argvPrefix"])

    def test_final_artifact_link_is_rejected_even_when_target_bytes_match(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            target = root / "real.exe"
            application = pe(payload=b"link")
            target.write_bytes(application)
            try:
                (bin_dir / "agent.exe").symlink_to(target)
            except OSError as caught:
                self.skipTest(f"file links unavailable: {caught}")
            with self.assertRaisesRegex(ResolutionError, "final artifact link"):
                resolve_profile(self._direct_profile(application), [bin_dir], root / "marker")

    def test_intermediate_artifact_directory_link_cannot_escape_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            package = bin_dir / "node_modules" / "example-runtime-agent"
            external = root / "external"
            package.mkdir(parents=True)
            external.mkdir()
            runtime = pe(payload=b"intermediate-link-runtime")
            entry = b"export const escaped = true;\n"
            manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"})
            (bin_dir / "node.exe").write_bytes(runtime)
            (external / "cli.js").write_bytes(entry)
            (package / "package.json").write_bytes(manifest)
            try:
                (package / "dist").symlink_to(external, target_is_directory=True)
            except OSError as caught:
                self.skipTest(f"directory links unavailable: {caught}")

            with self.assertRaisesRegex(ResolutionError, "intermediate artifact link"):
                resolve_profile(self._runtime_profile(runtime, entry, manifest), [bin_dir], root / "marker")

    def test_manifest_traversal_duplicate_keys_and_tamper_fail_closed(self) -> None:
        duplicate = b'{"name":"a","name":"b"}\n'
        with self.assertRaisesRegex(ResolutionError, "duplicate JSON key"):
            load_json_strict(duplicate)

        runtime = pe(payload=b"runtime")
        entry = b"export {};\n"
        manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"})
        traversal = self._runtime_profile(runtime, entry, manifest)
        traversal["resolution"]["entrypoint"] = "../escape.js"
        with self.assertRaisesRegex(ResolutionError, "relative path"):
            validate_profile(traversal)

        layout_traversal = self._runtime_profile(runtime, entry, manifest)
        layout_traversal["resolution"]["packageRootLayouts"] = ["../{packageName}"]
        with self.assertRaisesRegex(ResolutionError, "relative path"):
            validate_profile(layout_traversal)

        manifest_path_mismatch = self._runtime_profile(runtime, entry, manifest)
        manifest_path_mismatch["resolution"]["manifestPath"] = "metadata.json"
        with self.assertRaisesRegex(ResolutionError, "manifest path"):
            validate_profile(manifest_path_mismatch)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            package = bin_dir / "node_modules" / "example-runtime-agent"
            (package / "dist").mkdir(parents=True)
            (bin_dir / "node.exe").write_bytes(runtime)
            (package / "dist" / "cli.js").write_bytes(entry + b"tampered")
            (package / "package.json").write_bytes(manifest)
            with self.assertRaisesRegex(ResolutionError, "artifact digest"):
                resolve_profile(self._runtime_profile(runtime, entry, manifest), [bin_dir], root / "marker")

    def test_pe_machine_is_bound_even_when_digest_matches(self) -> None:
        wrong_machine = pe(machine=b"\x64\xaa", payload=b"arm64")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "agent.exe").write_bytes(wrong_machine)
            with self.assertRaisesRegex(ResolutionError, "PE machine"):
                resolve_profile(self._direct_profile(wrong_machine), [bin_dir], root / "marker")

    def test_ambiguity_and_manifest_version_mismatch_fail_closed(self) -> None:
        application = pe(payload=b"ambiguous")
        profile = self._direct_profile(application)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "agent.exe").write_bytes(application)
            (second / "agent.exe").write_bytes(application)
            with self.assertRaisesRegex(ResolutionError, "ambiguous"):
                resolve_profile(profile, [first, second], root / "marker")

        runtime = pe(payload=b"version")
        entry = b"export {};\n"
        expected_manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"})
        wrong_manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "9.9.9"})
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bin_dir = root / "bin"
            package = bin_dir / "node_modules" / "example-runtime-agent"
            (package / "dist").mkdir(parents=True)
            (bin_dir / "node.exe").write_bytes(runtime)
            (package / "dist" / "cli.js").write_bytes(entry)
            (package / "package.json").write_bytes(wrong_manifest)
            profile = self._runtime_profile(runtime, entry, expected_manifest)
            manifest_artifact = next(item for item in profile["artifacts"] if item["role"] == "package-manifest")
            manifest_artifact["size"] = len(wrong_manifest)
            manifest_artifact["sha256"] = digest(wrong_manifest)
            with self.assertRaisesRegex(ResolutionError, "manifest"):
                resolve_profile(profile, [bin_dir], root / "marker")

    def test_profile_mutations_cannot_silently_change_identity_or_launch(self) -> None:
        runtime = pe(payload=b"mutation")
        entry = b"export {};\n"
        manifest = canonical_json({"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"})
        for field, value in (
            ("argvPrefix", []),
            ("fixedEnvironment", [{"name": "Path", "value": "hostile"}]),
            ("inheritedEnvironmentNames", ["*"]),
        ):
            profile = self._runtime_profile(runtime, entry, manifest)
            profile[field] = value
            with self.subTest(field=field), self.assertRaises(ResolutionError):
                validate_profile(profile)

        native = self._direct_profile(pe())
        native["resolution"]["application"] = "agent.com"
        native["artifacts"][0]["path"] = "agent.com"
        with self.assertRaisesRegex(ResolutionError, "not an .exe"):
            validate_profile(native)

    def _direct_profile(self, application: bytes) -> dict:
        return {
            "schema": "openprose.windows-launch-profile/1",
            "profileId": "provider-free/direct-v1",
            "adapterId": "fixture/direct",
            "status": "provider-free-fixture",
            "platform": "win32-x64",
            "harnessVersion": "1.2.3",
            "versionPattern": "^agent 1\\.2\\.3$",
            "resolution": {"kind": "native-executable", "commandNames": ["agent"], "application": "agent.exe"},
            "artifacts": [{"role": "application", "path": "agent.exe", "size": len(application), "sha256": digest(application), "format": "pe-x64"}],
            "manifest": None,
            "argvPrefix": [],
            "fixedEnvironment": [],
            "inheritedEnvironmentNames": ["USERPROFILE"],
            "strippedEnvironmentNames": ["OPENPROSE_TOKEN"],
            "claims": {"productAdmission": False, "semanticConformance": False, "releaseEligibility": False},
        }

    def _runtime_profile(self, runtime: bytes, entry: bytes, manifest: bytes) -> dict:
        return {
            "schema": "openprose.windows-launch-profile/1",
            "profileId": "provider-free/runtime-v1",
            "adapterId": "fixture/runtime",
            "status": "provider-free-fixture",
            "platform": "win32-x64",
            "harnessVersion": "1.2.3",
            "versionPattern": "^agent 1\\.2\\.3$",
            "resolution": {
                "kind": "runtime-entrypoint",
                "commandNames": ["agent"],
                "runtimeApplication": "node.exe",
                "packageName": "example-runtime-agent",
                "packageRootLayouts": ["node_modules/{packageName}"],
                "entrypoint": "dist/cli.js",
                "manifestPath": "package.json",
            },
            "artifacts": [
                {"role": "application", "path": "node.exe", "size": len(runtime), "sha256": digest(runtime), "format": "pe-x64"},
                {"role": "entrypoint", "path": "dist/cli.js", "size": len(entry), "sha256": digest(entry), "format": "utf8-lf"},
                {"role": "package-manifest", "path": "package.json", "size": len(manifest), "sha256": digest(manifest), "format": "canonical-json-lf"},
            ],
            "manifest": {
                "artifactRole": "package-manifest",
                "match": "exact-object",
                "expected": {"bin": {"agent": "dist/cli.js"}, "name": "example-runtime-agent", "version": "1.2.3"},
            },
            "argvPrefix": [{"artifactRole": "entrypoint"}],
            "fixedEnvironment": [{"name": "OPENPROSE_RUNTIME_PROFILE", "value": "fixture-v1"}],
            "inheritedEnvironmentNames": ["USERPROFILE"],
            "strippedEnvironmentNames": ["OPENPROSE_TOKEN"],
            "claims": {"productAdmission": False, "semanticConformance": False, "releaseEligibility": False},
        }

    def _native_package_profile(self, application: bytes, manifest: bytes) -> dict:
        return {
            "schema": "openprose.windows-launch-profile/1",
            "profileId": "provider-free/native-package-v1",
            "adapterId": "fixture/native-package",
            "status": "provider-free-fixture",
            "platform": "win32-x64",
            "harnessVersion": "1.2.3",
            "versionPattern": "^agent 1\\.2\\.3$",
            "resolution": {
                "kind": "native-executable",
                "commandNames": ["agent"],
                "application": "agent.exe",
                "packageName": "example-native-agent",
                "packageRootLayouts": ["node_modules/{packageName}"],
                "manifestPath": "package.json",
            },
            "artifacts": [
                {"role": "application", "path": "agent.exe", "size": len(application), "sha256": digest(application), "format": "pe-x64"},
                {"role": "package-manifest", "path": "package.json", "size": len(manifest), "sha256": digest(manifest), "format": "canonical-json-lf"},
            ],
            "manifest": {
                "artifactRole": "package-manifest",
                "match": "exact-object",
                "expected": {"name": "example-native-agent", "version": "1.2.3"},
            },
            "argvPrefix": [],
            "fixedEnvironment": [{"name": "EXAMPLE_PACKAGE_ROOT", "value": "{packageRoot}"}],
            "inheritedEnvironmentNames": ["USERPROFILE"],
            "strippedEnvironmentNames": ["OPENPROSE_TOKEN"],
            "claims": {"productAdmission": False, "semanticConformance": False, "releaseEligibility": False},
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
