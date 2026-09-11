#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema

from protocol import (
    ProtocolViolation,
    decode_content,
    encode_frame,
    start_fingerprint,
    validate_record,
)
from test_support import IMAGE, TASK, start_record


HERE = Path(__file__).resolve().parent
WIRE_SCHEMA = json.loads((HERE / "hosted-protocol.v1.schema.json").read_text("utf-8"))


class HostedProtocolTest(unittest.TestCase):
    def test_all_local_contract_schemas_are_valid_draft_2020_12(self) -> None:
        paths = [
            HERE / "hosted-protocol.v1.schema.json",
            HERE / "hosted-evidence.v1.schema.json",
            HERE / "capability-bridge-evidence.v1.schema.json",
            HERE.parent / "cases" / "hosted" / "hosted-case-manifest.schema.json",
        ]
        for path in paths:
            with self.subTest(schema=path.name):
                jsonschema.Draft202012Validator.check_schema(
                    json.loads(path.read_text("utf-8"))
                )

    def test_start_keeps_exact_image_and_task_in_separate_channels(self) -> None:
        record = start_record()
        validate_record(record)
        jsonschema.Draft202012Validator(WIRE_SCHEMA).validate(record)

        self.assertEqual(decode_content(record["runtimeImage"], require_utf8=True), IMAGE)
        self.assertEqual(decode_content(record["taskEnvelope"], require_utf8=True), TASK)
        self.assertNotEqual(record["runtimeImage"]["sha256"], record["taskEnvelope"]["sha256"])
        self.assertNotIn("taskEnvelope", record["runtimeImage"])
        self.assertNotIn("runtimeImage", record["taskEnvelope"])

    def test_start_schema_has_no_secret_or_token_channel(self) -> None:
        record = start_record()
        record["auth"]["token"] = "must-not-cross"

        with self.assertRaises(ProtocolViolation):
            validate_record(record)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(WIRE_SCHEMA).validate(record)

    def test_content_digest_and_length_are_verified_before_acceptance(self) -> None:
        bad_digest = start_record()
        bad_digest["runtimeImage"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ProtocolViolation, "sha256"):
            validate_record(bad_digest)

        bad_length = start_record()
        bad_length["taskEnvelope"]["byteLength"] += 1
        with self.assertRaisesRegex(ProtocolViolation, "byteLength"):
            validate_record(bad_length)

    def test_idempotency_fingerprint_ignores_only_request_id(self) -> None:
        first = start_record(request_id="fixture-request-0001")
        replay = start_record(request_id="fixture-request-0002")
        changed = start_record(request_id="fixture-request-0003")
        changed["taskEnvelope"] = dict(changed["taskEnvelope"])
        changed["taskEnvelope"]["bytesBase64"] = "e30K"
        changed["taskEnvelope"]["byteLength"] = 3
        changed["taskEnvelope"]["sha256"] = "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356"

        self.assertEqual(start_fingerprint(first), start_fingerprint(replay))
        self.assertNotEqual(start_fingerprint(first), start_fingerprint(changed))

    def test_frame_limit_is_checked_on_encoded_utf8_bytes(self) -> None:
        frame = {
            "schema": "openprose.hosted-wire/1",
            "type": "assistant.delta",
            "requestId": "fixture-request-0001",
            "runId": "fixture-run-0001",
            "sequence": 1,
            "text": "雪" * 20,
        }
        with self.assertRaisesRegex(ProtocolViolation, "frame"):
            encode_frame(frame, max_bytes=32)


if __name__ == "__main__":
    unittest.main(verbosity=2)
