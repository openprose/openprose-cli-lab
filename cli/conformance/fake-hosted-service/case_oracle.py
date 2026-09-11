"""Executes hosted case manifests against the fake service, never a product CLI."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from capability_bridge import LocalCapabilityBridge
from fake_hosted_service import FakeHostedService
from protocol import ProtocolViolation


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]


def execute_case(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text("utf-8"))
    request = json.loads((REPOSITORY / manifest["requestFixture"]).read_text("utf-8"))
    service = FakeHostedService(manifest["serviceScenario"])
    service_records: list[dict[str, Any]] = []
    bridge_records: list[dict[str, Any]] = []
    finalize_error: str | None = None
    run_id: str | None = None

    with tempfile.TemporaryDirectory(prefix="openprose-hosted-oracle-") as directory:
        workspace = Path(directory) / "workspace"
        workspace.mkdir()
        (workspace / "input.txt").write_bytes(b"fixture input\n")
        outside = Path(directory) / "outside"
        outside.mkdir()
        (outside / "outside.txt").write_bytes(b"outside\n")
        if manifest["serviceScenario"] == "capability-symlink":
            os.symlink(outside, workspace / "link")

        for action in manifest["actions"]:
            action_type = action["type"]
            if action_type == "start":
                emitted = service.receive(request)
                service_records.extend(emitted)
                run_id = emitted[0]["runId"]
            elif action_type == "bridge.handle":
                capability = next(
                    record
                    for record in reversed(service_records)
                    if record["type"] == "capability.request"
                )
                arguments = capability["arguments"]
                read_grants = (
                    {arguments["path"]}
                    if capability["operation"] == "workspace.read"
                    else set()
                )
                write_grants = (
                    {arguments["path"]}
                    if capability["operation"] == "workspace.write"
                    else set()
                )
                bridge = LocalCapabilityBridge(
                    workspace,
                    read_allowlist=read_grants,
                    write_allowlist=write_grants,
                )
                response = bridge.handle(capability)
                bridge_records.append(response)
                service_records.extend(service.receive(response))
            elif action_type == "stream.credit":
                assert run_id is not None
                for _ in range(action["repeat"]):
                    outstanding = [
                        record
                        for record in service_records
                        if record["type"] == "assistant.delta"
                    ]
                    emitted = service.receive({
                        "schema": "openprose.hosted-wire/1",
                        "type": "stream.credit",
                        "requestId": request["requestId"],
                        "runId": run_id,
                        "acknowledgeThrough": outstanding[-1]["sequence"],
                        "grant": action["grant"],
                    })
                    service_records.extend(emitted)
            elif action_type == "replay":
                replay = copy.deepcopy(request)
                replay["requestId"] = action["requestId"]
                service_records.extend(service.receive(replay))
            elif action_type == "lifecycle.race":
                assert run_id is not None
                service_records.append(
                    service.settle_lifecycle_race(run_id, action["signals"])
                )
            elif action_type == "finalize":
                assert run_id is not None
                try:
                    service.finalize(run_id)
                except ProtocolViolation as error:
                    finalize_error = str(error)
            else:
                raise AssertionError(f"unhandled case action: {action_type}")

        assert run_id is not None
        evidence = service.evidence(run_id)

    expected = manifest["expected"]
    terminals = [
        record
        for record in service_records
        if record["type"] in {"run.terminal", "run.rejected"}
    ]
    if expected["terminalType"] == "missing-refused":
        assert not terminals
        assert finalize_error is not None and "terminal" in finalize_error
        observed_exit = 22
        observed_error = "PROTOCOL_TRUNCATED"
    else:
        assert terminals
        terminal = terminals[0]
        assert terminal["type"] == expected["terminalType"]
        observed_exit = terminal["error"]["exitCode"]
        observed_error = terminal["error"]["code"]
        assert terminal["semantic"]["status"] == "unknown"
        assert terminal["fallbackSelected"] is False

    event_types = [record["type"] for record in service_records]
    assert set(expected["requiredEventTypes"]).issubset(event_types)
    assert observed_exit == expected["exitCode"]
    if "error" in expected:
        assert observed_error == expected["error"]["code"]
        assert observed_exit == expected["error"]["exitCode"]
    usage_records = [record for record in service_records if record["type"] == "usage.report"]
    if expected["usageAuthority"] == "not-emitted":
        assert not usage_records
    else:
        assert usage_records[-1]["authority"] == expected["usageAuthority"]
    if "exactImageBytes" in expected:
        assert evidence["delivery"]["exactImageBytes"] is expected["exactImageBytes"]
    if "exactTaskBytes" in expected:
        assert evidence["delivery"]["exactTaskBytes"] is expected["exactTaskBytes"]
    if "maxInFlight" in expected:
        assert evidence["stream"]["maxInFlightObserved"] <= expected["maxInFlight"]
        assert evidence["stream"]["recordsDropped"] == 0
    if "sideEffectExecutions" in expected:
        assert evidence["idempotency"]["sideEffectExecutions"] == expected["sideEffectExecutions"]
    if "lifecycleWinner" in expected:
        assert evidence["lifecycle"]["winner"] == expected["lifecycleWinner"]
    if "capabilityError" in expected:
        assert any(
            record.get("error", {}).get("code") == expected["capabilityError"]
            for record in bridge_records
        )
    assert evidence["fallbackSelected"] is False

    return {
        "id": manifest["id"],
        "exitCode": observed_exit,
        "eventTypes": event_types,
        "bridgeRecordTypes": [record["type"] for record in bridge_records],
        "semanticStatus": "unknown",
        "fallbackSelected": False,
    }
