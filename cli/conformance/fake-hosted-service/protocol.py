"""Closed, stdlib-only helpers for the scripted hosted research protocol."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
from typing import Any, Mapping


WIRE_SCHEMA = "openprose.hosted-wire/1"
MAX_CONTENT_BYTES = 1024 * 1024
START_KEYS = frozenset({
    "schema", "type", "requestId", "invocationId", "idempotencyKey",
    "placementCandidate", "auth", "billing", "runtimeImage", "taskEnvelope",
    "stream",
})
CAPABILITY_ERROR_CODES = frozenset({
    "CAPABILITY_PATH_REJECTED", "CAPABILITY_SYMLINK_REJECTED",
    "CAPABILITY_NOT_FOUND", "CAPABILITY_NOT_FILE", "CAPABILITY_INPUT_LIMIT",
    "CAPABILITY_OUTPUT_LIMIT", "CAPABILITY_SHELL_REJECTED",
    "CAPABILITY_EXECUTABLE_REJECTED", "CAPABILITY_ENV_REJECTED",
    "CAPABILITY_OPERATION_REJECTED", "CAPABILITY_EXECUTION_FAILED",
})


class ProtocolViolation(ValueError):
    """A deterministic protocol-boundary refusal."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def encode_frame(value: Mapping[str, Any], *, max_bytes: int) -> bytes:
    encoded = canonical_json_bytes(value) + b"\n"
    if len(encoded) > max_bytes:
        raise ProtocolViolation(
            f"encoded frame is {len(encoded)} bytes, exceeding limit {max_bytes}"
        )
    return encoded


def decode_content(value: Mapping[str, Any], *, require_utf8: bool) -> bytes:
    expected = {"encoding", "byteLength", "sha256", "bytesBase64"}
    if set(value) != expected:
        raise ProtocolViolation("content object has unknown or missing fields")
    try:
        decoded = base64.b64decode(value["bytesBase64"], validate=True)
    except (binascii.Error, TypeError) as error:
        raise ProtocolViolation("content bytesBase64 is invalid") from error
    if len(decoded) > MAX_CONTENT_BYTES:
        raise ProtocolViolation("content exceeds fixture limit")
    if value["byteLength"] != len(decoded):
        raise ProtocolViolation("content byteLength does not match decoded bytes")
    digest = hashlib.sha256(decoded).hexdigest()
    if value["sha256"] != digest:
        raise ProtocolViolation("content sha256 does not match decoded bytes")
    if require_utf8:
        try:
            decoded.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ProtocolViolation("content is not valid UTF-8") from error
    return decoded


def validate_record(record: Mapping[str, Any]) -> None:
    if record.get("schema") != WIRE_SCHEMA:
        raise ProtocolViolation("unsupported hosted wire schema")
    record_type = record.get("type")
    if record_type == "run.start":
        _validate_start(record)
        return
    if record_type == "stream.credit":
        _validate_stream_credit(record)
        return
    if record_type in {"run.cancel", "run.timeout", "run.disconnect"}:
        _validate_lifecycle_signal(record)
        return
    if record_type in {"capability.result", "capability.error"}:
        _validate_capability_response(record)
        return
    if not isinstance(record_type, str):
        raise ProtocolViolation("record type is required")
    encoded = canonical_json_bytes(record)
    if len(encoded) + 1 > 65536:
        raise ProtocolViolation("record exceeds absolute fixture frame limit")


def _validate_start(record: Mapping[str, Any]) -> None:
    if set(record) != START_KEYS:
        raise ProtocolViolation("run.start has unknown or missing fields")
    for name in ("requestId", "invocationId", "idempotencyKey"):
        value = record[name]
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ProtocolViolation(f"{name} is invalid")
    if record["placementCandidate"] not in {
        "local-loop-gateway",
        "hosted-agent-local-capability-bridge",
    }:
        raise ProtocolViolation("placementCandidate is invalid")
    auth = record["auth"]
    if not isinstance(auth, Mapping) or set(auth) != {"category", "credentialTransport"}:
        raise ProtocolViolation("auth must be closed and secret-free")
    if auth != {"category": "none-test-only", "credentialTransport": "none"}:
        raise ProtocolViolation("only secret-free fixture auth is accepted")
    billing = record["billing"]
    if not isinstance(billing, Mapping) or set(billing) != {"owner", "usageAuthority"}:
        raise ProtocolViolation("billing fields are invalid")
    if billing.get("owner") != "openprose" or billing.get("usageAuthority") not in {
        "fixture-authoritative", "unavailable",
    }:
        raise ProtocolViolation("billing values are invalid")
    image = record["runtimeImage"]
    task = record["taskEnvelope"]
    if not isinstance(image, Mapping) or not isinstance(task, Mapping):
        raise ProtocolViolation("runtimeImage and taskEnvelope must be separate objects")
    if image.get("encoding") != "utf-8" or task.get("encoding") != "json-utf-8":
        raise ProtocolViolation("content encoding is invalid")
    decode_content(image, require_utf8=True)
    decode_content(task, require_utf8=True)
    stream = record["stream"]
    if not isinstance(stream, Mapping) or set(stream) != {
        "initialCredit", "maxInFlight", "maxFrameBytes", "maxQueuedFrames",
    }:
        raise ProtocolViolation("stream fields are invalid")
    if not _bounded_int(stream["initialCredit"], 0, 16):
        raise ProtocolViolation("initialCredit is invalid")
    if not _bounded_int(stream["maxInFlight"], 1, 16):
        raise ProtocolViolation("maxInFlight is invalid")
    if not _bounded_int(stream["maxQueuedFrames"], 1, 16):
        raise ProtocolViolation("maxQueuedFrames is invalid")
    if not _bounded_int(stream["maxFrameBytes"], 256, 65536):
        raise ProtocolViolation("maxFrameBytes is invalid")


