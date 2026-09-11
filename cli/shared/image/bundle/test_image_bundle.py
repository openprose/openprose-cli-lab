#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "image_bundle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("openprose_image_bundle", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_image(root: Path, *, release_eligible: bool = False) -> dict[str, object]:
    payloads = [
        ("payload/alpha.md", b"alpha\n"),
        ("payload/nested/beta.md", "beta snowman \u2603\n".encode()),
        ("payload/omega.txt", b"omega\n"),
    ]
    artifacts = {
        "contracts/task-v9.json": b'{"kind":"task"}\n',
        "contracts/framing-v9.txt": b"frame {{IMAGE_BYTES}}\n",
        "contracts/terminal-v9.json": b'{"kind":"terminal"}\n',
    }
    for path, body in [*payloads, *artifacts.items()]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    model_visible = b"".join(body for _, body in payloads)
    aggregate = hashlib.sha256()
    descriptors = []
    for path, body in payloads:
        descriptors.append(
            {
                "path": path,
                "mediaType": "text/markdown; charset=utf-8",
                "byteLength": len(body),
                "sha256": sha256(body),
            }
        )
        aggregate.update(path.encode())
        aggregate.update(b"\0")
        aggregate.update(str(len(body)).encode())
        aggregate.update(b"\0")
        aggregate.update(body)
        aggregate.update(b"\0")
    manifest: dict[str, object] = {
        "schema": "openprose.skill-runtime-image-manifest/1",
        "imageFormatVersion": "openprose.skill-runtime-image/1",
        "imageVersion": "synthetic-three-v1",
        "languageVersion": "synthetic",
        "skillVersion": "synthetic",
        "runtimeContractVersion": "synthetic",
        "semanticSourceRevision": "synthetic",
        "purpose": "canonical-language-runtime",
        "releaseEligible": release_eligible,
        "normalization": {
            "encoding": "utf-8",
            "newlines": "lf",
            "byteOrderMark": "forbidden",
            "pathSeparator": "/",
        },
        "payload": descriptors,
        "modelVisibleBytes": {
            "serialization": "ordered-raw-concatenation-v1",
            "byteLength": len(model_visible),
            "sha256": sha256(model_visible),
        },
        "aggregateSha256": {
            "algorithm": "sha256-path-length-nul-v1",
            "sha256": aggregate.hexdigest(),
        },
        "instructionPlacements": [
            {
                "id": "developer",
                "strictness": "strict",
                "preservesHarnessBasePrompt": True,
            }
        ],
        "taskEnvelope": {
            "schemaId": "synthetic.task/1",
            "path": "contracts/task-v9.json",
            "sha256": sha256(artifacts["contracts/task-v9.json"]),
        },
        "oneFieldFraming": {
            "id": "synthetic.frame/1",
            "path": "contracts/framing-v9.txt",
            "sha256": sha256(artifacts["contracts/framing-v9.txt"]),
        },
        "terminalEnvelope": {
            "schemaId": "synthetic.terminal/1",
            "path": "contracts/terminal-v9.json",
            "sha256": sha256(artifacts["contracts/terminal-v9.json"]),
        },
        "minimumTransportRequirements": {
            "nonInteractive": "required",
            "structuredOutput": "required",
            "ambientIsolation": "required",
            "terminalEnvelope": "required",
            "boundedStreaming": "required",
        },
    }
    (root / "manifest.json").write_bytes(canonical_json(manifest))
    return manifest


