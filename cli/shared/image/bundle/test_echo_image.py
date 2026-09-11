#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
CLI = HERE.parents[2]
IMAGE = CLI / "shared" / "image" / "echo-v0"
EMBEDDED = CLI / "shared" / "image" / "embedded"
ALPHA_ORACLE = CLI / "shared" / "capabilities" / "adapters" / "functional-alpha.v1.json"
ALPHA_CARRIAGE = CLI / "shared" / "fixtures" / "adapters" / "functional-alpha" / "terminal-carriage.v1.json"
RECIPES = CLI / "shared" / "capabilities" / "adapters" / "recipes"
MODULE_PATH = HERE / "image_bundle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("openprose_image_bundle_echo", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_json(path: Path):
    return json.loads(path.read_text("utf-8"))


class EchoImageContractTest(unittest.TestCase):
    def test_echo_image_is_release_eligible_but_explicitly_nonsemantic(self) -> None:
        bundle = load_module()
        parsed = bundle.load_image_directory(IMAGE, require_release_eligible=True)
        manifest = parsed.manifest
        self.assertEqual("echo-v0", manifest["imageVersion"])
        self.assertEqual("functional-alpha-placeholder", manifest["purpose"])
        self.assertIs(manifest["releaseEligible"], True)
        self.assertEqual("placeholder-not-openprose-language", manifest["languageVersion"])
        self.assertEqual("echo-placeholder-v0", manifest["skillVersion"])
        self.assertIn("placeholder", manifest["semanticSourceRevision"])

        terminal = load_json(IMAGE / manifest["terminalEnvelope"]["path"])
        self.assertEqual(
            ["schema", "semanticStatus", "placeholder", "marker", "task"],
            terminal["required"],
        )
        self.assertIs(terminal["additionalProperties"], False)
        self.assertEqual(
            "openprose.echo-terminal/1",
            terminal["properties"]["schema"]["const"],
        )
        self.assertEqual(
            "not-applicable",
            terminal["properties"]["semanticStatus"]["const"],
        )
        self.assertIs(terminal["properties"]["placeholder"]["const"], True)
        self.assertEqual(
            "OPENPROSE_ECHO_TERMINAL_V0",
            terminal["properties"]["marker"]["const"],
        )
        task = terminal["properties"]["task"]
        self.assertIs(task["additionalProperties"], False)
        self.assertEqual(["argv"], task["required"])

        task_envelope = load_json(IMAGE / manifest["taskEnvelope"]["path"])
        self.assertEqual(
            "openprose.task-envelope/1",
            task_envelope["properties"]["schema"]["const"],
        )
        self.assertNotIn("taskSha256", task_envelope["required"])

        visible = b"".join(
            (IMAGE / item["path"]).read_bytes() for item in manifest["payload"]
        ).decode("utf-8")
        self.assertIn("does not implement, parse, validate, or execute OpenProse", visible)
        self.assertIn("Do not use tools, run commands, modify files, or delegate", visible)
        self.assertIn(
            '{"schema":"openprose.echo-terminal/1","semanticStatus":"not-applicable","placeholder":true,"marker":"OPENPROSE_ECHO_TERMINAL_V0","task":{"argv":["prose","run","hello.prose.md"]}}',
            visible,
        )

    def test_embedded_current_is_exact_echo_bundle(self) -> None:
        bundle = load_module()
        expected = bundle.build_bundle(IMAGE, require_release_eligible=True)
        observed = (EMBEDDED / "current.bundle.bin").read_bytes()
        self.assertEqual(expected, observed)
        bundle.check_checksum(EMBEDDED / "current.bundle.sha256", observed)

    def test_functional_alpha_contract_keeps_four_adapters_thin_and_honest(self) -> None:
        oracle = load_json(ALPHA_ORACLE)
        self.assertEqual("openprose.functional-alpha-adapter-oracle/1", oracle["schema"])
        self.assertEqual("echo-v0", oracle["imageVersion"])
        self.assertEqual("not-applicable", oracle["semanticStatus"])
        self.assertIs(oracle["claims"]["openProseExecuted"], False)
        self.assertIs(oracle["claims"]["strictWrapperConformant"], False)
        self.assertEqual("forbidden", oracle["fallback"])
        self.assertEqual(
            {"prime/rpc", "omp/rpc", "codex/exec-json", "claude/print-stream-json"},
            {adapter["adapterId"] for adapter in oracle["adapters"]},
        )
        expected_terminal = {
            "schema": "openprose.echo-terminal/1",
            "semanticStatus": "not-applicable",
            "placeholder": True,
            "marker": "OPENPROSE_ECHO_TERMINAL_V0",
            "task": {
                "argv": "copy-exactly-from-task-envelope",
            },
        }
        self.assertEqual(expected_terminal, oracle["terminalEnvelopeTemplate"])
        self.assertIn("independently computed digest", oracle["taskEvidence"]["digestAuthority"])
        for adapter in oracle["adapters"]:
            with self.subTest(adapter=adapter["adapterId"]):
                self.assertIs(adapter["shell"], False)
                self.assertIs(adapter["outerPty"], False)
                self.assertEqual("explicit", adapter["selection"])
                self.assertEqual("user-provider", adapter["billingOwner"])
                self.assertEqual("harness-managed", adapter["authCategory"])
                self.assertEqual("assistant-final-text", adapter["terminalCarriage"])
                self.assertTrue(adapter["versionRange"])

    def test_terminal_carriage_fixture_binds_task_and_all_harness_streams(self) -> None:
        import hashlib

        fixture = load_json(ALPHA_CARRIAGE)
        original = fixture["task"]["original"]
        canonical = json.dumps(
            original, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        expected_digest = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(expected_digest, fixture["task"]["sha256"])
        delivered = fixture["task"]["delivered"]
        self.assertEqual(original["argv"], delivered["argv"])
        self.assertNotIn("taskSha256", delivered)

        terminal = fixture["terminal"]
        self.assertEqual(original["argv"], terminal["task"]["argv"])
        exact_line = json.dumps(
            terminal, ensure_ascii=False, sort_keys=False, separators=(",", ":")
        )
        expected_adapters = {
            "prime/rpc",
            "omp/rpc",
            "codex/exec-json",
            "claude/print-stream-json",
        }
        self.assertEqual(expected_adapters, set(fixture["streams"]))
        for adapter_id, stream in fixture["streams"].items():
            with self.subTest(adapter=adapter_id):
                self.assertEqual(exact_line, stream["assistantText"].splitlines()[-1])
                self.assertEqual(terminal, json.loads(stream["assistantText"].splitlines()[-1]))
                self.assertEqual(0, stream["expectedExitCode"])
                self.assertTrue(stream["harnessTerminalObserved"])

    def test_every_functional_alpha_recipe_has_optional_exact_model_selection(self) -> None:
        recipes = {
            recipe["adapterId"]: recipe
            for recipe in (
                load_json(path) for path in sorted(RECIPES.glob("*.v1.json"))
            )
        }
        oracle = load_json(ALPHA_ORACLE)
        for adapter in oracle["adapters"]:
            adapter_id = adapter["adapterId"]
            with self.subTest(adapter=adapter_id):
                recipe = recipes[adapter_id]
                self.assertEqual(adapter["versionRange"], recipe["support"]["versionRange"])
                expected_placement = {
                    "claude/print-stream-json": "before-final-argument",
                    "codex/exec-json": "before-final-argument",
                    "omp/rpc": "before-final-pair",
                    "prime/rpc": "append",
                }[adapter_id]
                self.assertEqual(
                    [
                        {
                            "when": "model-present",
                            "placement": expected_placement,
                            "argv": [
                                {"literal": "--model"},
                                {"value": "model"},
                            ],
                        }
                    ],
                    recipe["launch"]["optionalArgv"],
                )

        prime = recipes["prime/rpc"]["launch"]
        self.assertEqual("system-append", prime["instructionPlacement"]["manifestPlacementId"])
        self.assertEqual("strict", prime["instructionPlacement"]["strictness"])
        self.assertEqual("argv", prime["imageDelivery"]["mechanism"])
        self.assertEqual("rpc-field", prime["taskDelivery"]["mechanism"])
        self.assertIn({"value": "image-utf8"}, prime["argv"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
