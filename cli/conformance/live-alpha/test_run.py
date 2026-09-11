from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from io import StringIO
from unittest import mock

import jsonschema


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "openprose_functional_alpha", HERE / "run.py"
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class FunctionalAlphaRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = os.environ.get("PATH", os.defpath)

    def tearDown(self) -> None:
        os.environ["PATH"] = self.original_path

    def install_fake_codex(self, root: Path) -> None:
        prefix = root / "fake-codex-install"
        modules = prefix / "node_modules" / "@openai"
        package = modules / "codex"
        platform_id = RUNNER.admit_node_runtime(
            RUNNER.sanitized_base_environment(), root
        )[0]["platformId"]
        platform_name = platform_id.replace("-gnu", "").replace("-musl", "")
        platform_package = modules / f"codex-{platform_name}"
        launcher = package / "bin" / "codex.js"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/usr/bin/env node\n", "utf-8")
        launcher.chmod(0o755)
        package.joinpath("package.json").write_text(
            json.dumps(
                {
                    "name": "@openai/codex",
                    "version": "0.149.0-alpha.4.1",
                    "bin": {"codex": "bin/codex.js"},
                    "optionalDependencies": {
                        f"@openai/codex-{platform_name}": "0.149.0-alpha.4.1"
                    },
                }
            ),
            "utf-8",
        )
        native = platform_package / "vendor" / platform_name / "bin" / "codex"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"fixture codex native\n")
        native.chmod(0o755)
        platform_package.joinpath("package.json").write_text(
            json.dumps(
                {
                    "name": f"@openai/codex-{platform_name}",
                    "version": "0.149.0-alpha.4.1",
                }
            ),
            "utf-8",
        )
        bin_root = prefix / "bin"
        bin_root.mkdir()
        (bin_root / "codex").symlink_to(launcher)
        os.environ["PATH"] = f"{bin_root}{os.pathsep}{self.original_path}"

    def fake_candidate(self, root: Path) -> Path:
        self.install_fake_codex(root)
        candidate = root / "prose-fixture"
        source = textwrap.dedent(
            r"""
                #!/usr/bin/env python3
                import hashlib, json, os, pathlib, sys

                args = sys.argv[1:]
                assert not any(
                    name.startswith("OPENPROSE_CONFORMANCE_")
                    for name in os.environ
                )
                if "OPENAI_API_KEY" in os.environ:
                    assert os.environ["OPENAI_API_KEY"] == "explicit-test-credential"
                assert args[:2] == ["--output", "json"]
                args = args[2:]
                config_root = pathlib.Path(os.environ["XDG_CONFIG_HOME"])
                config = config_root / "openprose" / "cli.toml"
                if args[:3] == ["cli", "harness", "use"]:
                    harness = args[3]
                    assert args[4:] == [
                        "--model", "fixture-model",
                        "--auth-profile", "cached-chatgpt-login",
                    ]
                    assert "PROSE_MODEL" not in os.environ
                    assert "PROSE_AUTH_PROFILE" not in os.environ
                    config.parent.mkdir(parents=True, exist_ok=True)
                    config.write_text(
                        'auth_profile = "cached-chatgpt-login"\n'
                        f'harness = "{harness}"\n'
                        'model = "fixture-model"\n',
                        encoding="utf-8",
                    )
                    value = {
                      "schema":"openprose.harness-selection/1",
                      "harness":harness,
                      "scope":"user",
                      "path":str(config),
                      "changed":True,
                    }
                else:
                    assert "--model" not in args
                    assert "--auth-profile" not in args
                    assert "PROSE_MODEL" not in os.environ
                    assert "PROSE_AUTH_PROFILE" not in os.environ
                    persisted = config.read_text(encoding="utf-8")
                    assert persisted == (
                        'auth_profile = "cached-chatgpt-login"\n'
                        'harness = "codex"\n'
                        'model = "fixture-model"\n'
                    )
                    harness = next(
                        line.split('"')[1]
                        for line in persisted.splitlines()
                        if line.startswith("harness = ")
                    )
                    if args[-2:] == ["cli", "doctor"]:
                        value = {
                          "schema":"openprose.doctor-report/1",
                          "runner":{"name":"bun","version":"0.1.0-alpha.1","commit":"fixture-commit"},
                          "ready":True,
                          "cwd":str(pathlib.Path.cwd()),
                          "selectedHarness":harness,
                          "selectedHarnessVersion":"codex-cli 0.149.0-alpha.4.1",
                          "selectedTransport":"exec-json",
                          "selectedAdapterId":"codex/exec-json",
                          "promptPlacement":"user-prefix-framed",
                          "isolation":"unsupported",
                          "authCategory":"harness-managed",
                          "billingOwner":"user-provider",
                          "image":{
                            "formatVersion":"openprose.skill-runtime-image/1",
                            "version":"echo-v0",
                            "sha256":"__IMAGE_SHA__",
                            "releaseEligible":True,
                          },
                        }
                    else:
                        program = args[-1]
                        task = {
                          "schema":"openprose.task-envelope/1",
                          "argv":["prose","run",program],
                          "interactionMode":"non-interactive",
                        }
                        task_bytes = json.dumps(
                          task,
                          ensure_ascii=False,
                          sort_keys=True,
                          separators=(",", ":"),
                        ).encode()
                        adapters = {
                          "prime":"prime/rpc",
                          "omp":"omp/rpc",
                          "codex":"codex/exec-json",
                          "claude":"claude/print-stream-json",
                        }
                        value = {
                          "schema":"openprose.runner-result/1",
                          "runner":{"name":"bun","version":"0.1.0-alpha.1","commit":"fixture-commit"},
                          "adapter":{
                            "id":adapters[harness],
                            "harnessVersion":"codex-cli 0.149.0-alpha.4.1",
                            "descriptorDigestSha256":"__CODEX_DIGEST__",
                          },
                          "transport":"exec-json",
                          "negotiatedCapabilities":{
                            "promptPlacement":"user-prefix-framed",
                            "isolation":"unsupported",
                          },
                          "languageImage":{
                            "formatVersion":"openprose.skill-runtime-image/1",
                            "version":"echo-v0",
                            "sha256":"__IMAGE_SHA__",
                          },
                          "digests":{
                            "taskSha256":hashlib.sha256(task_bytes).hexdigest(),
                            "deliveredImageSha256":"__DELIVERED_IMAGE_SHA__",
                          },
                          "cwd":{
                            "path":str(pathlib.Path.cwd()),
                            "identitySha256":hashlib.sha256(
                              str(pathlib.Path.cwd()).encode()
                            ).hexdigest(),
                          },
                          "terminal":{
                            "classification":"success",
                            "transportCompleted":True,
                            "terminalEventObserved":True,
                            "exitCode":0,
                            "signal":None,
                          },
                          "semantic":{
                            "status":"not-applicable",
                            "terminalSchemaSha256":"__TERMINAL_SCHEMA_SHA__",
                            "terminalEnvelopeDigestSha256":"a"*64,
                          },
                          "billing":{"owner":"user-provider","authCategory":"harness-managed"},
                          "runnerExitCode":0,
                        }
                print(json.dumps(value, sort_keys=True, separators=(",", ":")))
                """
        ).lstrip()
        recipe = (
            HERE.parents[1]
            / "shared/capabilities/adapters/recipes/codex-exec-json.v1.json"
        )
        recipe_digest = __import__("hashlib").sha256(recipe.read_bytes()).hexdigest()
        image = RUNNER.echo_image_contract()
        candidate.write_text(
            source.replace("__CODEX_DIGEST__", recipe_digest)
            .replace("__IMAGE_SHA__", image["image"]["sha256"])
            .replace("__DELIVERED_IMAGE_SHA__", image["deliveredImageSha256"])
            .replace("__TERMINAL_SCHEMA_SHA__", image["terminalSchemaSha256"]),
            "utf-8",
        )
        candidate.chmod(0o755)
        return candidate

    def fake_npm_candidate(self, root: Path) -> tuple[Path, dict[str, Path]]:
        source_candidate = self.fake_candidate(root)
        scope = root / "lib" / "node_modules" / "@openprose"
        meta_root = scope / "prose-cli"
        node, _ = RUNNER.admit_node_runtime(RUNNER.sanitized_base_environment(), root)
        platform_id = node["platformId"]
        platform_root = scope / f"prose-cli-{platform_id}"
        launcher = meta_root / "bin" / "prose.js"
        native = (
            platform_root
            / "bin"
            / ("prose.exe" if platform_id.startswith("win32-") else "prose")
        )
        launcher.parent.mkdir(parents=True)
        native.parent.mkdir(parents=True)
        launcher.write_bytes(source_candidate.read_bytes())
        launcher.chmod(0o755)
        native.write_bytes(b"fixture native executable\n")
        native.chmod(0o755)
        version = "0.1.0-alpha.1"
        meta_manifest = meta_root / "package.json"
        platform_manifest = platform_root / "package.json"
        meta_manifest.write_text(
            json.dumps(
                {
                    "name": "@openprose/prose-cli",
                    "version": version,
                    "bin": {"prose": "bin/prose.js"},
                    "optionalDependencies": {
                        f"@openprose/prose-cli-{platform_id}": version
                    },
                }
            ),
            "utf-8",
        )
        native_bytes = native.read_bytes()
        platform_manifest.write_text(
            json.dumps(
                {
                    "name": f"@openprose/prose-cli-{platform_id}",
                    "version": version,
                    "openproseBinary": f"bin/{native.name}",
                    "openproseBinaryByteLength": len(native_bytes),
                    "openproseBinarySha256": __import__("hashlib")
                    .sha256(native_bytes)
                    .hexdigest(),
                }
            ),
            "utf-8",
        )
        return launcher, {
            "launcher": launcher,
            "meta-package-json": meta_manifest,
            "platform-package-json": platform_manifest,
            "native-executable": native,
        }

    def test_provider_free_fake_exercises_persist_doctor_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate, npm_members = self.fake_npm_candidate(root)
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            output = root / "evidence.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "run.py"),
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--surface",
                    "npm",
                    "--program",
                    str(program),
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--acknowledge-provider-cost",
                    "--out",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                env={**os.environ, "OPENPROSE_CONFORMANCE_ADAPTER_MODE": "forged"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.encode("utf-8"), output.read_bytes())
            evidence = json.loads(completed.stdout)
            schema = json.loads((HERE / "evidence.schema.json").read_text("utf-8"))
            jsonschema.Draft202012Validator(schema).validate(evidence)
        self.assertEqual(evidence["harness"], "codex")
        self.assertEqual(evidence["surface"], "npm")
        self.assertEqual(
            evidence["schema"], "openprose.functional-alpha-live-evidence/5"
        )
        self.assertEqual("fixture-model", evidence["invocation"]["model"])
        self.assertEqual(
            {"profile": "cached-chatgpt-login", "category": "harness-login"},
            evidence["invocation"]["authRoute"],
        )
        self.assertEqual("npm-package", evidence["harnessCustody"]["distributionKind"])
        self.assertNotIn(str(root), json.dumps(evidence["harnessCustody"]))
        self.assertEqual(
            evidence["target"]["platformId"],
            evidence["candidate"]["npm"]["platformId"],
        )
        self.assertEqual(evidence["target"]["authority"], "exact-node-runtime-probe")
        self.assertEqual(
            evidence["candidate"]["schema"], "openprose.candidate-closure/1"
        )
        self.assertEqual(evidence["candidate"]["kind"], "npm")
        self.assertEqual(
            [member["role"] for member in evidence["candidate"]["members"]],
            [
                "launcher",
                "meta-package-json",
                "platform-package-json",
                "native-executable",
            ],
        )
        self.assertEqual(
            [member["path"] for member in evidence["candidate"]["members"]],
            [str(npm_members[role].resolve()) for role in npm_members],
        )
        self.assertEqual(
            evidence["candidate"]["npm"]["declaredBinarySha256"],
            evidence["candidate"]["members"][3]["sha256"],
        )
        self.assertEqual(
            evidence["candidate"]["npm"]["platformId"],
            RUNNER.node_platform_id(
                evidence["candidate"]["npm"]["node"]["platform"],
                evidence["candidate"]["npm"]["node"]["architecture"],
                evidence["candidate"]["npm"]["node"]["libc"],
            ),
        )
        self.assertEqual(
            evidence["candidate"]["npm"]["node"]["sha256"],
            __import__("hashlib")
            .sha256(Path(evidence["candidate"]["npm"]["node"]["path"]).read_bytes())
            .hexdigest(),
        )
        self.assertEqual(evidence["runner"]["name"], "bun")
        self.assertEqual(evidence["run"]["adapterId"], "codex/exec-json")
        self.assertEqual(
            evidence["run"]["harnessVersion"], "codex-cli 0.149.0-alpha.4.1"
        )
        self.assertEqual(evidence["run"]["admittedVersions"], ["0.149.0-alpha.4.1"])
        self.assertEqual(
            evidence["run"]["repairCommand"],
            "npm install --global @openai/codex@0.149.0-alpha.4.1",
        )
        self.assertEqual(
            evidence["run"]["deliveredImageSha256"],
            RUNNER.echo_image_contract()["deliveredImageSha256"],
        )
        self.assertTrue(evidence["configuration"]["ownedTemporaryRootRemoved"])
        self.assertEqual(evidence["claims"]["authority"], "candidate-reported-smoke")
        self.assertFalse(evidence["claims"]["openProseExecuted"])
        self.assertEqual(evidence["claims"]["providerCredentialCharged"], "unverified")

    def test_direct_surface_records_a_one_member_candidate_closure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                ]
            )
            evidence = RUNNER.run_smoke(options)
        self.assertEqual(evidence["candidate"]["kind"], "direct")
        self.assertEqual(len(evidence["candidate"]["members"]), 1)
        self.assertEqual(evidence["candidate"]["members"][0]["role"], "executable")

    def test_npm_closure_rejects_member_mutation_after_each_candidate_phase(
        self,
    ) -> None:
        phases = {
            "selection": "config.parent.mkdir(parents=True, exist_ok=True)",
            "doctor": 'if args[-2:] == ["cli", "doctor"]:',
            "run": "program = args[-1]",
        }
        targets = {
            "selection": "native-executable",
            "doctor": "meta-package-json",
            "run": "platform-package-json",
        }
        for phase, marker in phases.items():
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                candidate, members = self.fake_npm_candidate(root)
                mutation = (
                    f"pathlib.Path({str(members[targets[phase]])!r})"
                    f'.write_bytes(b"mutated-{phase}\\n")'
                )
                source = candidate.read_text("utf-8")
                if phase == "selection":
                    source = source.replace(marker, marker + "\n    " + mutation)
                elif phase == "doctor":
                    source = source.replace(marker, marker + "\n        " + mutation)
                else:
                    source = source.replace(marker, marker + "\n        " + mutation)
                candidate.write_text(source, "utf-8")
                program = root / "hello.prose.md"
                program.write_text("placeholder\n", "utf-8")
                options = RUNNER.parser().parse_args(
                    [
                        "--candidate",
                        str(candidate),
                        "--harness",
                        "codex",
                        "--model",
                        "fixture-model",
                        "--auth-profile",
                        "cached-chatgpt-login",
                        "--surface",
                        "npm",
                        "--program",
                        str(program),
                    ]
                )
                with self.assertRaisesRegex(
                    RUNNER.SmokeError,
                    "candidate closure .* changed after initial admission",
                ):
                    RUNNER.run_smoke(options)

    def test_npm_closure_rejects_symlinked_or_missing_members(self) -> None:
        for role in (
            "launcher",
            "meta-package-json",
            "platform-package-json",
            "native-executable",
        ):
            with self.subTest(
                role=role, condition="symlink"
            ), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                candidate, members = self.fake_npm_candidate(root)
                member = members[role]
                replacement = root / f"real-{role}"
                member.rename(replacement)
                member.symlink_to(replacement)
                with self.assertRaisesRegex(
                    RUNNER.SmokeError,
                    "non-symlink regular file|exact @openprose/prose-cli|"
                    "symbolic link|alias",
                ):
                    RUNNER.admit_candidate_closure(candidate, "npm")
            with self.subTest(
                role=role, condition="missing"
            ), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                candidate, members = self.fake_npm_candidate(root)
                members[role].unlink()
                with self.assertRaisesRegex(
                    RUNNER.SmokeError, "unavailable|not installed"
                ):
                    RUNNER.admit_candidate_closure(candidate, "npm")

    def test_candidate_closure_rejects_raw_and_ancestor_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            target = self.fake_candidate(root)
            linked = root / "linked-candidate"
            linked.symlink_to(target)
            with self.assertRaisesRegex(RUNNER.SmokeError, "symbolic link|alias"):
                RUNNER.admit_candidate_closure(linked, None)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            real_parent = root / "real-parent"
            real_parent.mkdir()
            target = self.fake_candidate(real_parent)
            alias_parent = root / "alias-parent"
            alias_parent.symlink_to(real_parent, target_is_directory=True)
            aliased = alias_parent / target.name
            with self.assertRaisesRegex(RUNNER.SmokeError, "symbolic link|alias"):
                RUNNER.admit_candidate_closure(aliased, None)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            target = self.fake_candidate(root)
            relative = Path(os.path.relpath(target, Path.cwd()))
            with self.assertRaisesRegex(RUNNER.SmokeError, "absolute"):
                RUNNER.admit_candidate_closure(relative, None)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            parent = root / "candidate-parent"
            parent.mkdir()
            target = self.fake_candidate(parent)
            _, _, admitted = RUNNER.admit_candidate_closure(target, None)
            moved = root / "moved-candidate-parent"
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(RUNNER.SmokeError, "symbolic link|alias"):
                RUNNER.require_candidate_closure_unchanged(admitted)

    def test_npm_closure_rejects_duplicate_or_aliased_platform_packages(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate, members = self.fake_npm_candidate(root)
            platform_root = members["platform-package-json"].parent
            nested = (
                candidate.parent.parent
                / "node_modules"
                / "@openprose"
                / platform_root.name
            )
            shutil.copytree(platform_root, nested)
            with self.assertRaisesRegex(RUNNER.SmokeError, "duplicate"):
                RUNNER.admit_candidate_closure(candidate, "npm")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate, members = self.fake_npm_candidate(root)
            platform_root = members["platform-package-json"].parent
            outside = root / "outside-platform-package"
            platform_root.rename(outside)
            platform_root.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RUNNER.SmokeError, "symbolic link|alias"):
                RUNNER.admit_candidate_closure(candidate, "npm")

    def test_node_probe_is_bounded_bound_and_reauthenticated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            node = root / "node"
            node.write_text(
                f"#!{sys.executable}\n"
                "import json\n"
                "print(json.dumps({'platform':'darwin','architecture':'arm64',"
                "'libc':None}, separators=(',', ':')))\n",
                "utf-8",
            )
            node.chmod(0o755)
            identity, admitted = RUNNER.admit_node_runtime({"PATH": str(root)}, root)
            self.assertEqual(identity["platformId"], "darwin-arm64")
            self.assertEqual(identity["platform"], "darwin")
            self.assertEqual(identity["architecture"], "arm64")
            self.assertIsNone(identity["libc"])
            node.write_text("mutated node executable\n", "utf-8")
            with self.assertRaisesRegex(
                RUNNER.SmokeError, "node-executable changed after initial admission"
            ):
                RUNNER.require_candidate_closure_unchanged((admitted,))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            with self.assertRaisesRegex(RUNNER.SmokeError, "Node executable"):
                RUNNER.admit_node_runtime({"PATH": str(root)}, root)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            node = root / "node"
            node.write_text(f"#!{sys.executable}\nprint('x' * 513)\n", "utf-8")
            node.chmod(0o755)
            with self.assertRaisesRegex(RUNNER.SmokeError, "bounded output limit"):
                RUNNER.admit_node_runtime({"PATH": str(root)}, root)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            bin_root = root / "bin"
            bin_root.mkdir()
            source = (
                f"#!{sys.executable}\n"
                "import json\n"
                "print(json.dumps({'platform':'darwin','architecture':'arm64',"
                "'libc':None}, separators=(',', ':')))\n"
            )
            first = root / "node-first"
            second = root / "node-second"
            for target in (first, second):
                target.write_text(source, "utf-8")
                target.chmod(0o755)
            command = bin_root / "node"
            command.symlink_to(first)
            _, admitted = RUNNER.admit_node_runtime({"PATH": str(bin_root)}, root)
            command.unlink()
            command.symlink_to(second)
            with self.assertRaisesRegex(RUNNER.SmokeError, "Node executable route"):
                RUNNER.require_candidate_closure_unchanged((admitted,))

    def test_npm_closure_rejects_node_platform_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate, _ = self.fake_npm_candidate(root)
            node, admitted = RUNNER.admit_node_runtime(
                RUNNER.sanitized_base_environment(), root
            )
            mismatched = dict(node)
            mismatched["architecture"] = (
                "x64" if node["architecture"] == "arm64" else "arm64"
            )
            mismatched["platformId"] = RUNNER.node_platform_id(
                mismatched["platform"],
                mismatched["architecture"],
                mismatched["libc"],
            )
            with mock.patch.object(
                RUNNER,
                "admit_node_runtime",
                return_value=(mismatched, admitted),
            ), self.assertRaisesRegex(
                RUNNER.SmokeError, "does not bind the selected platform package"
            ):
                RUNNER.admit_candidate_closure(candidate, "npm")

    def test_npm_closure_is_reauthenticated_at_final_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate, members = self.fake_npm_candidate(root)
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--surface",
                    "npm",
                    "--program",
                    str(program),
                ]
            )
            validate = RUNNER.validate_harness_version

            def mutate_at_validation(harness: str, observed: str):
                members["launcher"].write_text("mutated at final settlement\n", "utf-8")
                return validate(harness, observed)

            with mock.patch.object(
                RUNNER, "validate_harness_version", side_effect=mutate_at_validation
            ), self.assertRaisesRegex(
                RUNNER.SmokeError,
                "candidate closure launcher changed after initial admission",
            ):
                RUNNER.run_smoke(options)

    def test_cost_acknowledgement_is_required_before_candidate_validation(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(HERE / "run.py"),
                "--candidate",
                "/does/not/exist",
                "--harness",
                "claude",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--acknowledge-provider-cost", completed.stderr)
        self.assertNotIn("unavailable", completed.stderr)

    def test_env_parser_selects_credentials_without_returning_values(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / ".env"
            path.write_text(
                "OPENROUTER_API_KEY=secret-alpha\nUNRELATED_SECRET=secret-beta\n",
                "utf-8",
            )
            selected = RUNNER.parse_env_file(path)
        self.assertEqual(set(selected), {"OPENROUTER_API_KEY"})
        self.assertNotIn("secret-beta", repr(selected))

    def test_base_environment_is_a_closed_cached_login_allowlist(self) -> None:
        ambient = {
            "PATH": "/fixture/bin",
            "HOME": "/fixture/cached-login-home",
            "USER": "fixture-user",
            "LOGNAME": "fixture-logname",
            "SHELL": "/bin/zsh",
            "TMPDIR": "/fixture/tmp",
            "LANG": "en_US.UTF-8",
            "LC_CTYPE": "UTF-8",
            "TZ": "UTC",
            "DATABASE_URL": "postgres://ambient-database-secret",
            "ARBITRARY_TOKEN": "ambient-token-secret",
            "OPENAI_API_KEY": "ambient-provider-secret",
            "ANTHROPIC_OAUTH_TOKEN": "ambient-provider-secret",
            "HTTP_PROXY": "http://proxy-user:proxy-password@127.0.0.1:9",
            "HTTPS_PROXY": "http://proxy-user:proxy-password@127.0.0.1:9",
            "ALL_PROXY": "socks5://proxy-user:proxy-password@127.0.0.1:9",
            "NO_PROXY": "credential-service.internal",
            "SSH_AUTH_SOCK": "/fixture/ssh-agent.sock",
            "XDG_CONFIG_HOME": "/fixture/ambient-xdg-config",
            "XDG_CACHE_HOME": "/fixture/ambient-xdg-cache",
            "XDG_DATA_HOME": "/fixture/ambient-xdg-data",
            "NODE_OPTIONS": "--require=/fixture/ambient-hook.js",
            "RUSTFLAGS": "--cfg ambient_build_override",
            "OPENPROSE_CONFORMANCE_ADAPTER_MODE": "forged",
            "PROSE_AUTH_PROFILE": "ambient-route-must-not-win",
            "LIVE_ALPHA_TEST_SENTINEL": "test-only-secret",
        }
        selected = RUNNER.sanitized_base_environment(ambient)
        self.assertEqual(
            selected,
            {
                "PATH": "/fixture/bin",
                "HOME": "/fixture/cached-login-home",
                "USER": "fixture-user",
                "LOGNAME": "fixture-logname",
                "SHELL": "/bin/zsh",
                "TMPDIR": "/fixture/tmp",
                "LANG": "en_US.UTF-8",
                "LC_CTYPE": "UTF-8",
                "TZ": "UTC",
            },
        )

    def test_harness_runtime_probe_receives_the_same_closed_base(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            observation = root / "observed-environment.json"
            runtime = root / "bun"
            runtime.write_text(
                f"#!{sys.executable}\n"
                "import json, os\n"
                "from pathlib import Path\n"
                f"Path({str(observation)!r}).write_text("
                "json.dumps(dict(os.environ), sort_keys=True), encoding='utf-8')\n"
                "print('1.3.14')\n",
                "utf-8",
            )
            runtime.chmod(0o755)
            environment = RUNNER.sanitized_base_environment(
                {
                    "PATH": str(root),
                    "HOME": "/fixture/cached-login-home",
                    "USER": "fixture-user",
                    "LANG": "C.UTF-8",
                    "DATABASE_URL": "postgres://ambient-database-secret",
                    "ARBITRARY_TOKEN": "ambient-token-secret",
                    "OPENAI_API_KEY": "ambient-provider-secret",
                    "HTTP_PROXY": "http://proxy-user:proxy-password@127.0.0.1:9",
                    "SSH_AUTH_SOCK": "/fixture/ssh-agent.sock",
                    "XDG_CONFIG_HOME": "/fixture/ambient-xdg-config",
                    "NODE_OPTIONS": "--require=/fixture/ambient-hook.js",
                    "OPENPROSE_TEST_SENTINEL": "test-only-secret",
                }
            )
            observed_version = RUNNER._runtime_version("bun", runtime, environment)
            observed = json.loads(observation.read_text("utf-8"))

        self.assertEqual(observed_version, "1.3.14")
        # macOS may synthesize its own locale encoding marker when Python
        # starts; that exact non-secret OS name is itself in the allowlist.
        observed.pop("__CF_USER_TEXT_ENCODING", None)
        self.assertEqual(observed, environment)
        self.assertEqual(
            observed,
            {
                "PATH": str(root),
                "HOME": "/fixture/cached-login-home",
                "USER": "fixture-user",
                "LANG": "C.UTF-8",
            },
        )

    def test_candidate_gets_only_closed_base_owned_controls_and_env_file_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            admitted_path = os.environ["PATH"]
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            env_file = root / ".env"
            env_file.write_text(
                "OPENAI_API_KEY=explicit-test-credential\n"
                "DATABASE_URL=ignored-env-file-database\n"
                "ARBITRARY_TOKEN=ignored-env-file-token\n",
                "utf-8",
            )
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                    "--env-file",
                    str(env_file),
                ]
            )
            hostile = {
                "PATH": admitted_path,
                "HOME": "/fixture/cached-login-home",
                "USER": "fixture-user",
                "LANG": "C.UTF-8",
                "DATABASE_URL": "postgres://ambient-database-secret",
                "ARBITRARY_TOKEN": "ambient-token-secret",
                "OPENAI_API_KEY": "ambient-provider-secret",
                "ANTHROPIC_API_KEY": "ambient-provider-secret",
                "HTTP_PROXY": "http://proxy-user:proxy-password@127.0.0.1:9",
                "HTTPS_PROXY": "http://proxy-user:proxy-password@127.0.0.1:9",
                "ALL_PROXY": "socks5://proxy-user:proxy-password@127.0.0.1:9",
                "NO_PROXY": "credential-service.internal",
                "SSH_AUTH_SOCK": "/fixture/ssh-agent.sock",
                "XDG_CONFIG_HOME": "/fixture/ambient-xdg-config",
                "XDG_CACHE_HOME": "/fixture/ambient-xdg-cache",
                "XDG_DATA_HOME": "/fixture/ambient-xdg-data",
                "NODE_OPTIONS": "--require=/fixture/ambient-hook.js",
                "OPENPROSE_CONFORMANCE_ADAPTER_MODE": "forged",
                "PROSE_AUTH_PROFILE": "ambient-route-must-not-win",
                "LIVE_ALPHA_TEST_SENTINEL": "test-only-secret",
            }
            observed_environments: list[dict[str, str]] = []
            observed_arguments: list[list[str]] = []
            command_json = RUNNER.command_json

            def capture_environment(*args, **kwargs):
                observed_environments.append(dict(kwargs["environment"]))
                observed_arguments.append(list(args[1]))
                return command_json(*args, **kwargs)

            with mock.patch.dict(os.environ, hostile, clear=True), mock.patch.object(
                RUNNER, "command_json", side_effect=capture_environment
            ):
                evidence = RUNNER.run_smoke(options)

        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(len(observed_environments), 3)
        self.assertEqual(
            observed_arguments,
            [
                [
                    "cli",
                    "harness",
                    "use",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                ],
                ["cli", "doctor"],
                ["run", "hello.prose.md"],
            ],
        )
        rendered_evidence = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("explicit-test-credential", rendered_evidence)
        self.assertNotIn("ambient-database-secret", rendered_evidence)
        self.assertNotIn("ambient-token-secret", rendered_evidence)
        self.assertNotIn("proxy-password", rendered_evidence)
        self.assertNotIn("/fixture/cached-login-home", rendered_evidence)
        self.assertNotIn("/fixture/ambient-xdg-config", rendered_evidence)
        self.assertNotIn("/fixture/ssh-agent.sock", rendered_evidence)
        expected_names = {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "XDG_CONFIG_HOME",
            "NO_COLOR",
            "TERM",
            "CI",
            "OPENAI_API_KEY",
        }
        forbidden_values = {
            value
            for name, value in hostile.items()
            if name not in {"PATH", "HOME", "USER", "LANG"}
        }
        for environment in observed_environments:
            self.assertEqual(set(environment), expected_names)
            self.assertEqual(environment["OPENAI_API_KEY"], "explicit-test-credential")
            self.assertNotIn("PROSE_MODEL", environment)
            self.assertNotIn("PROSE_AUTH_PROFILE", environment)
            self.assertEqual(environment["HOME"], "/fixture/cached-login-home")
            self.assertNotIn("/fixture/ambient-xdg-config", environment.values())
            self.assertTrue(forbidden_values.isdisjoint(environment.values()))

    def test_live_invocation_requires_exact_model_and_harness_profile(self) -> None:
        for argv, message in (
            (["--candidate", "/unused", "--harness", "codex"], "--model"),
            (
                [
                    "--candidate",
                    "/unused",
                    "--harness",
                    "codex",
                    "--model",
                    "fixture/model",
                    "--auth-profile",
                    "prime-harness-login",
                ],
                "--auth-profile",
            ),
        ):
            with self.subTest(argv=argv), self.assertRaisesRegex(
                RUNNER.SmokeError, message
            ):
                RUNNER.invocation_identity(RUNNER.parser().parse_args(argv))

    def test_harness_custody_is_pathless_and_rejects_package_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.fake_candidate(root)
            environment = RUNNER.sanitized_base_environment()
            node, admitted_node = RUNNER.admit_node_runtime(environment, root)
            custody = RUNNER.admit_harness_custody(
                "codex", environment, node, admitted_node
            )
            rendered = json.dumps(custody.evidence, sort_keys=True)
            self.assertNotIn(str(root), rendered)
            self.assertEqual("npm-package", custody.evidence["distributionKind"])
            package_file = next(
                item
                for item in custody.files
                if "package file" in item.label and item.path.name == "codex"
            )
            package_file.path.write_bytes(b"mutated same-version native\n")
            with self.assertRaisesRegex(
                RUNNER.SmokeError, "package file changed after initial admission"
            ):
                RUNNER.require_harness_custody_unchanged(custody, environment)

    def test_harness_custody_rejects_route_retarget_and_new_optional_dependency(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.fake_candidate(root)
            package = (
                root / "fake-codex-install/node_modules/@openai/codex/package.json"
            )
            manifest = json.loads(package.read_text("utf-8"))
            manifest["optionalDependencies"]["fixture-optional"] = "1.0.0"
            package.write_text(json.dumps(manifest), "utf-8")
            environment = RUNNER.sanitized_base_environment()
            node, admitted_node = RUNNER.admit_node_runtime(environment, root)
            custody = RUNNER.admit_harness_custody(
                "codex", environment, node, admitted_node
            )
            missing = root / "fake-codex-install/node_modules/fixture-optional"
            missing.mkdir()
            missing.joinpath("package.json").write_text(
                json.dumps({"name": "fixture-optional", "version": "1.0.0"}),
                "utf-8",
            )
            with self.assertRaisesRegex(RUNNER.SmokeError, "previously absent"):
                RUNNER.require_harness_custody_unchanged(custody, environment)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            self.fake_candidate(root)
            environment = RUNNER.sanitized_base_environment()
            node, admitted_node = RUNNER.admit_node_runtime(environment, root)
            custody = RUNNER.admit_harness_custody(
                "codex", environment, node, admitted_node
            )
            route = custody.routes[0]
            replacement = root / "replacement-codex"
            replacement.write_bytes(route.target.read_bytes())
            replacement.chmod(0o755)
            route.command_path.unlink()
            route.command_path.symlink_to(replacement)
            with self.assertRaisesRegex(RUNNER.SmokeError, "symlink route changed"):
                RUNNER.require_harness_custody_unchanged(custody, environment)

    def test_native_codex_distribution_is_admitted_without_package_overclaim(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            native = root / "codex"
            native.write_bytes(b"native-codex-fixture\n")
            native.chmod(0o755)
            environment = RUNNER.sanitized_base_environment()
            environment["PATH"] = f"{root}{os.pathsep}{environment['PATH']}"
            node, admitted_node = RUNNER.admit_node_runtime(environment, root)
            custody = RUNNER.admit_harness_custody(
                "codex", environment, node, admitted_node
            )
        self.assertEqual("native", custody.evidence["distributionKind"])
        self.assertIsNone(custody.evidence["runtime"])
        self.assertEqual([], custody.evidence["packages"])

    def test_ambient_credentials_are_removed_and_explicit_file_wins(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            env_file = root / ".env"
            env_file.write_text("OPENAI_API_KEY=explicit-test-credential\n", "utf-8")
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                    "--env-file",
                    str(env_file),
                ]
            )
            with mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "ambient-credential-must-not-win",
                },
            ):
                evidence = RUNNER.run_smoke(options)
        self.assertEqual("pass", evidence["status"])
        self.assertEqual("bun", evidence["surface"])

    def test_candidate_output_is_stopped_at_the_closed_limit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = root / "noisy"
            candidate.write_text(
                "#!/usr/bin/env python3\nimport sys\n"
                f"sys.stdout.buffer.write(b'x' * {RUNNER.MAX_OUTPUT_BYTES + 65_536})\n",
                "utf-8",
            )
            candidate.chmod(0o755)
            with self.assertRaisesRegex(RUNNER.SmokeError, "bounded output limit"):
                RUNNER.run_bounded(
                    [str(candidate)],
                    cwd=root,
                    environment={"PATH": os.defpath},
                    timeout_seconds=10,
                )

    def test_candidate_nonzero_output_does_not_escape_into_diagnostics(self) -> None:
        token = "credential-canary-nonzero"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = root / "nonzero"
            candidate.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"print(json.dumps({{'error':{{'code':'{token}'}}}}))\n"
                "sys.exit(9)\n",
                "utf-8",
            )
            candidate.chmod(0o755)
            with self.assertRaises(RUNNER.SmokeError) as captured:
                RUNNER.command_json(
                    candidate,
                    [],
                    cwd=root,
                    environment={"PATH": os.defpath},
                    timeout_seconds=5,
                    label="probe",
                )
        self.assertEqual("probe failed with a nonzero exit", str(captured.exception))
        self.assertNotIn(token, str(captured.exception))

    def test_prime_parser_failure_renders_only_the_fixed_safe_projection(self) -> None:
        candidate_canary = "candidate-secret-canary-must-not-render"
        value = {
            "schema": "openprose.runner-result/1",
            "adapter": {"id": "prime/rpc", "candidate": candidate_canary},
            "runnerExitCode": 22,
            "error": {
                "schema": "openprose.runner-error/1",
                "code": "PROTOCOL_TRUNCATED",
                "boundary": "protocol",
                "message": candidate_canary,
                "action": candidate_canary,
                "exitCode": 22,
                "retryable": False,
                "details": {
                    "reason": candidate_canary,
                    "adapterDiagnostic": {
                        "schema": "openprose.adapter-diagnostic/1",
                        "adapterId": "prime/rpc",
                        "stage": "prime-lifecycle",
                        "phase": "await-thinking-or-text-start",
                        "counters": {
                            "acceptedRecords": 6,
                            "thinkingDeltas": 0,
                            "textDeltas": 0,
                            "saturated": False,
                        },
                    },
                },
            },
        }
        stdout = json.dumps(value, separators=(",", ":")).encode("utf-8")
        with mock.patch.object(
            RUNNER, "run_bounded", return_value=(22, stdout, b"", 17)
        ), self.assertRaises(RUNNER.SmokeError) as captured:
            RUNNER.command_json(
                Path("/candidate-not-launched"),
                [],
                cwd=HERE,
                environment={},
                timeout_seconds=5,
                label="functional-alpha run",
                render_prime_diagnostic=True,
            )
        self.assertEqual(
            "functional-alpha run failed with candidate-reported Prime parser "
            "diagnostic: code=PROTOCOL_TRUNCATED stage=prime-lifecycle "
            "phase=await-thinking-or-text-start acceptedRecords=6 thinkingDeltas=0 "
            "textDeltas=0 saturated=false",
            str(captured.exception),
        )
        self.assertNotIn(candidate_canary, str(captured.exception))

    def test_prime_parser_failure_whitelist_rejects_diagnostic_mutations(self) -> None:
        base = {
            "schema": "openprose.runner-result/1",
            "adapter": {"id": "prime/rpc"},
            "runnerExitCode": 22,
            "error": {
                "schema": "openprose.runner-error/1",
                "code": "PROTOCOL_MALFORMED",
                "boundary": "protocol",
                "exitCode": 22,
                "details": {
                    "adapterDiagnostic": {
                        "schema": "openprose.adapter-diagnostic/1",
                        "adapterId": "prime/rpc",
                        "stage": "jsonl-framing",
                        "phase": "record-boundary",
                        "counters": {
                            "acceptedRecords": 2,
                            "thinkingDeltas": 0,
                            "textDeltas": 0,
                            "saturated": False,
                        },
                    }
                },
            },
        }
        mutations = []
        for mutate in (
            lambda value: value["adapter"].update({"id": "codex/exec-json"}),
            lambda value: value["error"]["details"]["adapterDiagnostic"].update(
                {"candidate": "secret"}
            ),
            lambda value: value["error"]["details"]["adapterDiagnostic"].update(
                {"phase": "await-text-start"}
            ),
            lambda value: value["error"]["details"]["adapterDiagnostic"][
                "counters"
            ].update({"acceptedRecords": True}),
        ):
            candidate = json.loads(json.dumps(base))
            mutate(candidate)
            mutations.append(candidate)
        stale_phase = json.loads(json.dumps(base))
        stale_phase["error"]["details"]["adapterDiagnostic"].update(
            {"stage": "prime-lifecycle", "phase": "await-thinking-start"}
        )
        mutations.append(stale_phase)
        for value in mutations:
            with self.subTest(value=value), mock.patch.object(
                RUNNER,
                "run_bounded",
                return_value=(
                    22,
                    json.dumps(value, separators=(",", ":")).encode("utf-8"),
                    b"",
                    1,
                ),
            ), self.assertRaises(RUNNER.SmokeError) as captured:
                RUNNER.command_json(
                    Path("/candidate-not-launched"),
                    [],
                    cwd=HERE,
                    environment={},
                    timeout_seconds=5,
                    label="functional-alpha run",
                    render_prime_diagnostic=True,
                )
            self.assertEqual(
                "functional-alpha run failed with a nonzero exit",
                str(captured.exception),
            )

    def test_failed_prime_diagnostic_never_creates_an_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "failed-evidence.json"
            with mock.patch.object(
                RUNNER,
                "run_smoke",
                side_effect=RUNNER.SmokeError(
                    "functional-alpha run failed with candidate-reported Prime parser "
                    "diagnostic: code=PROTOCOL_TRUNCATED stage=prime-lifecycle "
                    "phase=await-agent-end acceptedRecords=18 thinkingDeltas=3 "
                    "textDeltas=3 saturated=false"
                ),
            ), mock.patch("sys.stderr", new_callable=StringIO) as stderr:
                exit_code = RUNNER.main(
                    [
                        "--candidate",
                        "/candidate-not-launched",
                        "--harness",
                        "prime",
                        "--model",
                        "fixture-model",
                        "--auth-profile",
                        "prime-harness-login",
                        "--acknowledge-provider-cost",
                        "--out",
                        str(output),
                    ]
                )
            self.assertEqual(2, exit_code)
            self.assertFalse(output.exists())
            self.assertIn(
                "candidate-reported Prime parser diagnostic", stderr.getvalue()
            )

    def test_candidate_malformed_output_does_not_escape_into_diagnostics(self) -> None:
        token = "credential-canary-malformed"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = root / "malformed"
            candidate.write_text(
                f"#!/usr/bin/env python3\nprint('not-json-{token}')\n",
                "utf-8",
            )
            candidate.chmod(0o755)
            with self.assertRaises(RUNNER.SmokeError) as captured:
                RUNNER.command_json(
                    candidate,
                    [],
                    cwd=root,
                    environment={"PATH": os.defpath},
                    timeout_seconds=5,
                    label="probe",
                )
        self.assertEqual("probe did not emit one JSON object", str(captured.exception))
        self.assertNotIn(token, str(captured.exception))

    def test_candidate_stderr_does_not_escape_into_diagnostics(self) -> None:
        token = "credential-canary-stderr"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = root / "stderr"
            candidate.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"print('{token}', file=sys.stderr)\n"
                "print(json.dumps({'status':'unused'}))\n",
                "utf-8",
            )
            candidate.chmod(0o755)
            with self.assertRaises(RUNNER.SmokeError) as captured:
                RUNNER.command_json(
                    candidate,
                    [],
                    cwd=root,
                    environment={"PATH": os.defpath},
                    timeout_seconds=5,
                    label="probe",
                )
        self.assertEqual("probe wrote diagnostics", str(captured.exception))
        self.assertNotIn(token, str(captured.exception))

    def test_cli_printing_does_not_reemit_candidate_stderr(self) -> None:
        token = "credential-canary-cli-stderr"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            candidate.write_text(
                candidate.read_text("utf-8").replace(
                    "import hashlib, json, os, pathlib, sys",
                    "import hashlib, json, os, pathlib, sys\n"
                    f"print('{token}', file=sys.stderr)",
                ),
                "utf-8",
            )
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "run.py"),
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                    "--acknowledge-provider-cost",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(2, completed.returncode)
        self.assertIn("harness selection wrote diagnostics", completed.stderr)
        self.assertNotIn(token, completed.stderr)
        self.assertNotIn(token, completed.stdout)

    def test_candidate_timeout_output_does_not_escape_into_diagnostics(self) -> None:
        token = "credential-canary-timeout"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = root / "timeout"
            candidate.write_text(
                "#!/usr/bin/env python3\n"
                "import sys, time\n"
                f"print('{token}', flush=True)\n"
                f"print('{token}', file=sys.stderr, flush=True)\n"
                "time.sleep(10)\n",
                "utf-8",
            )
            candidate.chmod(0o755)
            with self.assertRaises(RUNNER.SmokeError) as captured:
                RUNNER.run_bounded(
                    [str(candidate)],
                    cwd=root,
                    environment={"PATH": os.defpath},
                    timeout_seconds=1,
                )
        self.assertIn("timed out", str(captured.exception))
        self.assertNotIn(token, str(captured.exception))

    def test_candidate_mismatch_value_does_not_escape_into_diagnostics(self) -> None:
        token = "credential-canary-mismatch"
        with self.assertRaises(RUNNER.SmokeError) as captured:
            RUNNER.require_equal(token, "expected", "candidate field")
        self.assertEqual("candidate field differs", str(captured.exception))
        self.assertNotIn(token, str(captured.exception))

    def test_live_evidence_rejects_declared_surface_runner_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(self.fake_candidate(root)),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--surface",
                    "rust",
                    "--program",
                    str(root / "hello.prose.md"),
                ]
            )
            options.program.write_text("placeholder\n", "utf-8")
            with self.assertRaisesRegex(RUNNER.SmokeError, "surface.*runner"):
                RUNNER.run_smoke(options)

    def test_live_evidence_rejects_version_outside_frozen_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            candidate.write_text(
                candidate.read_text("utf-8").replace(
                    "codex-cli 0.149.0-alpha.4.1", "codex-cli 0.150.0"
                ),
                "utf-8",
            )
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                ]
            )
            with self.assertRaisesRegex(RUNNER.SmokeError, "outside frozen support"):
                RUNNER.run_smoke(options)

    def test_live_evidence_rejects_selection_outside_owned_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            candidate.write_text(
                candidate.read_text("utf-8").replace(
                    'config = config_root / "openprose" / "cli.toml"',
                    'config = pathlib.Path.cwd() / "escaped-cli.toml"',
                ),
                "utf-8",
            )
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                ]
            )
            with self.assertRaisesRegex(RUNNER.SmokeError, "escaped the owned"):
                RUNNER.run_smoke(options)

    def test_live_evidence_reauthenticates_program_bytes_after_each_phase(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            candidate = self.fake_candidate(root)
            candidate.write_text(
                candidate.read_text("utf-8").replace(
                    "program = args[-1]",
                    "program = args[-1]\n"
                    "        pathlib.Path(program).write_text("
                    '"mutated\\n", encoding="utf-8")',
                ),
                "utf-8",
            )
            program = root / "hello.prose.md"
            program.write_text("placeholder\n", "utf-8")
            options = RUNNER.parser().parse_args(
                [
                    "--candidate",
                    str(candidate),
                    "--harness",
                    "codex",
                    "--model",
                    "fixture-model",
                    "--auth-profile",
                    "cached-chatgpt-login",
                    "--program",
                    str(program),
                ]
            )
            with self.assertRaisesRegex(
                RUNNER.SmokeError, "program changed after initial admission"
            ):
                RUNNER.run_smoke(options)

    def test_frozen_version_boundaries_cover_all_live_adapters(self) -> None:
        accepted = {
            "prime": ["0.7.0", "prime-agent 0.8.1"],
            "omp": ["omp/18.0.9"],
            "codex": ["codex-cli 0.149.0-alpha.4.1"],
            "claude": ["2.1.243", "2.1.243 (Claude Code)"],
        }
        rejected = {
            "prime": [
                "prime-agent 0.6.99",
                "0.8.0",
                "0.8.2",
                "0.9.0",
                "prime 0.7.0",
            ],
            "omp": ["omp/18.0.8", "18.0.9", "omp/18.0.10"],
            "codex": [
                "codex-cli 0.149.0-alpha.4",
                "codex-cli 0.149.0-alpha.4.2",
                "codex-cli 0.150.0",
                "0.149.0-alpha.4.1",
            ],
            "claude": [
                "2.1.242",
                "2.1.244",
                "2.2.0-alpha.1",
                "2.2.0",
                "claude 2.1.243",
            ],
        }
        for harness, versions in accepted.items():
            for version in versions:
                with self.subTest(harness=harness, version=version, accepted=True):
                    admission = RUNNER.validate_harness_version(harness, version)
                    self.assertTrue(admission.admitted_versions)
                    self.assertTrue(admission.repair_command)
                    self.assertRegex(admission.recipe_digest, r"^[0-9a-f]{64}$")
        for harness, versions in rejected.items():
            for version in versions:
                with self.subTest(harness=harness, version=version, accepted=False):
                    with self.assertRaisesRegex(
                        RUNNER.SmokeError, "outside frozen support"
                    ):
                        RUNNER.validate_harness_version(harness, version)

    def test_exact_admission_ignores_range_and_rejects_malformed_recipe_lists(
        self,
    ) -> None:
        original, digest = RUNNER.adapter_recipe("codex")
        descriptive = json.loads(json.dumps(original))
        descriptive["support"]["versionRange"] = ">=0.0.0"
        with mock.patch.object(
            RUNNER, "adapter_recipe", return_value=(descriptive, digest)
        ):
            admission = RUNNER.validate_harness_version(
                "codex", "codex-cli 0.149.0-alpha.4.1"
            )
            self.assertEqual(("0.149.0-alpha.4.1",), admission.admitted_versions)
            with self.assertRaisesRegex(RUNNER.SmokeError, "outside frozen support"):
                RUNNER.validate_harness_version("codex", "codex-cli 0.149.0-alpha.4.2")

        for admitted, repair in (
            ([], original["support"]["repairCommand"]),
            (
                ["0.149.0-alpha.4.1", "0.149.0-alpha.4.1"],
                original["support"]["repairCommand"],
            ),
            (["not-semver"], original["support"]["repairCommand"]),
            (["0.149.0-alpha.4.1"], ""),
            (["0.149.0-alpha.4.1"], "repair\ncommand"),
        ):
            with self.subTest(admitted=admitted, repair=repair):
                malformed = json.loads(json.dumps(original))
                malformed["support"]["admittedVersions"] = admitted
                malformed["support"]["repairCommand"] = repair
                with mock.patch.object(
                    RUNNER, "adapter_recipe", return_value=(malformed, digest)
                ), self.assertRaisesRegex(
                    RUNNER.SmokeError, "lacks exact version admission"
                ):
                    RUNNER.validate_harness_version(
                        "codex", "codex-cli 0.149.0-alpha.4.1"
                    )


if __name__ == "__main__":
    unittest.main()
