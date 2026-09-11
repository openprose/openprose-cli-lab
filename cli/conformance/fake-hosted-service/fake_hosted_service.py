#!/usr/bin/env python3
"""Deterministic JSONL hosted-service oracle; it never contacts a provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from protocol import (
    ProtocolViolation,
    WIRE_SCHEMA,
    decode_content,
    encode_frame,
    start_fingerprint,
    validate_record,
)


VERSION = "1.0.0-research"
RUN_ID = "fixture-run-0001"
CAPABILITY_ID = "fixture-capability-0001"
PLACEMENTS = {
    "local-loop-gateway",
    "hosted-agent-local-capability-bridge",
}
SCENARIOS = {
    "local-loop-gateway",
    "hosted-agent-local-capability-bridge",
    "usage-unavailable",
    "auth-required",
    "quota-exceeded",
    "unavailable",
    "backpressure",
    "no-terminal",
    "capability-traversal",
    "capability-symlink",
    "capability-nul",
    "capability-shell",
}

_REJECTION_ERRORS = {
    "auth-required": ("HOSTED_AUTH_REQUIRED", 10, False),
    "quota-exceeded": ("HOSTED_QUOTA_EXCEEDED", 10, False),
    "unavailable": ("HOSTED_UNAVAILABLE", 10, False),
}
_LIFECYCLE = {
    "cancel": ("CANCELLED", 24, True, "cancelled"),
    "timeout": ("STARTUP_TIMEOUT", 21, True, "timeout"),
    "disconnect": ("PROTOCOL_TRUNCATED", 22, True, "runner-error"),
}
_RACE_PRIORITY = {"cancel": 0, "timeout": 1, "disconnect": 2}


def choose_lifecycle_winner(signals: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for signal in signals:
        if set(signal) != {"cause", "atMs"}:
            raise ProtocolViolation("lifecycle signal has unknown or missing fields")
        cause = signal["cause"]
        at_ms = signal["atMs"]
        if cause not in _RACE_PRIORITY or not isinstance(at_ms, int) or isinstance(at_ms, bool) or at_ms < 0:
            raise ProtocolViolation("lifecycle signal is invalid")
        candidates.append({"cause": cause, "atMs": at_ms})
    if not candidates:
        raise ProtocolViolation("lifecycle race requires at least one signal")
    return min(candidates, key=lambda item: (item["atMs"], _RACE_PRIORITY[item["cause"]]))


class _Run:
    def __init__(self, scenario: str, start: Mapping[str, Any], run_id: str) -> None:
        self.scenario = scenario
        self.start = dict(start)
        self.run_id = run_id
        self.request_id = start["requestId"]
        self.invocation_id = start["invocationId"]
        self.idempotency_key = start["idempotencyKey"]
        self.fingerprint = start_fingerprint(start)
        self.placement = start["placementCandidate"]
        self.sequence = 0
        self.pending_deltas: list[str] = []
        self.outstanding_data_sequences: list[int] = []
        self.credit = min(start["stream"]["initialCredit"], start["stream"]["maxInFlight"])
        self.max_in_flight = start["stream"]["maxInFlight"]
        self.max_in_flight_observed = 0
        self.max_frame_bytes = start["stream"]["maxFrameBytes"]
        self.max_queued_frames = start["stream"]["maxQueuedFrames"]
        self.max_queued_frames_observed = 1 if scenario == "backpressure" else 0
        self.replays = 0
        self.side_effect_executions = 1
        self.capability_requested = 0
        self.capability_completed = 0
        self.capability_rejected = 0
        self.capability_correlation_failures = 0
        self.expected_capability_id: str | None = None
        self.terminal_record: dict[str, Any] | None = None
        self.lifecycle_winner = "none"
        self.usage: dict[str, Any] = {
            "authority": start["billing"]["usageAuthority"],
        }
        self.usage_emitted = False
        image = decode_content(start["runtimeImage"], require_utf8=True)
        task = decode_content(start["taskEnvelope"], require_utf8=True)
        self.image_sha = start["runtimeImage"]["sha256"]
        self.task_sha = start["taskEnvelope"]["sha256"]
        self.exact_image = len(image) == start["runtimeImage"]["byteLength"]
        self.exact_task = len(task) == start["taskEnvelope"]["byteLength"]

    def record(self, record_type: str, **values: Any) -> dict[str, Any]:
        record = {
            "schema": WIRE_SCHEMA,
            "type": record_type,
            "requestId": self.request_id,
            "runId": self.run_id,
            "sequence": self.sequence,
            **values,
        }
        self.sequence += 1
        encode_frame(record, max_bytes=self.max_frame_bytes)
        return record


class FakeHostedService:
    def __init__(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown fake hosted scenario: {scenario}")
        self.scenario = scenario
        self._runs: dict[str, _Run] = {}
        self._idempotency: dict[str, _Run] = {}
        self._next_run = 1

    def receive(self, record: Mapping[str, Any]) -> list[dict[str, Any]]:
        validate_record(record)
        record_type = record["type"]
        if record_type == "run.start":
            return self._start(record)
        run = self._find_run(record)
        if run.terminal_record is not None:
            return [run.terminal_record]
        if record_type == "stream.credit":
            return self._credit(run, record)
        if record_type in {"capability.result", "capability.error"}:
            return self._capability_response(run, record)
        if record_type in {"run.cancel", "run.timeout", "run.disconnect"}:
            cause = record_type.split(".", 1)[1]
            return [self._lifecycle_terminal(run, {"cause": cause, "atMs": 0})]
        raise ProtocolViolation(f"record type is not accepted from the client: {record_type}")

    def poll(self, run_id: str) -> list[dict[str, Any]]:
        run = self._runs.get(run_id)
        if run is None:
            raise ProtocolViolation("unknown runId")
        if run.terminal_record is not None:
            return []
        return self._drain(run)

    def settle_lifecycle_race(
        self,
        run_id: str,
        signals: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise ProtocolViolation("unknown runId")
        if run.terminal_record is not None:
            return run.terminal_record
        return self._lifecycle_terminal(run, choose_lifecycle_winner(signals))

    def finalize(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise ProtocolViolation("unknown runId")
        if run.terminal_record is None:
            raise ProtocolViolation("hosted stream ended without a required terminal record")
        return run.terminal_record

    def evidence(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise ProtocolViolation("unknown runId")
        usage = dict(run.usage)
        if usage["authority"] == "fixture-authoritative":
            usage.update({
                "inputTokens": 7,
                "outputTokens": 3,
                "currency": "USD",
                "amountMicros": 11,
            })
        else:
            usage["reason"] = "fixture-usage-disabled"
        return {
            "schema": "openprose.hosted-evidence/1",
            "scenario": run.scenario,
            "placementCandidate": run.placement,
            "requestId": run.request_id,
            "invocationId": run.invocation_id,
            "runId": run.run_id,
            "delivery": {
                "channelsSeparated": True,
                "exactImageBytes": run.exact_image,
                "exactTaskBytes": run.exact_task,
                "imageSha256": run.image_sha,
                "taskSha256": run.task_sha,
            },
            "auth": {
                "category": "none-test-only",
                "credentialTransport": "none",
                "secretObserved": False,
            },
            "billing": {"owner": "openprose", "authority": "test-fixture-only"},
            "usage": usage,
            "correlation": {
                "allRecordsCorrelated": run.capability_correlation_failures == 0,
                "failures": run.capability_correlation_failures,
            },
            "stream": {
                "maxInFlightConfigured": run.max_in_flight,
                "maxInFlightObserved": run.max_in_flight_observed,
                "maxFrameBytes": run.max_frame_bytes,
                "maxQueuedFramesConfigured": run.max_queued_frames,
                "maxQueuedFramesObserved": run.max_queued_frames_observed,
                "recordsDropped": 0,
            },
            "idempotency": {
                "key": run.idempotency_key,
                "replays": run.replays,
                "sideEffectExecutions": run.side_effect_executions,
            },
            "capabilities": {
                "requested": run.capability_requested,
                "completed": run.capability_completed,
                "rejected": run.capability_rejected,
                "correlationFailures": run.capability_correlation_failures,
                "localEvidenceAuthority": (
                    "external-bridge-required"
                    if run.placement == "hosted-agent-local-capability-bridge"
                    else "not-applicable"
                ),
            },
            "lifecycle": {
                "settledOnce": run.terminal_record is not None,
                "winner": run.lifecycle_winner,
            },
            "terminal": {
                "present": run.terminal_record is not None,
                "semanticStatus": "unknown",
                "externalSchemaAvailable": False,
            },
            "fallbackSelected": False,
        }

    def _start(self, record: Mapping[str, Any]) -> list[dict[str, Any]]:
        key = record["idempotencyKey"]
        existing = self._idempotency.get(key)
        if existing is not None:
            if existing.fingerprint != start_fingerprint(record):
                raise ProtocolViolation("idempotency key was reused with a different payload")
            existing.replays += 1
            replay_values: dict[str, Any] = {
                "requestId": record["requestId"],
                "originalRequestId": existing.request_id,
                "idempotencyKey": key,
                "sideEffectsReexecuted": False,
                "state": "terminal" if existing.terminal_record is not None else "in-progress",
            }
            if existing.terminal_record is not None:
                replay_values["terminalSnapshot"] = {
                    "classification": existing.terminal_record["classification"],
                    "semantic": existing.terminal_record["semantic"],
                    "error": existing.terminal_record["error"],
                    "fallbackSelected": False,
                }
            replay = existing.record(
                "run.replayed",
                **replay_values,
            )
            return [replay]

        run_id = RUN_ID if self._next_run == 1 else f"fixture-run-{self._next_run:04d}"
        self._next_run += 1
        run = _Run(self.scenario, record, run_id)
        self._runs[run_id] = run
        self._idempotency[key] = run
        self._validate_placement(run)

        if self.scenario in _REJECTION_ERRORS:
            return [self._rejected(run, self.scenario)]
        accepted = run.record(
            "run.accepted",
            invocationId=run.invocation_id,
            placementCandidate=run.placement,
            billingOwner="openprose",
            authCategory="none-test-only",
        )
        if self._is_capability_scenario():
            return [accepted, self._capability_request(run)]
        if self.scenario == "backpressure":
            run.pending_deltas = [f"fixture-part-{index}" for index in range(6)]
        else:
            run.pending_deltas = ["fixture-local-loop-complete"]
        return [accepted, *self._drain(run)]

    def _validate_placement(self, run: _Run) -> None:
        expected = (
            "hosted-agent-local-capability-bridge"
            if self._is_capability_scenario()
            else "local-loop-gateway"
        )
        if run.placement != expected:
            raise ProtocolViolation(
                f"scenario {self.scenario} requires placementCandidate {expected}"
            )

    def _is_capability_scenario(self) -> bool:
        return self.scenario in {
            "hosted-agent-local-capability-bridge",
            "capability-traversal",
            "capability-symlink",
            "capability-nul",
            "capability-shell",
        }

    def _capability_request(self, run: _Run) -> dict[str, Any]:
        run.capability_requested += 1
        run.expected_capability_id = CAPABILITY_ID
        if self.scenario == "capability-traversal":
            operation = "workspace.read"
            arguments = {"path": "../outside", "maxBytes": 64}
        elif self.scenario == "capability-symlink":
            operation = "workspace.read"
            arguments = {"path": "link/outside.txt", "maxBytes": 64}
        elif self.scenario == "capability-nul":
            operation = "workspace.read"
            arguments = {"path": "bad\0path", "maxBytes": 64}
        elif self.scenario == "capability-shell":
            operation = "process.exec"
            arguments = {
                "argv": ["sh", "-c", "touch escaped"],
                "cwd": ".",
                "env": {},
                "timeoutMs": 50,
                "maxOutputBytes": 128,
            }
        else:
            operation = "workspace.read"
            arguments = {"path": "input.txt", "maxBytes": 64}
        return run.record(
            "capability.request",
            correlationId=CAPABILITY_ID,
            operation=operation,
            arguments=arguments,
        )

    def _capability_response(
        self,
        run: _Run,
        record: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        expected = run.expected_capability_id
        if record.get("requestId") != run.request_id or record.get("correlationId") != expected:
            run.capability_correlation_failures += 1
            raise ProtocolViolation("capability response correlation does not match the pending request")
        run.expected_capability_id = None
        if record["type"] == "capability.error":
            run.capability_rejected += 1
            return [self._terminal(
                run,
                code="LOCAL_CAPABILITY_REJECTED",
                exit_code=22,
                retryable=False,
                classification="runner-error",
                semantic_reason="transport-did-not-complete",
            )]
        run.capability_completed += 1
        return [self._usage(run), self._semantic_unknown_terminal(run)]

    def _credit(self, run: _Run, record: Mapping[str, Any]) -> list[dict[str, Any]]:
        required = {"schema", "type", "requestId", "runId", "acknowledgeThrough", "grant"}
        if set(record) != required:
            raise ProtocolViolation("stream.credit has unknown or missing fields")
        acknowledge = record["acknowledgeThrough"]
        grant = record["grant"]
        if not isinstance(acknowledge, int) or isinstance(acknowledge, bool) or acknowledge < 0:
            raise ProtocolViolation("stream.credit acknowledgement is invalid")
        if not isinstance(grant, int) or isinstance(grant, bool) or not 1 <= grant <= 16:
            raise ProtocolViolation("stream.credit grant is invalid")
        acknowledged = [value for value in run.outstanding_data_sequences if value <= acknowledge]
        if not acknowledged:
            raise ProtocolViolation("stream.credit does not acknowledge an outstanding data record")
        run.outstanding_data_sequences = [
            value for value in run.outstanding_data_sequences if value > acknowledge
        ]
        available_slots = run.max_in_flight - len(run.outstanding_data_sequences)
        run.credit += min(grant, available_slots)
        return self._drain(run)

    def _drain(self, run: _Run) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        while (
            run.pending_deltas
            and run.credit > 0
            and len(run.outstanding_data_sequences) < run.max_in_flight
        ):
            part = 6 - len(run.pending_deltas) if run.scenario == "backpressure" else 0
            text = run.pending_deltas.pop(0)
            record = run.record("assistant.delta", part=part, text=text)
            run.outstanding_data_sequences.append(record["sequence"])
            run.max_in_flight_observed = max(
                run.max_in_flight_observed,
                len(run.outstanding_data_sequences),
            )
            run.credit -= 1
            records.append(record)
        if run.pending_deltas:
            return records
        if run.scenario == "no-terminal":
            if not run.usage_emitted:
                records.append(self._usage(run))
            return records
        records.append(self._usage(run))
        records.append(self._semantic_unknown_terminal(run))
        return records

    def _usage(self, run: _Run) -> dict[str, Any]:
        if run.usage_emitted:
            raise ProtocolViolation("usage evidence was emitted more than once")
        run.usage_emitted = True
        if run.start["billing"]["usageAuthority"] == "unavailable" or run.scenario == "usage-unavailable":
            run.usage = {"authority": "unavailable"}
            return run.record(
                "usage.report",
                authority="unavailable",
                reason="fixture-usage-disabled",
            )
        run.usage = {"authority": "fixture-authoritative"}
        return run.record(
            "usage.report",
            authority="fixture-authoritative",
            usage={"inputTokens": 7, "outputTokens": 3},
            cost={"currency": "USD", "amountMicros": 11},
        )

    def _semantic_unknown_terminal(self, run: _Run) -> dict[str, Any]:
        return self._terminal(
            run,
            code="SEMANTIC_STATUS_UNKNOWN",
            exit_code=23,
            retryable=False,
            classification="runner-error",
            semantic_reason="external-terminal-schema-unavailable",
        )

    def _rejected(self, run: _Run, scenario: str) -> dict[str, Any]:
        code, exit_code, retryable = _REJECTION_ERRORS[scenario]
        record = run.record(
            "run.rejected",
            terminal=True,
            classification="runner-error",
            semantic={
                "status": "unknown",
                "reason": "transport-did-not-complete",
                "externalSchemaAvailable": False,
            },
            error={"code": code, "exitCode": exit_code, "retryable": retryable},
            fallbackSelected=False,
        )
        run.terminal_record = record
        return record

    def _lifecycle_terminal(
        self,
        run: _Run,
        winner: Mapping[str, Any],
    ) -> dict[str, Any]:
        code, exit_code, retryable, classification = _LIFECYCLE[winner["cause"]]
        run.lifecycle_winner = winner["cause"]
        return self._terminal(
            run,
            code=code,
            exit_code=exit_code,
            retryable=retryable,
            classification=classification,
            semantic_reason="transport-did-not-complete",
            lifecycle={"winner": winner["cause"], "atMs": winner["atMs"]},
        )

    def _terminal(
        self,
        run: _Run,
        *,
        code: str,
        exit_code: int,
        retryable: bool,
        classification: str,
        semantic_reason: str,
        lifecycle: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if run.terminal_record is not None:
            return run.terminal_record
        values: dict[str, Any] = {
            "terminal": True,
            "classification": classification,
            "semantic": {
                "status": "unknown",
                "reason": semantic_reason,
                "externalSchemaAvailable": False,
            },
            "error": {"code": code, "exitCode": exit_code, "retryable": retryable},
            "fallbackSelected": False,
        }
        if lifecycle is not None:
            values["lifecycle"] = dict(lifecycle)
        run.terminal_record = run.record("run.terminal", **values)
        return run.terminal_record

    def _find_run(self, record: Mapping[str, Any]) -> _Run:
        run_id = record.get("runId")
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            raise ProtocolViolation("unknown runId")
        if record.get("requestId") != run.request_id:
            raise ProtocolViolation("requestId does not correlate to runId")
        return run


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="openprose-fake-hosted-service")
    result.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    result.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    result.add_argument("--evidence-file", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    service = FakeHostedService(args.scenario)
    observed_run: str | None = None
    try:
        for raw_line in sys.stdin.buffer:
            if len(raw_line) > 65536:
                raise ProtocolViolation("input frame exceeds absolute fixture limit")
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProtocolViolation("input frame is not one valid UTF-8 JSON value") from error
            records = service.receive(record)
            for response in records:
                observed_run = response["runId"]
                sys.stdout.buffer.write(encode_frame(response, max_bytes=65536))
            sys.stdout.buffer.flush()
    except ProtocolViolation as error:
        print(f"fake hosted protocol violation: {error}", file=sys.stderr)
        return 22
    if args.evidence_file is not None and observed_run is not None:
        evidence = service.evidence(observed_run)
        args.evidence_file.write_text(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
