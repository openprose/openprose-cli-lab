from __future__ import annotations

import base64
import hashlib
from typing import Any


IMAGE = "opaque hosted image\nλ\n".encode("utf-8")
TASK = b'{"schema":"openprose.task-envelope/1","argv":["prose","run","space here",";$(nope)"],"interactionMode":"non-interactive"}\n'


def content(value: bytes, encoding: str) -> dict[str, Any]:
    return {
        "encoding": encoding,
        "byteLength": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
        "bytesBase64": base64.b64encode(value).decode("ascii"),
    }


def start_record(
    *,
    placement: str = "local-loop-gateway",
    request_id: str = "fixture-request-0001",
    idempotency_key: str = "fixture-idempotency-0001",
    usage_authority: str = "fixture-authoritative",
    initial_credit: int = 8,
) -> dict[str, Any]:
    return {
        "schema": "openprose.hosted-wire/1",
        "type": "run.start",
        "requestId": request_id,
        "invocationId": "fixture-invocation-0001",
        "idempotencyKey": idempotency_key,
        "placementCandidate": placement,
        "auth": {
            "category": "none-test-only",
            "credentialTransport": "none",
        },
        "billing": {
            "owner": "openprose",
            "usageAuthority": usage_authority,
        },
        "runtimeImage": content(IMAGE, "utf-8"),
        "taskEnvelope": content(TASK, "json-utf-8"),
        "stream": {
            "initialCredit": initial_credit,
            "maxInFlight": 2,
            "maxFrameBytes": 4096,
            "maxQueuedFrames": 2,
        },
    }


def capability_request(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "openprose.hosted-wire/1",
        "type": "capability.request",
        "requestId": "fixture-request-0001",
        "runId": "fixture-run-0001",
        "sequence": 1,
        "correlationId": "fixture-capability-0001",
        "operation": operation,
        "arguments": arguments,
    }