def _bounded_int(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _validate_stream_credit(record: Mapping[str, Any]) -> None:
    expected = {"schema", "type", "requestId", "runId", "acknowledgeThrough", "grant"}
    if set(record) != expected:
        raise ProtocolViolation("stream.credit has unknown or missing fields")
    if not _bounded_int(record["acknowledgeThrough"], 0, 2**63 - 1):
        raise ProtocolViolation("stream.credit acknowledgeThrough is invalid")
    if not _bounded_int(record["grant"], 1, 16):
        raise ProtocolViolation("stream.credit grant is invalid")
    _validate_correlation_ids(record)


def _validate_lifecycle_signal(record: Mapping[str, Any]) -> None:
    allowed = {"schema", "type", "requestId", "runId", "reason"}
    required = {"schema", "type", "requestId", "runId"}
    if not required.issubset(record) or not set(record).issubset(allowed):
        raise ProtocolViolation("lifecycle signal has unknown or missing fields")
    reason = record.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 256 or "\0" in reason):
        raise ProtocolViolation("lifecycle reason is invalid")
    _validate_correlation_ids(record)


def _validate_capability_response(record: Mapping[str, Any]) -> None:
    payload_name = "result" if record["type"] == "capability.result" else "error"
    expected = {"schema", "type", "requestId", "runId", "correlationId", payload_name}
    if set(record) != expected:
        raise ProtocolViolation("capability response has unknown or missing fields")
    _validate_correlation_ids(record, include_capability=True)
    payload = record[payload_name]
    if not isinstance(payload, Mapping):
        raise ProtocolViolation("capability response payload must be an object")
    if payload_name == "error":
        if set(payload) != {"code", "message"}:
            raise ProtocolViolation("capability error has unknown or missing fields")
        if payload["code"] not in CAPABILITY_ERROR_CODES:
            raise ProtocolViolation("capability error code is invalid")
        if not isinstance(payload["message"], str) or not 1 <= len(payload["message"]) <= 256:
            raise ProtocolViolation("capability error message is invalid")
        return
    allowed = {
        "bytesBase64", "byteLength", "bytesWritten", "exitCode",
        "stdoutBase64", "stderrBase64",
    }
    shapes = [
        {"bytesBase64", "byteLength"},
        {"bytesWritten"},
        {"exitCode", "stdoutBase64", "stderrBase64"},
    ]
    if set(payload) not in shapes or not set(payload).issubset(allowed):
        raise ProtocolViolation("capability result has unknown or missing fields")
    for name in ("bytesBase64", "stdoutBase64", "stderrBase64"):
        if name in payload:
            try:
                base64.b64decode(payload[name], validate=True)
            except (binascii.Error, TypeError) as error:
                raise ProtocolViolation(f"capability result {name} is invalid") from error
    for name in ("byteLength", "bytesWritten"):
        if name in payload and not _bounded_int(payload[name], 0, MAX_CONTENT_BYTES):
            raise ProtocolViolation(f"capability result {name} is invalid")
    if "bytesBase64" in payload:
        decoded_length = len(base64.b64decode(payload["bytesBase64"], validate=True))
        if payload["byteLength"] != decoded_length:
            raise ProtocolViolation("capability result byteLength does not match decoded bytes")
    if "exitCode" in payload and not _bounded_int(payload["exitCode"], 0, 255):
        raise ProtocolViolation("capability result exitCode is invalid")


def _validate_correlation_ids(
    record: Mapping[str, Any],
    *,
    include_capability: bool = False,
) -> None:
    names = ["requestId", "runId"]
    if include_capability:
        names.append("correlationId")
    for name in names:
        value = record.get(name)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ProtocolViolation(f"{name} is invalid")


def start_fingerprint(record: Mapping[str, Any]) -> str:
    if record.get("type") != "run.start":
        raise ProtocolViolation("only run.start records have idempotency fingerprints")
    value = copy.deepcopy(dict(record))
    value.pop("requestId", None)
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
