#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import jsonschema


HERE = Path(__file__).resolve().parent
HARNESS = HERE / "fake_harness.py"
EVENT_SCHEMA = json.loads((HERE / "fake-harness-event.schema.json").read_text("utf-8"))
OBSERVATION_SCHEMA = json.loads((HERE / "fake-harness-observation.schema.json").read_text("utf-8"))


class FakeHarnessTest(unittest.TestCase):
    def invoke(self, scenario: str, *, delay_ms: int = 1) -> tuple[subprocess.CompletedProcess[bytes], dict, bytes, bytes]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = b"image\x00bytes\n\xce\xbb\n"
            task = b'{"argv":["prose","run","space here",";$(nope)"],"interactionMode":"non-interactive"}\n'
            image_path = root / "image.bin"
            task_path = root / "task.json"
            observation_path = root / "observation.json"
            image_path.write_bytes(image)
            task_path.write_bytes(task)
            command = [
                sys.executable, str(HARNESS), "run", "--scenario", scenario,
                "--image-file", str(image_path), "--task-file", str(task_path),
                "--observation-file", str(observation_path), "--delay-ms", str(delay_ms),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=5,
                env={
                    "OPENPROSE_INVOCATION_ID": "fixture-invocation-0001",
                    "OPENPROSE_RUN_NONCE": "fixture-nonce-0001",
                    "UNRELATED_SECRET": "must-not-be-observed",
                },
            )
            return completed, json.loads(observation_path.read_text("utf-8")), image, task

    def test_observes_exact_image_task_and_allowlisted_environment(self) -> None:
        completed, observation, image, task = self.invoke("success")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(base64.b64decode(observation["image"]["bytesBase64"]), image)
        self.assertEqual(base64.b64decode(observation["task"]["bytesBase64"]), task)
        self.assertEqual(
            observation["environment"],
            {
                "OPENPROSE_INVOCATION_ID": "fixture-invocation-0001",
                "OPENPROSE_RUN_NONCE": "fixture-nonce-0001",
            },
        )
        jsonschema.Draft202012Validator(OBSERVATION_SCHEMA).validate(observation)
        records = [json.loads(line) for line in completed.stdout.splitlines()]
        for record in records:
            jsonschema.Draft202012Validator(EVENT_SCHEMA).validate(record)
        self.assertEqual([record["type"] for record in records], ["session.started", "assistant.message", "session.completed"])
        self.assertEqual(records[-1]["terminalEnvelope"]["semanticStatus"], "not-applicable")

    def test_failure_stream_scenarios_are_distinct(self) -> None:
        malformed, _, _, _ = self.invoke("malformed")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(malformed.stdout)
        truncated, _, _, _ = self.invoke("truncated")
        self.assertFalse(truncated.stdout.endswith(b"\n"))
        eof, _, _, _ = self.invoke("eof-without-terminal")
        self.assertNotIn(b"session.completed", eof.stdout)
        nonzero, _, _, _ = self.invoke("nonzero")
        self.assertEqual(nonzero.returncode, 17)
        terminal_nonzero, _, _, _ = self.invoke("terminal-nonzero")
        self.assertEqual(terminal_nonzero.returncode, 17)
        self.assertIn(b"session.completed", terminal_nonzero.stdout)

    def test_delay_stderr_fragmentation_and_crlf(self) -> None:
        start = time.monotonic()
        delayed, _, _, _ = self.invoke("delay", delay_ms=30)
        self.assertGreaterEqual(time.monotonic() - start, 0.025)
        self.assertEqual(delayed.returncode, 0)
        stderr, _, _, _ = self.invoke("stderr")
        self.assertEqual(stderr.returncode, 0)
        self.assertIn(b"stderr remains separate", stderr.stderr)
        self.assertNotIn(b"stderr remains separate", stderr.stdout)
        fragmented, _, _, _ = self.invoke("fragmented")
        self.assertEqual(len(fragmented.stdout.splitlines()), 3)
        crlf, _, _, _ = self.invoke("crlf")
        self.assertIn(b"\r\n", crlf.stdout)

    @unittest.skipUnless(hasattr(os, "killpg"), "Unix process-group fixture")
    def test_descendant_fixture_stays_in_owned_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.bin"
            task_path = root / "task.json"
            pid_path = root / "descendants.json"
            image_path.write_bytes(b"image")
            task_path.write_bytes(b"{}")
            process = subprocess.Popen(
                [
                    sys.executable, str(HARNESS), "run", "--scenario", "descendant",
                    "--image-file", str(image_path), "--task-file", str(task_path),
                    "--descendant-pid-file", str(pid_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            captured_stdout = b""
            try:
                deadline = time.monotonic() + 5
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists())
                identities = json.loads(pid_path.read_text("utf-8"))
                self.assertEqual(identities["processGroupId"], process.pid)
                self.assertFalse(identities["attemptedDetachment"])
                self.assertGreater(identities["childPid"], 0)
                self.assertGreater(identities["grandchildPid"], 0)
                assert process.stdout is not None
                while captured_stdout.count(b"\n") < 2:
                    remaining = max(0.0, deadline - time.monotonic())
                    readable, _, _ = select.select([process.stdout], [], [], remaining)
                    self.assertTrue(readable, "fixture did not publish descendant events")
                    chunk = os.read(process.stdout.fileno(), 4096)
                    self.assertTrue(chunk, "fixture closed stdout before descendant events")
                    captured_stdout += chunk
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                remaining_stdout, _ = process.communicate(timeout=5)
                captured_stdout += remaining_stdout
            records = [json.loads(line) for line in captured_stdout.splitlines()]
            self.assertEqual([record["type"] for record in records], ["session.started", "fixture.descendants"])
            for record in records:
                jsonschema.Draft202012Validator(EVENT_SCHEMA).validate(record)


if __name__ == "__main__":
    unittest.main(verbosity=2)
