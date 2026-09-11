#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import jsonschema

from capability_bridge import LocalCapabilityBridge
from fake_hosted_service import (
    FakeHostedService,
    ProtocolViolation,
    choose_lifecycle_winner,
)
from test_support import start_record


HERE = Path(__file__).resolve().parent
WIRE_SCHEMA = json.loads((HERE / "hosted-protocol.v1.schema.json").read_text("utf-8"))
EVIDENCE_SCHEMA = json.loads((HERE / "hosted-evidence.v1.schema.json").read_text("utf-8"))


def validate_records(records: list[dict]) -> None:
    validator = jsonschema.Draft202012Validator(WIRE_SCHEMA)
    for record in records:
        validator.validate(record)


class FakeHostedServiceTest(unittest.TestCase):
    def test_jsonl_service_is_a_provider_free_black_box_process(self) -> None:
        fixture = (
            HERE.parent.parent
            / "shared"
            / "fixtures"
            / "hosted"
            / "requests"
            / "local-loop-gateway.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "evidence.json"
            request_frame = (
                json.dumps(
                    json.loads(fixture.read_text("utf-8")),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "fake_hosted_service.py"),
                    "--scenario",
                    "local-loop-gateway",
                    "--evidence-file",
                    str(evidence_path),
                ],
                input=request_frame,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )
            evidence = json.loads(evidence_path.read_text("utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        records = [json.loads(line) for line in completed.stdout.splitlines()]
        validate_records(records)
        self.assertEqual(records[-1]["type"], "run.terminal")
        jsonschema.Draft202012Validator(EVIDENCE_SCHEMA).validate(evidence)

    def test_local_loop_gateway_preserves_channels_and_ends_semantically_unknown(self) -> None:
        service = FakeHostedService("local-loop-gateway")
        records = service.receive(start_record())
        validate_records(records)

        self.assertEqual(records[0]["type"], "run.accepted")
        self.assertEqual(records[-1]["type"], "run.terminal")
        self.assertTrue(records[-1]["terminal"])
        self.assertEqual(records[-1]["semantic"]["status"], "unknown")
        self.assertEqual(records[-1]["semantic"]["reason"], "external-terminal-schema-unavailable")
        self.assertEqual(records[-1]["error"]["code"], "SEMANTIC_STATUS_UNKNOWN")
        self.assertFalse(records[-1]["fallbackSelected"])

        evidence = service.evidence(records[0]["runId"])
        jsonschema.Draft202012Validator(EVIDENCE_SCHEMA).validate(evidence)
        self.assertTrue(evidence["delivery"]["channelsSeparated"])
        self.assertTrue(evidence["delivery"]["exactImageBytes"])
        self.assertTrue(evidence["delivery"]["exactTaskBytes"])
        self.assertFalse(evidence["auth"]["secretObserved"])
        self.assertEqual(evidence["usage"]["authority"], "fixture-authoritative")

    def test_hosted_agent_round_trips_a_correlated_local_capability(self) -> None:
        service = FakeHostedService("hosted-agent-local-capability-bridge")
        accepted = service.receive(start_record(placement="hosted-agent-local-capability-bridge"))
        validate_records(accepted)
        capability = next(record for record in accepted if record["type"] == "capability.request")

        with self.subTest("independent bridge"):
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "input.txt").write_bytes(b"fixture input\n")
                response = LocalCapabilityBridge(root, read_allowlist={"input.txt"}).handle(capability)
                validate_records([response])
                completed = service.receive(response)

        validate_records(completed)
        self.assertEqual(completed[-1]["type"], "run.terminal")
        self.assertEqual(completed[-1]["semantic"]["status"], "unknown")
        evidence = service.evidence(capability["runId"])
        self.assertEqual(evidence["capabilities"]["completed"], 1)
        self.assertEqual(evidence["capabilities"]["correlationFailures"], 0)
        self.assertEqual(
            evidence["capabilities"]["localEvidenceAuthority"],
            "external-bridge-required",
        )

    def test_auth_quota_and_unavailable_have_closed_mappings_and_no_fallback(self) -> None:
        expectations = {
            "auth-required": ("HOSTED_AUTH_REQUIRED", 10),
            "quota-exceeded": ("HOSTED_QUOTA_EXCEEDED", 10),
            "unavailable": ("HOSTED_UNAVAILABLE", 10),
        }
        for scenario, (code, exit_code) in expectations.items():
            with self.subTest(scenario=scenario):
                service = FakeHostedService(scenario)
                records = service.receive(start_record())
                validate_records(records)
                terminal = records[-1]
                self.assertEqual(terminal["type"], "run.rejected")
                self.assertEqual(terminal["error"]["code"], code)
                self.assertEqual(terminal["error"]["exitCode"], exit_code)
                self.assertFalse(terminal["fallbackSelected"])
                self.assertNotIn("adapter", terminal)

    def test_usage_is_explicitly_authoritative_or_unavailable(self) -> None:
        authoritative = FakeHostedService("local-loop-gateway").receive(start_record())
        usage = next(record for record in authoritative if record["type"] == "usage.report")
        self.assertEqual(usage["authority"], "fixture-authoritative")
        self.assertIn("cost", usage)

        unavailable = FakeHostedService("usage-unavailable").receive(
            start_record(usage_authority="unavailable")
        )
        usage = next(record for record in unavailable if record["type"] == "usage.report")
        self.assertEqual(usage, {
            "schema": "openprose.hosted-wire/1",
            "type": "usage.report",
            "requestId": "fixture-request-0001",
            "runId": "fixture-run-0001",
            "sequence": 2,
            "authority": "unavailable",
            "reason": "fixture-usage-disabled",
        })

    def test_streaming_stops_at_credit_and_resumes_without_dropping_records(self) -> None:
        service = FakeHostedService("backpressure")
        first = service.receive(start_record(initial_credit=2))
        data = [record for record in first if record["type"] == "assistant.delta"]
        self.assertEqual([record["part"] for record in data], [0, 1])
        self.assertNotIn("run.terminal", [record["type"] for record in first])
        self.assertEqual(service.poll("fixture-run-0001"), [])

        second = service.receive({
            "schema": "openprose.hosted-wire/1",
            "type": "stream.credit",
            "requestId": "fixture-request-0001",
            "runId": "fixture-run-0001",
            "acknowledgeThrough": data[-1]["sequence"],
            "grant": 2,
        })
        third = service.receive({
            "schema": "openprose.hosted-wire/1",
            "type": "stream.credit",
            "requestId": "fixture-request-0001",
            "runId": "fixture-run-0001",
            "acknowledgeThrough": second[-1]["sequence"],
            "grant": 2,
        })
        parts = [record["part"] for record in first + second + third if record["type"] == "assistant.delta"]
        self.assertEqual(parts, list(range(6)))
        self.assertEqual((first + second + third)[-1]["type"], "run.terminal")
        evidence = service.evidence("fixture-run-0001")
        self.assertLessEqual(evidence["stream"]["maxInFlightObserved"], 2)
        self.assertLessEqual(
            evidence["stream"]["maxQueuedFramesObserved"],
            evidence["stream"]["maxQueuedFramesConfigured"],
        )
        self.assertEqual(evidence["stream"]["recordsDropped"], 0)

    def test_replay_is_idempotent_and_conflicting_payload_is_rejected(self) -> None:
        service = FakeHostedService("local-loop-gateway")
        first = service.receive(start_record(request_id="fixture-request-0001"))
        replay = service.receive(start_record(request_id="fixture-request-0002"))
        self.assertEqual(replay[0]["type"], "run.replayed")
        self.assertEqual(replay[0]["runId"], first[0]["runId"])
        self.assertEqual(replay[0]["state"], "terminal")
        self.assertEqual(
            replay[0]["terminalSnapshot"]["error"]["code"],
            "SEMANTIC_STATUS_UNKNOWN",
        )
        self.assertEqual(service.evidence(first[0]["runId"])["idempotency"]["sideEffectExecutions"], 1)

        conflict = start_record(request_id="fixture-request-0003")
        conflict["placementCandidate"] = "hosted-agent-local-capability-bridge"
        with self.assertRaisesRegex(ProtocolViolation, "idempotency"):
            service.receive(conflict)

    def test_capability_correlation_mismatch_is_refused_and_evidenced(self) -> None:
        service = FakeHostedService("hosted-agent-local-capability-bridge")
        records = service.receive(
            start_record(placement="hosted-agent-local-capability-bridge")
        )
        capability = next(record for record in records if record["type"] == "capability.request")
        response = {
            "schema": "openprose.hosted-wire/1",
            "type": "capability.result",
            "requestId": capability["requestId"],
            "runId": capability["runId"],
            "correlationId": "wrong-capability",
            "result": {"bytesBase64": "", "byteLength": 0},
        }
        with self.assertRaisesRegex(ProtocolViolation, "correlation"):
            service.receive(response)
        evidence = service.evidence(capability["runId"])
        self.assertFalse(evidence["correlation"]["allRecordsCorrelated"])
        self.assertEqual(evidence["correlation"]["failures"], 1)

    def test_disconnect_cancel_timeout_race_is_deterministic(self) -> None:
        self.assertEqual(
            choose_lifecycle_winner([
                {"cause": "disconnect", "atMs": 50},
                {"cause": "timeout", "atMs": 50},
                {"cause": "cancel", "atMs": 50},
            ]),
            {"cause": "cancel", "atMs": 50},
        )
        self.assertEqual(
            choose_lifecycle_winner([
                {"cause": "cancel", "atMs": 80},
                {"cause": "disconnect", "atMs": 20},
                {"cause": "timeout", "atMs": 40},
            ]),
            {"cause": "disconnect", "atMs": 20},
        )

        service = FakeHostedService("backpressure")
        service.receive(start_record(initial_credit=1))
        terminal = service.settle_lifecycle_race(
            "fixture-run-0001",
            [
                {"cause": "timeout", "atMs": 100},
                {"cause": "cancel", "atMs": 100},
                {"cause": "disconnect", "atMs": 90},
            ],
        )
        self.assertEqual(terminal["error"]["code"], "PROTOCOL_TRUNCATED")
        self.assertEqual(terminal["classification"], "runner-error")
        self.assertFalse(terminal["fallbackSelected"])
        self.assertEqual(service.settle_lifecycle_race("fixture-run-0001", [{"cause": "cancel", "atMs": 1}]), terminal)

    def test_finalize_fails_closed_without_terminal(self) -> None:
        service = FakeHostedService("no-terminal")
        records = service.receive(start_record())
        self.assertNotIn("run.terminal", [record["type"] for record in records])
        with self.assertRaisesRegex(ProtocolViolation, "terminal"):
            service.finalize("fixture-run-0001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
