from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import jsonschema


HERE = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load("openprose_live_alpha_test_runner", HERE / "run.py")
MATRIX = load("openprose_live_alpha_test_matrix", HERE / "matrix.py")


VERSIONS = {
    "prime": "prime-agent 0.7.0",
    "omp": "omp/18.0.9",
    "codex": "codex-cli 0.149.0-alpha.4.1",
    "claude": "2.1.243 (Claude Code)",
}


def sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def custody(harness: str) -> dict:
    value = {
        "schema": "openprose.harness-byte-custody/1",
        "harness": harness,
        "commandName": RUNNER.HARNESS_COMMANDS[harness],
        "route": {"kind": "direct", "linkCount": 0},
        "distributionKind": "native",
        "launcherKind": "native",
        "entrypoint": {"byteLength": 1000, "sha256": sha(f"harness:{harness}")},
        "runtime": None,
        "packages": [],
        "dependencyGraph": None,
        "fileCount": 1,
        "totalByteLength": 1000,
        "externalAuthorities": [
            "dynamic-undeclared-imports",
            "native-os-loader-libraries",
            "interpreter-resource-files",
            "ambient-harness-config-plugins-skills",
            "cached-account-provider-state",
            "provider-side-model-routing",
        ],
    }
    value["aggregateSha256"] = hashlib.sha256(MATRIX.canonical_json(value)).hexdigest()
    return value


def invocation(harness: str) -> dict:
    profiles = {
        "prime": "prime-harness-login",
        "omp": "omp-harness-login",
        "codex": "cached-chatgpt-login",
        "claude": "claude-subscription",
    }
    return {
        "model": f"fixture/{harness}",
        "authRoute": {"profile": profiles[harness], "category": "harness-login"},
    }


def evidence(surface: str, harness: str) -> dict:
    runner_name = "rust" if surface == "rust" else "bun"
    adapter = RUNNER.ADAPTERS[harness]
    transport = RUNNER.TRANSPORTS[harness]
    admission = RUNNER.validate_harness_version(harness, VERSIONS[harness])
    recipe_digest = admission.recipe_digest
    recipe, _ = RUNNER.adapter_recipe(harness)
    prompt_placement = recipe["launch"]["instructionPlacement"]["manifestPlacementId"]
    isolation = recipe["isolation"]["guarantee"]
    image_contract = RUNNER.echo_image_contract()
    image = deepcopy(image_contract["image"])
    platform_id = "darwin-arm64"
    target_node = {
        "commandPath": "/private/staging/node/bin/node",
        "path": "/private/staging/node/bin/node",
        "byteLength": 606,
        "sha256": sha("candidate:node"),
    }
    if surface == "npm":
        native = {
            "role": "native-executable",
            "path": (
                f"/private/staging/npm/@openprose/" f"prose-cli-{platform_id}/bin/prose"
            ),
            "byteLength": 202,
            "sha256": sha("candidate:bun"),
        }
        candidate = {
            "schema": "openprose.candidate-closure/1",
            "kind": "npm",
            "members": [
                {
                    "role": "launcher",
                    "path": "/private/staging/npm/@openprose/prose-cli/bin/prose.js",
                    "byteLength": 303,
                    "sha256": sha("candidate:npm-launcher"),
                },
                {
                    "role": "meta-package-json",
                    "path": "/private/staging/npm/@openprose/prose-cli/package.json",
                    "byteLength": 404,
                    "sha256": sha("candidate:npm-meta"),
                },
                {
                    "role": "platform-package-json",
                    "path": (
                        f"/private/staging/npm/@openprose/"
                        f"prose-cli-{platform_id}/package.json"
                    ),
                    "byteLength": 505,
                    "sha256": sha("candidate:npm-platform"),
                },
                native,
            ],
            "npm": {
                "platformId": platform_id,
                "metaPackage": "@openprose/prose-cli",
                "metaVersion": "0.1.0-alpha.1",
                "platformPackage": f"@openprose/prose-cli-{platform_id}",
                "platformVersion": "0.1.0-alpha.1",
                "binaryRelativePath": "bin/prose",
                "declaredBinaryByteLength": native["byteLength"],
                "declaredBinarySha256": native["sha256"],
                "node": {
                    **target_node,
                    "platform": "darwin",
                    "architecture": "arm64",
                    "libc": None,
                    "platformId": platform_id,
                },
            },
        }
    else:
        direct_member = {
            "role": "executable",
            "path": f"/private/staging/{surface}/prose",
            "byteLength": {"rust": 101, "bun": 202}[surface],
            "sha256": sha(f"candidate:{surface}"),
        }
        candidate = {
            "schema": "openprose.candidate-closure/1",
            "kind": "direct",
            "members": [direct_member],
        }
    return {
        "schema": "openprose.functional-alpha-live-evidence/5",
        "status": "pass",
        "target": {
            "authority": "exact-node-runtime-probe",
            "platformId": platform_id,
            "platform": "darwin",
            "architecture": "arm64",
            "libc": None,
            "node": target_node,
        },
        "surface": surface,
        "candidate": candidate,
        "runner": {
            "name": runner_name,
            "version": "0.1.0-alpha.1",
            "commit": "0123456789abcdef0123456789abcdef01234567",
        },
        "harness": harness,
        "harnessCustody": custody(harness),
        "invocation": invocation(harness),
        "program": {
            "path": "/private/staging/hello.prose.md",
            "byteLength": 42,
            "sha256": sha("program"),
        },
        "selection": {
            "exitCode": 0,
            "schema": "openprose.harness-selection/1",
            "scope": "user",
            "changed": True,
        },
        "configuration": deepcopy(MATRIX.CONFIGURATION),
        "doctor": {
            "exitCode": 0,
            "schema": "openprose.doctor-report/1",
            "ready": True,
            "selectedHarness": harness,
            "selectedHarnessVersion": VERSIONS[harness],
            "selectedTransport": transport,
            "selectedAdapterId": adapter,
            "promptPlacement": prompt_placement,
            "isolation": isolation,
            "authCategory": "harness-managed",
            "billingOwner": "user-provider",
            "image": {
                **image,
                "releaseEligible": image_contract["releaseEligible"],
            },
        },
        "run": {
            "exitCode": 0,
            "schema": "openprose.runner-result/1",
            "adapterId": adapter,
            "adapterDescriptorSha256": recipe_digest,
            "harnessVersion": VERSIONS[harness],
            "admittedVersions": list(admission.admitted_versions),
            "repairCommand": admission.repair_command,
            "transport": transport,
            "promptPlacement": prompt_placement,
            "isolation": isolation,
            "image": image,
            "deliveredImageSha256": image_contract["deliveredImageSha256"],
            "taskSha256": sha("task"),
            "cwdIdentitySha256": sha(f"cwd:{surface}:{harness}"),
            "terminal": deepcopy(MATRIX.TERMINAL),
            "semantic": {
                "status": "not-applicable",
                "terminalSchemaSha256": image_contract["terminalSchemaSha256"],
                "terminalEnvelopeDigestSha256": sha("terminal-envelope"),
            },
            "billing": {
                "owner": "user-provider",
                "authCategory": "harness-managed",
            },
            "durationMs": 100,
        },
        "claims": deepcopy(MATRIX.CLAIMS),
    }


