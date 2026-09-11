#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import jsonschema

from capability_bridge import LocalCapabilityBridge
from test_support import capability_request


HERE = Path(__file__).resolve().parent
BRIDGE_EVIDENCE_SCHEMA = json.loads(
    (HERE / "capability-bridge-evidence.v1.schema.json").read_text("utf-8")
)


class CapabilityBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "input.txt").write_bytes(b"fixture input\n")
        self.executor_calls: list[tuple[list[str], Path, dict[str, str], int, int]] = []

        def executor(
            argv: list[str],
            cwd: Path,
            env: dict[str, str],
            timeout_ms: int,
            max_output_bytes: int,
        ) -> tuple[int, bytes, bytes]:
            self.executor_calls.append((argv, cwd, env, timeout_ms, max_output_bytes))
            return 0, ("|".join(argv)).encode("utf-8"), b""

        self.bridge = LocalCapabilityBridge(
            self.root,
            read_allowlist={"input.txt", "leaf-link", "dir-link/nested.txt"},
            write_allowlist={"output.txt"},
            executable_allowlist={"fixture-tool": "fixture-tool"},
            executor=executor,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reads_and_writes_only_inside_the_workspace(self) -> None:
        read = self.bridge.handle(
            capability_request("workspace.read", {"path": "input.txt", "maxBytes": 64})
        )
        self.assertEqual(read["type"], "capability.result")
        self.assertEqual(base64.b64decode(read["result"]["bytesBase64"]), b"fixture input\n")

        write = self.bridge.handle(
            capability_request(
                "workspace.write",
                {
                    "path": "output.txt",
                    "bytesBase64": base64.b64encode(b"written\n").decode("ascii"),
                },
            )
        )
        self.assertEqual(write["type"], "capability.result")
        self.assertEqual((self.root / "output.txt").read_bytes(), b"written\n")
        self.assertEqual((self.root / "output.txt").stat().st_mode & 0o777, 0o600)

    def test_rejects_traversal_absolute_paths_and_nul(self) -> None:
        for path in ("../outside", str(self.root.parent / "outside"), "bad\0name"):
            response = self.bridge.handle(
                capability_request("workspace.read", {"path": path, "maxBytes": 64})
            )
            self.assertEqual(response["type"], "capability.error")
            self.assertEqual(response["error"]["code"], "CAPABILITY_PATH_REJECTED")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink fixture unavailable")
    def test_rejects_symlink_leaf_and_parent(self) -> None:
        outside = self.root.parent / "outside-hosted-fixture.txt"
        outside.write_bytes(b"outside")
        os.symlink(outside, self.root / "leaf-link")
        outside_dir = self.root.parent / "outside-hosted-fixture-dir"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "nested.txt").write_bytes(b"outside nested")
        os.symlink(outside_dir, self.root / "dir-link")
        try:
            for path in ("leaf-link", "dir-link/nested.txt"):
                response = self.bridge.handle(
                    capability_request("workspace.read", {"path": path, "maxBytes": 64})
                )
                self.assertEqual(response["type"], "capability.error")
                self.assertEqual(response["error"]["code"], "CAPABILITY_SYMLINK_REJECTED")
        finally:
            outside.unlink(missing_ok=True)
            (outside_dir / "nested.txt").unlink(missing_ok=True)
            outside_dir.rmdir()

    def test_rejects_shells_but_preserves_metacharacters_as_one_argv_item(self) -> None:
        with self.assertRaises(ValueError):
            LocalCapabilityBridge(
                self.root,
                executable_allowlist={"unsafe": "/bin/sh"},
            )
        shell = self.bridge.handle(
            capability_request(
                "process.exec",
                {
                    "argv": ["sh", "-c", "touch escaped"],
                    "cwd": ".",
                    "env": {},
                    "timeoutMs": 50,
                    "maxOutputBytes": 128,
                },
            )
        )
        self.assertEqual(shell["type"], "capability.error")
        self.assertEqual(shell["error"]["code"], "CAPABILITY_SHELL_REJECTED")

        safe = self.bridge.handle(
            capability_request(
                "process.exec",
                {
                    "argv": ["fixture-tool", ";$(touch nope)"],
                    "cwd": ".",
                    "env": {"LANG": "C"},
                    "timeoutMs": 50,
                    "maxOutputBytes": 128,
                },
            )
        )
        self.assertEqual(safe["type"], "capability.result")
        self.assertEqual(self.executor_calls[0][0], ["fixture-tool", ";$(touch nope)"])
        self.assertFalse((self.root / "nope").exists())

    def test_rejects_unallowlisted_executable_and_environment(self) -> None:
        executable = self.bridge.handle(
            capability_request(
                "process.exec",
                {
                    "argv": ["other-tool"],
                    "cwd": ".",
                    "env": {},
                    "timeoutMs": 50,
                    "maxOutputBytes": 128,
                },
            )
        )
        self.assertEqual(executable["error"]["code"], "CAPABILITY_EXECUTABLE_REJECTED")

        environment = self.bridge.handle(
            capability_request(
                "process.exec",
                {
                    "argv": ["fixture-tool"],
                    "cwd": ".",
                    "env": {"OPENPROSE_TOKEN": "must-not-cross"},
                    "timeoutMs": 50,
                    "maxOutputBytes": 128,
                },
            )
        )
        self.assertEqual(environment["error"]["code"], "CAPABILITY_ENV_REJECTED")
        self.assertNotIn("must-not-cross", str(environment))

    def test_ungranted_workspace_paths_are_denied_even_when_inside_root(self) -> None:
        (self.root / "not-granted.txt").write_bytes(b"not granted")
        response = self.bridge.handle(
            capability_request("workspace.read", {"path": "not-granted.txt", "maxBytes": 64})
        )
        self.assertEqual(response["type"], "capability.error")
        self.assertEqual(response["error"]["code"], "CAPABILITY_PATH_REJECTED")
        self.assertTrue(self.bridge.snapshot()["defaultDeny"])

    def test_bridge_evidence_is_closed_and_explicitly_offline(self) -> None:
        self.bridge.handle(
            capability_request("workspace.read", {"path": "input.txt", "maxBytes": 64})
        )
        evidence = self.bridge.snapshot()
        jsonschema.Draft202012Validator(BRIDGE_EVIDENCE_SCHEMA).validate(evidence)
        self.assertFalse(evidence["shellEnabled"])
        self.assertFalse(evidence["networkEnabled"])

    def test_default_executor_aborts_oversize_output_and_reaps_direct_child(self) -> None:
        program = (
            "import os,pathlib,sys,time;"
            "pathlib.Path('child.pid').write_text(str(os.getpid()));"
            "chunk=b'x'*65536;"
            "[(sys.stdout.buffer.write(chunk),sys.stdout.buffer.flush()) for _ in range(16)];"
            "time.sleep(0.2);"
            "pathlib.Path('child-completed').write_text('unexpected')"
        )
        bridge = LocalCapabilityBridge(
            self.root,
            executable_allowlist={"fixture-python": [sys.executable, "-c", program]},
        )
        started = time.monotonic()
        response = bridge.handle(
            capability_request(
                "process.exec",
                {
                    "argv": ["fixture-python"],
                    "cwd": ".",
                    "env": {},
                    "timeoutMs": 3000,
                    "maxOutputBytes": 4096,
                },
            )
        )
        elapsed = time.monotonic() - started

        self.assertEqual(response["type"], "capability.error")
        self.assertEqual(response["error"]["code"], "CAPABILITY_OUTPUT_LIMIT")
        self.assertLess(elapsed, 2.0)
        self.assertTrue((self.root / "child.pid").exists())
        self.assertFalse((self.root / "child-completed").exists())
        evidence = bridge.snapshot()
        self.assertLessEqual(evidence["maxRetainedOutputBytesObserved"], 4096)
        self.assertTrue(evidence["directChildReaped"])
        self.assertEqual(evidence["processContainment"], "direct-child-only")
        self.assertEqual(evidence["descendantContainment"], "unsupported")
        jsonschema.Draft202012Validator(BRIDGE_EVIDENCE_SCHEMA).validate(evidence)

    def test_response_preserves_request_run_and_capability_correlation(self) -> None:
        response = self.bridge.handle(
            capability_request("workspace.read", {"path": "input.txt", "maxBytes": 64})
        )
        self.assertEqual(response["requestId"], "fixture-request-0001")
        self.assertEqual(response["runId"], "fixture-run-0001")
        self.assertEqual(response["correlationId"], "fixture-capability-0001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