class ImageBundleTest(unittest.TestCase):
    def test_three_payload_bundle_is_deterministic_and_exact(self) -> None:
        bundle = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "image"
            root.mkdir()
            manifest = write_image(root)
            first = bundle.build_bundle(root)
            second = bundle.build_bundle(root)
            parsed = bundle.parse_bundle(first)
        self.assertEqual(first, second)
        self.assertEqual(parsed.manifest, manifest)
        self.assertEqual(
            [entry.path for entry in parsed.files],
            [
                "payload/alpha.md",
                "payload/nested/beta.md",
                "payload/omega.txt",
                "contracts/task-v9.json",
                "contracts/framing-v9.txt",
                "contracts/terminal-v9.json",
            ],
        )
        self.assertEqual(parsed.files[1].bytes, "beta snowman \u2603\n".encode())

    def test_release_gate_rejects_sentinel(self) -> None:
        bundle = load_module()
        sentinel = HERE.parent / "sentinel-v1"
        with self.assertRaisesRegex(bundle.BundleError, "release eligible"):
            bundle.build_bundle(sentinel, require_release_eligible=True)

    def test_release_gate_accepts_explicit_functional_alpha_placeholder(self) -> None:
        bundle = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "image"
            root.mkdir()
            manifest = write_image(root, release_eligible=True)
            manifest["purpose"] = "functional-alpha-placeholder"
            (root / "manifest.json").write_bytes(canonical_json(manifest))
            parsed = bundle.validate_bundle(
                bundle.build_bundle(root, require_release_eligible=True),
                require_release_eligible=True,
            )
        self.assertEqual("functional-alpha-placeholder", parsed.manifest["purpose"])

    def test_check_detects_bundle_drift_and_binary_tamper(self) -> None:
        bundle = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "image"
            root.mkdir()
            write_image(root)
            expected = bundle.build_bundle(root)
            bundle.validate_bundle(expected)
            with self.assertRaisesRegex(bundle.BundleError, "trailing"):
                bundle.validate_bundle(expected + b"x")
            with self.assertRaisesRegex(bundle.BundleError, "drift"):
                bundle.check_bundle(root, expected[:-1] + bytes([expected[-1] ^ 1]))

    def test_directory_rejects_unlisted_missing_symlink_and_unsafe_paths(self) -> None:
        bundle = load_module()
        cases = ("unlisted", "missing", "traversal", "duplicate", "oversize")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw:
                root = Path(raw) / "image"
                root.mkdir()
                manifest = write_image(root)
                if case == "unlisted":
                    (root / "extra.txt").write_text("extra\n")
                elif case == "missing":
                    (root / "payload/alpha.md").unlink()
                elif case == "traversal":
                    manifest["payload"][0]["path"] = "payload/../escape.md"
                    (root / "manifest.json").write_bytes(canonical_json(manifest))
                elif case == "duplicate":
                    manifest["payload"][1]["path"] = manifest["payload"][0]["path"]
                    (root / "manifest.json").write_bytes(canonical_json(manifest))
                elif case == "oversize":
                    manifest["payload"][0]["byteLength"] = 16 * 1024 * 1024 + 1
                    (root / "manifest.json").write_bytes(canonical_json(manifest))
                with self.assertRaises(bundle.BundleError):
                    bundle.build_bundle(root)

        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw) / "image"
                root.mkdir()
                write_image(root)
                target = root / "payload/alpha.md"
                target.unlink()
                target.symlink_to(root / "payload/omega.txt")
                with self.assertRaisesRegex(bundle.BundleError, "symlink"):
                    bundle.build_bundle(root)

    def test_normalization_and_duplicate_json_keys_fail_closed(self) -> None:
        bundle = load_module()
        for invalid in (b"nul\0text\n", b"\xef\xbb\xbftext\n", b"crlf\r\n"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as raw:
                root = Path(raw) / "image"
                root.mkdir()
                manifest = write_image(root)
                path = root / "payload/alpha.md"
                path.write_bytes(invalid)
                descriptor = manifest["payload"][0]
                descriptor["byteLength"] = len(invalid)
                descriptor["sha256"] = sha256(invalid)
                # Aggregate/model-visible values are deliberately stale: every
                # malformed input must fail before it can become a bundle.
                (root / "manifest.json").write_bytes(canonical_json(manifest))
                with self.assertRaises(bundle.BundleError):
                    bundle.build_bundle(root)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "image"
            root.mkdir()
            write_image(root)
            original = (root / "manifest.json").read_bytes()
            duplicate_key = (
                b'{\n  "schema": "openprose.skill-runtime-image-manifest/1",'
                b'\n  "schema":'
            )
            duplicate = original.replace(
                b'{\n  "schema":',
                duplicate_key,
                1,
            )
            (root / "manifest.json").write_bytes(duplicate)
            with self.assertRaisesRegex(bundle.BundleError, "duplicate JSON key"):
                bundle.build_bundle(root)

    def test_manifest_authority_rejects_non_v1_path_media_length_and_placement(
        self,
    ) -> None:
        bundle = load_module()
        mutations = {
            "unicode-path": lambda manifest: manifest["payload"][0].update(
                path="payload/snowman-☃.md"
            ),
            "media-type": lambda manifest: manifest["payload"][0].update(
                mediaType="text/plain; charset=utf-8"
            ),
            "zero-length": lambda manifest: manifest["payload"][0].update(byteLength=0),
            "placement-id": lambda manifest: manifest["instructionPlacements"][
                0
            ].update(id="Developer Prompt"),
        }
        for case, mutate in mutations.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw:
                root = Path(raw) / "image"
                root.mkdir()
                manifest = write_image(root)
                mutate(manifest)
                (root / "manifest.json").write_bytes(canonical_json(manifest))
                with self.assertRaises(bundle.BundleError):
                    bundle.build_bundle(root)

    def test_bounded_reader_rejects_symlinks_and_growth_before_reading(self) -> None:
        bundle = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            oversized = root / "oversized.bundle"
            with oversized.open("wb") as stream:
                stream.truncate(66)
            with self.assertRaisesRegex(bundle.BundleError, "65-byte limit"):
                bundle.read_bounded(oversized, 65)
            if hasattr(os, "symlink"):
                linked = root / "linked.bundle"
                linked.symlink_to(oversized)
                with self.assertRaisesRegex(bundle.BundleError, "symlink"):
                    bundle.read_bounded(linked, 65)

    def test_manifest_copy_is_not_mutated_by_fixture_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = write_image(root)
            clone = deepcopy(manifest)
            self.assertEqual(manifest, clone)


if __name__ == "__main__":
    unittest.main(verbosity=2)