def complete_records() -> list[tuple[dict, str]]:
    return [
        (evidence(surface, harness), sha(f"evidence:{surface}:{harness}"))
        for surface in MATRIX.SURFACES
        for harness in MATRIX.HARNESS_ORDER
    ]


class FunctionalAlphaMatrixTests(unittest.TestCase):
    def test_complete_matrix_binds_cross_surface_equivalence_without_paths(
        self,
    ) -> None:
        report = MATRIX.build_report(complete_records(), MATRIX.SURFACES)
        schema = json.loads((HERE / "matrix.schema.json").read_text("utf-8"))
        jsonschema.Draft202012Validator(schema).validate(report)
        self.assertEqual("pass", report["status"])
        self.assertEqual(12, report["cellCount"])
        self.assertEqual("darwin-arm64", report["target"]["platformId"])
        self.assertEqual(
            "exact-node-runtime-probe-reauthenticated",
            report["claims"]["targetBinding"],
        )
        self.assertEqual(sha("task"), report["common"]["taskSha256"])
        self.assertEqual(
            sha("terminal-envelope"),
            report["common"]["terminalEnvelopeDigestSha256"],
        )
        self.assertEqual(
            "candidate-closure-digest-and-settlement",
            report["claims"]["crossSurfaceEquality"],
        )
        self.assertNotIn("/private/staging", json.dumps(report))
        npm_surface = next(
            item for item in report["surfaces"] if item["surface"] == "npm"
        )
        self.assertEqual(
            {
                "byteLength",
                "sha256",
                "platform",
                "architecture",
                "libc",
                "platformId",
            },
            set(npm_surface["candidateClosure"]["npm"]["node"]),
        )
        self.assertEqual(
            ["bun", "bun", "rust"],
            sorted(item["runner"]["name"] for item in report["surfaces"]),
        )
        for cell in report["cells"]:
            self.assertTrue(cell["admittedVersions"])
            self.assertTrue(cell["repairCommand"])
        self.assertEqual(
            "exact-recipe-list-revalidated",
            report["claims"]["strictVersionAdmission"],
        )

    def test_target_specific_matrix_requires_only_its_declared_product(self) -> None:
        records = []
        for harness in ("codex", "omp"):
            value = evidence("rust", harness)
            value["target"].update(
                {
                    "platformId": "linux-x64-gnu",
                    "platform": "linux",
                    "architecture": "x64",
                    "libc": "gnu",
                }
            )
            records.append((value, sha(f"linux-rust:{harness}")))
        report = MATRIX.build_report(
            records, ("rust",), target_platform="linux-x64-gnu"
        )
        schema = json.loads((HERE / "matrix.schema.json").read_text("utf-8"))
        jsonschema.Draft202012Validator(schema).validate(report)
        self.assertEqual(["codex", "omp"], report["requiredHarnesses"])
        self.assertEqual(["rust"], report["requiredSurfaces"])
        self.assertEqual(2, report["cellCount"])
        self.assertEqual(
            "exact-declared-target-surface-harness-product",
            report["claims"]["coverage"],
        )

    def test_matrix_rejects_cross_target_pooling_and_unsupported_subsets(self) -> None:
        records = complete_records()
        records[4][0]["target"].update(
            {
                "platformId": "darwin-x64",
                "architecture": "x64",
            }
        )
        with self.assertRaisesRegex(MATRIX.MatrixError, "evidence target differs"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        codex = [(evidence("rust", "codex"), sha("darwin-x64-rust-codex"))]
        codex[0][0]["target"].update(
            {"platformId": "darwin-x64", "architecture": "x64"}
        )
        with self.assertRaisesRegex(MATRIX.MatrixError, "target-supported set"):
            MATRIX.build_report(
                codex,
                ("rust",),
                target_platform="darwin-x64",
                required_harnesses=("prime",),
            )

    def test_matrix_rejects_forged_target_probe_and_npm_target_drift(self) -> None:
        records = complete_records()
        records[0][0]["target"]["platformId"] = "darwin-x64"
        with self.assertRaisesRegex(MATRIX.MatrixError, "target identity"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        npm = next(value for value, _ in records if value["surface"] == "npm")
        npm["target"]["node"]["sha256"] = sha("different-target-node")
        with self.assertRaisesRegex(
            MATRIX.MatrixError, "npm candidate and target execution identities differ"
        ):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_schemas_reject_internally_inconsistent_targets_and_harnesses(self) -> None:
        evidence_schema = json.loads((HERE / "evidence.schema.json").read_text("utf-8"))
        forged_evidence = evidence("rust", "codex")
        forged_evidence["target"]["architecture"] = "x64"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(evidence_schema).validate(forged_evidence)

        report = MATRIX.build_report(complete_records(), MATRIX.SURFACES)
        matrix_schema = json.loads((HERE / "matrix.schema.json").read_text("utf-8"))
        report["target"].update({"platformId": "darwin-x64", "architecture": "x64"})
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(matrix_schema).validate(report)

    def test_matrix_rejects_missing_and_duplicate_cells(self) -> None:
        records = complete_records()
        with self.assertRaisesRegex(MATRIX.MatrixError, "matrix is incomplete"):
            MATRIX.build_report(records[:-1], MATRIX.SURFACES)
        with self.assertRaisesRegex(MATRIX.MatrixError, "duplicate matrix cell"):
            MATRIX.build_report([*records, records[0]], MATRIX.SURFACES)

    def test_matrix_rejects_cross_cell_task_or_terminal_drift(self) -> None:
        for path, replacement in (
            (("run", "taskSha256"), sha("different-task")),
            (
                ("run", "semantic", "terminalEnvelopeDigestSha256"),
                sha("different-terminal"),
            ),
        ):
            with self.subTest(path=path):
                records = complete_records()
                target = records[-1][0]
                cursor = target
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = replacement
                with self.assertRaisesRegex(MATRIX.MatrixError, "cross-cell"):
                    MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        records[-1][0]["run"]["terminal"]["terminalEventObserved"] = False
        with self.assertRaisesRegex(MATRIX.MatrixError, "terminal settlement"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_candidate_drift_within_a_surface(self) -> None:
        records = complete_records()
        records[1][0]["candidate"]["members"][0]["sha256"] = sha("different-candidate")
        with self.assertRaisesRegex(MATRIX.MatrixError, "rust candidate closure"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_harness_custody_forgery_and_cross_surface_drift(
        self,
    ) -> None:
        records = complete_records()
        records[0][0]["harnessCustody"]["entrypoint"]["sha256"] = sha(
            "forged-entrypoint"
        )
        with self.assertRaisesRegex(MATRIX.MatrixError, "aggregateSha256"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        changed = records[4][0]["harnessCustody"]
        changed["entrypoint"]["sha256"] = sha("different-prime-install")
        changed["aggregateSha256"] = hashlib.sha256(
            MATRIX.canonical_json(
                {
                    key: value
                    for key, value in changed.items()
                    if key != "aggregateSha256"
                }
            )
        ).hexdigest()
        with self.assertRaisesRegex(MATRIX.MatrixError, "prime byte custody"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_model_or_auth_route_drift_across_surfaces(self) -> None:
        records = complete_records()
        records[4][0]["invocation"]["model"] = "fixture/different-prime"
        with self.assertRaisesRegex(MATRIX.MatrixError, "invocation identity"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        records[0][0]["invocation"]["authRoute"] = {
            "profile": "openrouter",
            "category": "provider-api-key",
        }
        with self.assertRaisesRegex(MATRIX.MatrixError, "invocation identity"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_missing_duplicate_or_reordered_closure_members(
        self,
    ) -> None:
        for mutation in ("missing", "duplicate", "duplicate-path", "reordered"):
            with self.subTest(mutation=mutation):
                records = complete_records()
                members = records[-1][0]["candidate"]["members"]
                if mutation == "missing":
                    members.pop()
                elif mutation == "duplicate":
                    members[2] = deepcopy(members[1])
                elif mutation == "duplicate-path":
                    members[3]["path"] = members[0]["path"]
                else:
                    members[0], members[1] = members[1], members[0]
                with self.assertRaisesRegex(
                    MATRIX.MatrixError, r"candidate closure.*members"
                ):
                    MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_npm_native_drift_from_direct_bun(self) -> None:
        records = complete_records()
        for value, _ in records:
            if value["surface"] == "npm":
                value["candidate"]["members"][3]["sha256"] = sha("other-native")
                value["candidate"]["npm"]["declaredBinarySha256"] = sha("other-native")
        with self.assertRaisesRegex(MATRIX.MatrixError, "npm native.*Bun direct"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_npm_node_missing_mismatch_duplicate_or_drift(self) -> None:
        records = complete_records()
        del records[-1][0]["candidate"]["npm"]["node"]
        with self.assertRaisesRegex(MATRIX.MatrixError, "unsupported shape"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        records[-1][0]["candidate"]["npm"]["node"]["architecture"] = "x64"
        with self.assertRaisesRegex(
            MATRIX.MatrixError, "Node identity is inconsistent"
        ):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        npm = records[-1][0]["candidate"]
        npm["npm"]["node"]["path"] = npm["members"][0]["path"]
        with self.assertRaisesRegex(
            MATRIX.MatrixError, "duplicates a candidate member"
        ):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        records[-1][0]["candidate"]["npm"]["node"]["sha256"] = sha("different-node")
        with self.assertRaisesRegex(
            MATRIX.MatrixError, "npm candidate and target execution identities differ"
        ):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_independently_revalidates_frozen_version_and_recipe(self) -> None:
        records = complete_records()
        value = records[2][0]
        value["doctor"]["selectedHarnessVersion"] = "codex-cli 0.150.0"
        value["run"]["harnessVersion"] = "codex-cli 0.150.0"
        with self.assertRaisesRegex(MATRIX.MatrixError, "outside frozen support"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        records[0][0]["run"]["adapterDescriptorSha256"] = sha("forged-recipe")
        with self.assertRaisesRegex(MATRIX.MatrixError, "frozen adapter recipe"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_exact_admission_and_repair_drift(self) -> None:
        for field, replacement in (
            ("admittedVersions", ["0.7.0", "0.8.2"]),
            ("repairCommand", "credential-canary-repair-command"),
        ):
            with self.subTest(field=field):
                records = complete_records()
                records[0][0]["run"][field] = replacement
                with self.assertRaisesRegex(
                    MATRIX.MatrixError, "does not bind the frozen adapter recipe"
                ):
                    MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_cross_surface_exact_admission_drift(self) -> None:
        records = complete_records()
        for value, _ in records:
            if value["harness"] == "prime":
                value["run"]["admittedVersions"] = ["0.7.0", "0.8.1"]
        records[4][0]["run"]["admittedVersions"] = ["0.7.0"]
        with self.assertRaisesRegex(
            MATRIX.MatrixError, "does not bind the frozen adapter recipe"
        ):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_matrix_rejects_runner_build_and_isolation_drift(self) -> None:
        records = complete_records()
        records[-1][0]["runner"]["commit"] = "different-commit"
        with self.assertRaisesRegex(MATRIX.MatrixError, "runner build identity"):
            MATRIX.build_report(records, MATRIX.SURFACES)

        records = complete_records()
        records[-1][0]["configuration"]["selectionPathContained"] = False
        with self.assertRaisesRegex(MATRIX.MatrixError, "configuration isolation"):
            MATRIX.build_report(records, MATRIX.SURFACES)

    def test_reader_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "evidence.json"
            path.write_text('{"schema":"one","schema":"two"}\n', "utf-8")
            with self.assertRaisesRegex(MATRIX.MatrixError, "duplicate JSON key"):
                MATRIX.read_evidence(path)


if __name__ == "__main__":
    unittest.main()
