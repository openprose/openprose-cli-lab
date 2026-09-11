#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

import jsonschema

from case_oracle import execute_case


HERE = Path(__file__).resolve().parent
CASES = HERE.parent / "cases" / "hosted"
FIXTURES = HERE.parent.parent / "shared" / "fixtures" / "hosted"


class HostedCaseManifestTest(unittest.TestCase):
    def test_case_set_is_closed_schema_valid_and_digest_pinned(self) -> None:
        schema = json.loads((CASES / "hosted-case-manifest.schema.json").read_text("utf-8"))
        index = json.loads((CASES / "case-index.v1.json").read_text("utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        observed: dict[str, str] = {}
        for path in sorted(CASES.glob("*.json")):
            if path.name in {"hosted-case-manifest.schema.json", "case-index.v1.json"}:
                continue
            encoded = path.read_bytes()
            manifest = json.loads(encoded)
            validator.validate(manifest)
            observed[path.name] = hashlib.sha256(encoded).hexdigest()
        self.assertEqual(index["cases"], observed)

    def test_cases_cover_every_required_hosted_transport_risk(self) -> None:
        tags: set[str] = set()
        placements: set[str] = set()
        errors: set[str] = set()
        for path in CASES.glob("*.json"):
            if path.name in {"hosted-case-manifest.schema.json", "case-index.v1.json"}:
                continue
            manifest = json.loads(path.read_text("utf-8"))
            tags.update(manifest["tags"])
            placements.add(manifest["placementCandidate"])
            if "error" in manifest["expected"]:
                errors.add(manifest["expected"]["error"]["code"])
            self.assertFalse(manifest["expected"]["fallbackSelected"])
            self.assertEqual(manifest["expected"]["semanticStatus"], "unknown")
        self.assertEqual(
            placements,
            {"local-loop-gateway", "hosted-agent-local-capability-bridge"},
        )
        self.assertTrue({
            "image-exactness", "task-exactness", "auth", "billing", "quota",
            "usage", "correlation", "backpressure", "replay", "disconnect",
            "cancellation", "timeout", "no-fallback", "least-privilege",
            "traversal", "symlink", "nul", "shell", "terminal",
        }.issubset(tags))
        self.assertTrue({
            "HOSTED_AUTH_REQUIRED", "HOSTED_QUOTA_EXCEEDED", "HOSTED_UNAVAILABLE",
        }.issubset(errors))

    def test_request_fixtures_are_protocol_valid_and_secret_free(self) -> None:
        wire_schema = json.loads((HERE / "hosted-protocol.v1.schema.json").read_text("utf-8"))
        validator = jsonschema.Draft202012Validator(wire_schema)
        paths = sorted((FIXTURES / "requests").glob("*.json"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            request = json.loads(path.read_text("utf-8"))
            validator.validate(request)
            encoded = path.read_text("utf-8").lower()
            self.assertNotIn('"token"', encoded)
            self.assertNotIn('"secret"', encoded)
            self.assertIn("runtimeImage", request)
            self.assertIn("taskEnvelope", request)
            self.assertNotEqual(request["runtimeImage"]["sha256"], request["taskEnvelope"]["sha256"])

    def test_every_manifest_executes_against_the_independent_oracle(self) -> None:
        results = []
        for path in sorted(CASES.glob("*.json")):
            if path.name in {"hosted-case-manifest.schema.json", "case-index.v1.json"}:
                continue
            results.append(execute_case(path))
        self.assertEqual(len(results), 14)
        self.assertTrue(all(result["semanticStatus"] == "unknown" for result in results))
        self.assertTrue(all(result["fallbackSelected"] is False for result in results))


if __name__ == "__main__":
    unittest.main(verbosity=2)
