from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from jsonschema import Draft202012Validator

import promote_alpha_release as promote


VERSION = "0.15.0-alpha.1"
SOURCE_SHA = "1" * 40
REPOSITORY = "openprose/prose"
RELEASE_ID = 187
WORKFLOW_RUN_ID = 9_876_543_210
WORKFLOW_RUN_ATTEMPT = 2
DRAFT_WORKFLOW_RUN_ID = 1_234_567_890
DRAFT_WORKFLOW_RUN_ATTEMPT = 3
DRAFT_AUTHORITY_SHA256 = "a" * 64
OWNERS = ("jose_at_prose", "dan_openprose")
FAKE_TOOLCHAIN_IDENTITY = {
    "npmVersion": "11.15.0",
    "npmTarballUrl": promote.NPM_TARBALL_URL,
    "npmTarballByteLength": promote.PINNED_NPM_AUTHORITY.byte_length,
    "npmTarballSha1": promote.PINNED_NPM_AUTHORITY.sha1,
    "npmTarballSha256": promote.PINNED_NPM_AUTHORITY.sha256,
    "npmTarballSha512": promote.PINNED_NPM_AUTHORITY.sha512,
    "npmTarballIntegrity": promote.PINNED_NPM_AUTHORITY.integrity,
    "npmTreeSha256": promote.PINNED_NPM_AUTHORITY.tree_sha256,
    "npmEntrySha256": promote.PINNED_NPM_AUTHORITY.entry_sha256,
    "nodeVersion": "24.20.0",
    "nodeExecutableSha256": "6" * 64,
}
FAKE_GH_TOOL_IDENTITY = {
    "authority": promote.GH_TOOL_AUTHORITY,
    "version": "2.74.1",
    "executableByteLength": 123456,
    "executableSha256": "7" * 64,
}


def confirmation(operation: str) -> str:
    return (
        f"PROMOTE {operation} {VERSION} {SOURCE_SHA} {RELEASE_ID} AUTHORITY "
        f"{DRAFT_WORKFLOW_RUN_ID}/{DRAFT_WORKFLOW_RUN_ATTEMPT} "
        f"{DRAFT_AUTHORITY_SHA256}"
    )


def package_bytes(name: str, role: str) -> bytes:
    cohort = {
        "schema": "openprose.npm-cohort/1",
        "version": VERSION,
        "sourceRevision": SOURCE_SHA,
        "releaseChannel": "functional-alpha",
        "purpose": "provisional-echo-runtime",
        "image": {"version": "echo-v0"},
        "admittedPlatforms": sorted(promote.PLATFORMS),
        "semanticStatus": "not-applicable",
        "releaseEligible": False,
        "publicationAuthorized": False,
    }
    manifest: dict[str, object] = {
        "name": name,
        "version": VERSION,
        "license": "MIT",
        "publishConfig": {"access": "public"},
        "openproseCohort": cohort,
    }
    if role == "meta":
        manifest["optionalDependencies"] = {
            package: VERSION for package in promote.PLATFORM_PACKAGES
        }
    else:
        manifest["openprosePlatform"] = name.removeprefix("@openprose/prose-cli-")
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        member = tarfile.TarInfo("package/package.json")
        member.size = len(encoded)
        member.mode = 0o644
        member.mtime = 0
        archive.addfile(member, io.BytesIO(encoded))
    return output.getvalue()


def artifacts(root: Path) -> tuple[promote.PackageArtifact, ...]:
    result: list[promote.PackageArtifact] = []
    for name in promote.PACKAGE_ORDER:
        role = "meta" if name == promote.META_PACKAGE else "platform"
        body = package_bytes(name, role)
        platform = name.removeprefix("@openprose/prose-cli-")
        artifact = (
            f"openprose-prose-cli-{VERSION}.tgz"
            if role == "meta"
            else f"openprose-prose-cli-{platform}-{VERSION}.tgz"
        )
        path = root / artifact
        path.write_bytes(body)
        result.append(
            promote.PackageArtifact(
                name=name,
                role=role,
                artifact=artifact,
                path=path,
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                shasum=hashlib.sha1(body).hexdigest(),
                integrity="sha512-"
                + base64.b64encode(hashlib.sha512(body).digest()).decode(),
            )
        )
    return tuple(result)


def candidate(root: Path) -> promote.Candidate:
    packages = artifacts(root)
    package_sha256 = {package.artifact: package.sha256 for package in packages}
    assets = tuple(
        promote.ReleaseAsset(
            name=name,
            byte_length=len(name.encode("ascii")),
            sha256=package_sha256.get(
                name, hashlib.sha256(name.encode("ascii")).hexdigest()
            ),
        )
        for name in promote.expected_asset_names(VERSION)
    )
    return promote.Candidate(
        repository=REPOSITORY,
        release_id=RELEASE_ID,
        version=VERSION,
        source_sha=SOURCE_SHA,
        tag=f"cli-v{VERSION}",
        assembly_sha256=next(
            asset.sha256 for asset in assets if asset.name == "SHA256SUMS"
        ),
        assets=assets,
        packages=packages,
    )


def draft_authority(value: promote.Candidate, body: bytes) -> dict[str, object]:
    assets = [
        {
            "name": asset.name,
            "sha256": asset.sha256,
            "byteLength": asset.byte_length,
        }
        for asset in value.assets
    ]
    return {
        "schema": "openprose.alpha-draft-authority/1",
        "repository": value.repository,
        "version": value.version,
        "sourceSha": value.source_sha,
        "controlSha": value.source_sha,
        "tag": value.tag,
        "releaseId": value.release_id,
        "draft": True,
        "prerelease": True,
        "publicationAuthorized": False,
        "releaseBody": {
            "byteLength": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        },
        "assetCount": len(assets),
        "assets": assets,
        "assetInventorySha256": hashlib.sha256(
            json.dumps(assets, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "workflowRun": {
            "path": ".github/workflows/openprose-cli-alpha-release.yml",
            "id": DRAFT_WORKFLOW_RUN_ID,
            "attempt": DRAFT_WORKFLOW_RUN_ATTEMPT,
        },
        "outcome": "created",
    }


def write_draft_authority(
    root: Path, value: promote.Candidate, body: bytes
) -> tuple[Path, str]:
    authority = draft_authority(value, body)
    encoded = (
        json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    path = root / "alpha-draft-authority.json"
    path.write_bytes(encoded)
    return path, hashlib.sha256(encoded).hexdigest()


def promotion_draft_identity(
    value: promote.Candidate, body: bytes = b"# Retained release body\n"
) -> dict[str, object]:
    authority = draft_authority(value, body)
    encoded = (
        json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    workflow = authority["workflowRun"]
    assert isinstance(workflow, dict)
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "artifact": (
            "openprose-cli-alpha-draft-authority-run-"
            f"{DRAFT_WORKFLOW_RUN_ID}-attempt-{DRAFT_WORKFLOW_RUN_ATTEMPT}"
        ),
        "controlSha": SOURCE_SHA,
        "releaseBody": authority["releaseBody"],
        "assetInventorySha256": authority["assetInventorySha256"],
        "workflowRun": workflow,
        "outcome": authority["outcome"],
    }


def old_version(name: str) -> promote.RegistryVersion:
    return promote.RegistryVersion(
        name=name,
        version="0.14.0",
        shasum="0" * 40,
        integrity="sha512-" + base64.b64encode(b"old").decode(),
        tarball_url=f"https://registry.npmjs.org/{name}/-/old.tgz",
        optional_dependencies=None,
        provenance_predicate_type=None,
    )


class FakeRegistry:
    def __init__(self, *, existing_platforms: bool = False) -> None:
        self.states: dict[str, promote.RegistryPackage] = {
            promote.META_PACKAGE: promote.RegistryPackage(
                name=promote.META_PACKAGE,
                owners=OWNERS,
                tags={"latest": "0.14.0"},
                versions={"0.14.0": old_version(promote.META_PACKAGE)},
            )
        }
        if existing_platforms:
            for name in promote.PLATFORM_PACKAGES:
                self.states[name] = promote.RegistryPackage(
                    name=name,
                    owners=OWNERS,
                    tags={"alpha": "0.14.0"},
                    versions={"0.14.0": old_version(name)},
                )
        self.bodies: dict[str, bytes] = {}

    def package(self, name: str) -> promote.RegistryPackage | None:
        return copy.deepcopy(self.states.get(name))

    def tarball(self, url: str) -> bytes:
        if url not in self.bodies:
            raise promote.PromotionError("fixture registry tarball is absent")
        return self.bodies[url]

    def publish(
        self,
        package: promote.PackageArtifact,
        *,
        corrupt_integrity: bool = False,
        omit_provenance: bool = False,
    ) -> None:
        state = self.states.get(package.name)
        versions = {} if state is None else dict(state.versions)
        tags = {} if state is None else dict(state.tags)
        url = "https://registry.npmjs.org/fixture/-/" + quote_artifact(package.artifact)
        versions[VERSION] = promote.RegistryVersion(
            name=package.name,
            version=VERSION,
            shasum=package.shasum,
            integrity=("sha512-" + base64.b64encode(b"wrong").decode())
            if corrupt_integrity
            else package.integrity,
            tarball_url=url,
            optional_dependencies=(
                {name: VERSION for name in promote.PLATFORM_PACKAGES}
                if package.role == "meta"
                else None
            ),
            provenance_predicate_type=(
                None if omit_provenance else promote.PROVENANCE_PREDICATE
            ),
        )
        tags["alpha"] = VERSION
        self.states[package.name] = promote.RegistryPackage(
            name=package.name, owners=OWNERS, tags=tags, versions=versions
        )
        self.bodies[url] = package.body


def quote_artifact(value: str) -> str:
    return value.replace(" ", "%20").replace("$", "%24")


class FakeNpm:
    def __init__(
        self,
        registry: FakeRegistry,
        *,
        fail_at: int | None = None,
        publish_on_failure: bool = False,
        corrupt_at: int | None = None,
        omit_provenance_at: int | None = None,
    ) -> None:
        self.registry = registry
        self.fail_at = fail_at
        self.publish_on_failure = publish_on_failure
        self.corrupt_at = corrupt_at
        self.omit_provenance_at = omit_provenance_at
        self.calls: list[tuple[str, str]] = []
        self.toolchain_checked = False

    def require_toolchain(self) -> dict[str, object]:
        self.toolchain_checked = True
        return dict(FAKE_TOOLCHAIN_IDENTITY)

    def mutate(self, operation: str, package: promote.PackageArtifact) -> int:
        self.calls.append((operation, package.name))
        index = len(self.calls)
        failed = index == self.fail_at
        if operation == "publish" and (not failed or self.publish_on_failure):
            self.registry.publish(
                package,
                corrupt_integrity=index == self.corrupt_at,
                omit_provenance=index == self.omit_provenance_at,
            )
        return 1 if failed else 0


class FakeGitHub:
    def __init__(self) -> None:
        self.promotions: list[promote.Candidate] = []

    def promote(self, value: promote.Candidate) -> None:
        self.promotions.append(value)


class FakeAttestations:
    def __init__(
        self, *, fail_at: int | None = None, fail_reauthenticate_at: int | None = None
    ) -> None:
        self.fail_at = fail_at
        self.fail_reauthenticate_at = fail_reauthenticate_at
        self.calls: list[str] = []
        self.reauthentication_calls = 0
        self.toolchain_checked = False

    def require_toolchain(self) -> dict[str, object]:
        self.toolchain_checked = True
        return dict(FAKE_GH_TOOL_IDENTITY)

    def verify(
        self, *, candidate: promote.Candidate, asset: promote.ReleaseAsset
    ) -> tuple[promote.AttestationResult, ...]:
        self.calls.append(asset.name)
        if len(self.calls) == self.fail_at:
            raise promote.PromotionError("fixture attestation did not verify")
        return (
            promote.AttestationResult(
                repository=candidate.repository,
                source_sha=candidate.source_sha,
                source_ref="refs/heads/main",
                predicate_type=promote.PROVENANCE_PREDICATE,
                signer_workflow=(
                    f"{candidate.repository}/{promote.SIGNER_WORKFLOW_PATH}"
                ),
                signer_digest=candidate.source_sha,
                subject_name=asset.name,
                subject_sha256=asset.sha256,
            ),
        )

    def reauthenticate(self, candidate: promote.Candidate) -> None:
        self.reauthentication_calls += 1
        if self.reauthentication_calls == self.fail_reauthenticate_at:
            raise promote.PromotionError("fixture attested bytes changed")


class TransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = candidate(self.root)
        authority_path, authority_sha256 = write_draft_authority(
            self.root,
            self.candidate,
            b"# Retained functional-alpha release body\n",
        )
        self.draft_authority = promote.load_draft_authority(
            authority_path,
            expected_sha256=authority_sha256,
            repository=REPOSITORY,
            release_id=RELEASE_ID,
            version=VERSION,
            source_sha=SOURCE_SHA,
            producer_run_id=DRAFT_WORKFLOW_RUN_ID,
            producer_run_attempt=DRAFT_WORKFLOW_RUN_ATTEMPT,
        )
        self.lineage = promote.Lineage(latest="0.14.0", owners=OWNERS)
        self.github = FakeGitHub()
        self.evidence = promote.preverify_attestations(
            candidate=self.candidate,
            draft_authority=self.draft_authority,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            attestations=FakeAttestations(),
            writer=promote.AttestationEvidenceWriter(
                self.root / "fixture" / "github-attestation-evidence.json"
            ),
        )
        self.evidence_sha256 = promote.attestation_evidence_digest(self.evidence)
        self.attestations = FakeAttestations()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def writer(self) -> promote.SettlementWriter:
        return promote.SettlementWriter(
            self.root / "evidence" / "npm-publication-settlement.json"
        )

    def test_read_only_preverification_writes_one_closed_final_record(self) -> None:
        verifier = FakeAttestations()
        path = self.root / "evidence" / "github-attestation-evidence.json"
        record = promote.preverify_attestations(
            candidate=self.candidate,
            draft_authority=self.draft_authority,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            attestations=verifier,
            writer=promote.AttestationEvidenceWriter(path),
        )
        self.assertEqual(promote.ATTESTATION_EVIDENCE_SCHEMA, record["schema"])
        self.assertEqual("verified", record["status"])
        self.assertEqual(38, record["verifiedAssetCount"])
        self.assertTrue(record["bytesReauthenticated"])
        self.assertEqual(
            self.draft_authority.sha256, record["draftAuthority"]["sha256"]
        )
        self.assertEqual(
            DRAFT_WORKFLOW_RUN_ID,
            record["draftAuthority"]["workflowRun"]["id"],
        )
        self.assertEqual(38, len(verifier.calls))
        self.assertEqual(1, verifier.reauthentication_calls)
        self.assertEqual(record, json.loads(path.read_text("utf-8")))
        Draft202012Validator(
            json.loads(
                Path(
                    "cli/release/alpha-promotion-attestation-evidence.schema.json"
                ).read_text("utf-8")
            )
        ).validate(record)
        mutations = []
        extra = copy.deepcopy(record)
        extra["privatePath"] = "/tmp/secret"
        mutations.append(extra)
        wrong_assembly = copy.deepcopy(record)
        wrong_assembly["assemblySha256"] = "f" * 64
        mutations.append(wrong_assembly)
        wrong_tool = copy.deepcopy(record)
        wrong_tool["tool"]["authority"] = "ambient-gh"
        mutations.append(wrong_tool)
        replay_shape = copy.deepcopy(record)
        replay_shape["workflowRunAttempt"] = False
        mutations.append(replay_shape)
        wrong_draft = copy.deepcopy(record)
        wrong_draft["draftAuthority"]["sha256"] = "4" * 64
        mutations.append(wrong_draft)
        wrong_draft_inventory = copy.deepcopy(record)
        wrong_draft_inventory["draftAuthority"]["assetInventorySha256"] = "5" * 64
        mutations.append(wrong_draft_inventory)
        reordered = copy.deepcopy(record)
        reordered["assets"][0], reordered["assets"][1] = (
            reordered["assets"][1],
            reordered["assets"][0],
        )
        mutations.append(reordered)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(
                promote.PromotionError
            ):
                promote.validate_attestation_evidence(
                    mutation, draft_authority=self.draft_authority
                )

    def test_preverification_is_nonoverwriting_and_never_leaves_partial_output(
        self,
    ) -> None:
        existing = self.root / "existing" / "github-attestation-evidence.json"
        existing.parent.mkdir()
        existing.write_bytes(b"owner evidence\n")
        verifier = FakeAttestations()
        with self.assertRaisesRegex(promote.PromotionError, "already exists"):
            promote.preverify_attestations(
                candidate=self.candidate,
                draft_authority=self.draft_authority,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                attestations=verifier,
                writer=promote.AttestationEvidenceWriter(existing),
            )
        self.assertFalse(verifier.toolchain_checked)
        self.assertEqual(b"owner evidence\n", existing.read_bytes())

        partial = self.root / "partial" / "github-attestation-evidence.json"
        with patch.object(promote, "_write_all", side_effect=OSError("short write")):
            with self.assertRaisesRegex(promote.PromotionError, "atomically"):
                promote.preverify_attestations(
                    candidate=self.candidate,
                    draft_authority=self.draft_authority,
                    workflow_run_id=WORKFLOW_RUN_ID,
                    workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                    attestations=FakeAttestations(),
                    writer=promote.AttestationEvidenceWriter(partial),
                )
        self.assertFalse(partial.exists())
        self.assertEqual([], list(partial.parent.glob(".*.tmp")))

    def test_evidence_rejects_tampering_replay_and_candidate_drift(self) -> None:
        path = self.root / "fixture" / "github-attestation-evidence.json"
        loaded = promote.load_attestation_evidence(
            path,
            expected_sha256=self.evidence_sha256,
            candidate=self.candidate,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            draft_authority=self.draft_authority,
        )
        self.assertEqual(self.evidence, loaded)

        tampered = copy.deepcopy(self.evidence)
        tampered["tool"]["executableSha256"] = "9" * 64
        tampered_path = self.root / "tampered.json"
        tampered_path.write_bytes(promote._encode_attestation_evidence(tampered))
        with self.assertRaisesRegex(promote.PromotionError, "digest does not match"):
            promote.load_attestation_evidence(
                tampered_path,
                expected_sha256=self.evidence_sha256,
                candidate=self.candidate,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                draft_authority=self.draft_authority,
            )

        with self.assertRaisesRegex(promote.PromotionError, "another workflow run"):
            promote.load_attestation_evidence(
                path,
                expected_sha256=self.evidence_sha256,
                candidate=self.candidate,
                workflow_run_id=WORKFLOW_RUN_ID + 1,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                draft_authority=self.draft_authority,
            )
        with self.assertRaisesRegex(promote.PromotionError, "another workflow attempt"):
            promote.load_attestation_evidence(
                path,
                expected_sha256=self.evidence_sha256,
                candidate=self.candidate,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT + 1,
                draft_authority=self.draft_authority,
            )

        first = self.candidate.assets[0]
        drifted = replace(
            self.candidate,
            assets=(replace(first, sha256="a" * 64), *self.candidate.assets[1:]),
        )
        with self.assertRaisesRegex(promote.PromotionError, "current candidate"):
            promote.load_attestation_evidence(
                path,
                expected_sha256=self.evidence_sha256,
                candidate=drifted,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                draft_authority=self.draft_authority,
            )

    def transition(
        self,
        operation: str,
        registry: FakeRegistry,
        npm: FakeNpm | None,
    ) -> dict[str, object]:
        return promote.execute_transition(
            operation=operation,
            candidate=self.candidate,
            lineage=self.lineage,
            registry=registry,
            npm=npm,
            github=self.github,
            attestations=self.attestations,
            attestation_evidence=self.evidence,
            attestation_evidence_sha256=self.evidence_sha256,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            writer=self.writer(),
        )

    def test_bootstrap_publishes_platforms_before_meta_and_never_promotes(self) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry)
        settlement = self.transition("bootstrap", registry, npm)
        self.assertTrue(npm.toolchain_checked)
        self.assertEqual(
            [("publish", name) for name in promote.PACKAGE_ORDER], npm.calls
        )
        self.assertEqual("npm-settled", settlement["status"])
        self.assertEqual(FAKE_TOOLCHAIN_IDENTITY, settlement["npmMutationToolchain"])
        attestation = settlement["githubArtifactAttestations"]
        self.assertEqual("verified", attestation["status"])
        self.assertEqual(38, attestation["verifiedAssetCount"])
        self.assertTrue(attestation["bytesReauthenticated"])
        self.assertEqual(FAKE_GH_TOOL_IDENTITY, attestation["tool"])
        self.assertEqual(WORKFLOW_RUN_ID, attestation["workflowRunId"])
        self.assertEqual(WORKFLOW_RUN_ATTEMPT, attestation["workflowRunAttempt"])
        self.assertEqual(self.evidence_sha256, attestation["evidenceSha256"])
        self.assertEqual(self.evidence["draftAuthority"], settlement["draftAuthority"])
        self.assertEqual(0, len(self.attestations.calls))
        self.assertEqual(5, self.attestations.reauthentication_calls)
        self.assertEqual([], self.github.promotions)
        self.assertEqual("0.14.0", registry.states[promote.META_PACKAGE].tags["latest"])
        self.assertEqual(
            ["published"] * 5,
            [item["outcome"] for item in settlement["packages"]],
        )
        self.assertEqual(
            [promote.PROVENANCE_PREDICATE] * 5,
            [item["provenancePredicateType"] for item in settlement["packages"]],
        )
        Draft202012Validator(
            json.loads(
                Path("cli/release/alpha-promotion-settlement.schema.json").read_text()
            )
        ).validate(settlement)

    def test_attestation_failure_writes_no_partial_evidence_and_mutates_nothing(
        self,
    ) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry)
        self.attestations = FakeAttestations(fail_at=3)
        evidence_path = self.root / "failed" / "github-attestation-evidence.json"
        with self.assertRaisesRegex(promote.PromotionError, "did not verify"):
            promote.preverify_attestations(
                candidate=self.candidate,
                draft_authority=self.draft_authority,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                attestations=self.attestations,
                writer=promote.AttestationEvidenceWriter(evidence_path),
            )
        self.assertEqual([], npm.calls)
        self.assertEqual([], self.github.promotions)
        self.assertFalse(evidence_path.exists())
        self.assertEqual(3, len(self.attestations.calls))

    def test_attested_bytes_are_reauthenticated_before_the_first_mutation(
        self,
    ) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry)
        self.attestations = FakeAttestations(fail_reauthenticate_at=1)
        with self.assertRaisesRegex(promote.PromotionError, "bytes changed"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual([], npm.calls)
        report = json.loads(self.writer().path.read_text())
        self.assertEqual("failed", report["status"])
        self.assertEqual("failed", report["githubArtifactAttestations"]["status"])
        self.assertFalse(report["githubArtifactAttestations"]["bytesReauthenticated"])

    def test_attested_byte_drift_after_publication_retains_valid_partial_state(
        self,
    ) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry)
        self.attestations = FakeAttestations(fail_reauthenticate_at=2)
        with self.assertRaisesRegex(promote.PromotionError, "bytes changed"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual([("publish", promote.PLATFORM_PACKAGES[0])], npm.calls)
        report = json.loads(self.writer().path.read_text())
        self.assertEqual("partial-publication", report["status"])
        self.assertEqual("failed", report["githubArtifactAttestations"]["status"])
        self.assertEqual("published", report["packages"][0]["outcome"])
        self.assertEqual("not-attempted", report["packages"][1]["outcome"])
        Draft202012Validator(
            json.loads(
                Path("cli/release/alpha-promotion-settlement.schema.json").read_text()
            )
        ).validate(report)

    def test_platform_failure_is_visible_and_meta_is_never_attempted(self) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry, fail_at=2)
        with self.assertRaisesRegex(promote.PromotionError, "expected npm package"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual(
            [
                ("publish", promote.PLATFORM_PACKAGES[0]),
                ("publish", promote.PLATFORM_PACKAGES[1]),
            ],
            npm.calls,
        )
        report = json.loads(self.writer().path.read_text())
        self.assertEqual("partial-publication", report["status"])
        self.assertEqual(FAKE_TOOLCHAIN_IDENTITY, report["npmMutationToolchain"])
        self.assertFalse(_entry(report, promote.META_PACKAGE)["attempted"])
        with self.assertRaisesRegex(promote.PromotionError, "prior mutation attempt"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual([], self.github.promotions)

    def test_lost_client_response_is_settled_only_by_exact_registry_bytes(self) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry, fail_at=1, publish_on_failure=True)
        settlement = self.transition("bootstrap", registry, npm)
        self.assertEqual("npm-settled", settlement["status"])
        self.assertEqual("published", settlement["packages"][0]["outcome"])

    def test_integrity_mismatch_burns_partial_bootstrap_and_stops(self) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry, corrupt_at=1)
        with self.assertRaisesRegex(promote.PromotionError, "integrity or provenance"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual(1, len(npm.calls))
        report = json.loads(self.writer().path.read_text())
        self.assertEqual("partial-publication", report["status"])
        self.assertFalse(_entry(report, promote.META_PACKAGE)["attempted"])

    def test_missing_registry_provenance_stops_before_meta_or_github(self) -> None:
        registry = FakeRegistry()
        npm = FakeNpm(registry, omit_provenance_at=1)
        with self.assertRaisesRegex(promote.PromotionError, "provenance"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual(1, len(npm.calls))
        report = json.loads(self.writer().path.read_text())
        self.assertIsNone(report["packages"][0]["provenancePredicateType"])
        self.assertFalse(_entry(report, promote.META_PACKAGE)["attempted"])
        self.assertEqual([], self.github.promotions)

    def test_registry_downloaded_byte_mismatch_blocks_promotion(self) -> None:
        registry = FakeRegistry(existing_platforms=True)
        for package in self.candidate.packages:
            registry.publish(package)
        first = self.candidate.packages[0]
        version = registry.states[first.name].versions[VERSION]
        registry.bodies[version.tarball_url] = b"different registry bytes"
        with self.assertRaisesRegex(promote.PromotionError, "downloaded npm"):
            self.transition("settle-and-promote", registry, None)
        self.assertEqual([], self.github.promotions)

    def test_preexisting_version_and_latest_drift_fail_before_npm(self) -> None:
        for mutation in ("version", "latest"):
            with self.subTest(mutation=mutation):
                root = self.root / mutation
                root.mkdir()
                writer = promote.SettlementWriter(
                    root / "npm-publication-settlement.json"
                )
                registry = FakeRegistry()
                if mutation == "version":
                    registry.publish(self.candidate.packages[-1])
                else:
                    meta = registry.states[promote.META_PACKAGE]
                    registry.states[promote.META_PACKAGE] = promote.RegistryPackage(
                        name=meta.name,
                        owners=meta.owners,
                        tags={"latest": "9.9.9"},
                        versions=meta.versions,
                    )
                npm = FakeNpm(registry)
                with self.assertRaises(promote.PromotionError):
                    promote.execute_transition(
                        operation="bootstrap",
                        candidate=self.candidate,
                        lineage=self.lineage,
                        registry=registry,
                        npm=npm,
                        github=self.github,
                        attestations=self.attestations,
                        attestation_evidence=self.evidence,
                        attestation_evidence_sha256=self.evidence_sha256,
                        workflow_run_id=WORKFLOW_RUN_ID,
                        workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                        writer=writer,
                    )
                self.assertEqual([], npm.calls)

    def test_bootstrap_refuses_a_preexisting_platform_namespace(self) -> None:
        registry = FakeRegistry(existing_platforms=True)
        npm = FakeNpm(registry)
        with self.assertRaisesRegex(promote.PromotionError, "unpublished"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual([], npm.calls)

    def test_ownership_drift_fails_before_any_mutation(self) -> None:
        registry = FakeRegistry()
        meta = registry.states[promote.META_PACKAGE]
        registry.states[promote.META_PACKAGE] = promote.RegistryPackage(
            name=meta.name,
            owners=("namespace-squatter",),
            tags=meta.tags,
            versions=meta.versions,
        )
        npm = FakeNpm(registry)
        with self.assertRaisesRegex(promote.PromotionError, "ownership"):
            self.transition("bootstrap", registry, npm)
        self.assertEqual([], npm.calls)

    def test_staged_transitions_require_existing_names_and_human_settlement(
        self,
    ) -> None:
        absent = FakeRegistry()
        absent_npm = FakeNpm(absent)
        with self.assertRaisesRegex(promote.PromotionError, "exist already"):
            self.transition("stage-platforms", absent, absent_npm)
        self.assertEqual([], absent_npm.calls)

        root = self.root / "staged"
        root.mkdir()
        registry = FakeRegistry(existing_platforms=True)
        npm = FakeNpm(registry)
        settlement = promote.execute_transition(
            operation="stage-platforms",
            candidate=self.candidate,
            lineage=self.lineage,
            registry=registry,
            npm=npm,
            github=self.github,
            attestations=self.attestations,
            attestation_evidence=self.evidence,
            attestation_evidence_sha256=self.evidence_sha256,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            writer=promote.SettlementWriter(root / "npm-publication-settlement.json"),
        )
        self.assertEqual("awaiting-platform-approval", settlement["status"])
        self.assertEqual(
            [("stage", name) for name in promote.PLATFORM_PACKAGES], npm.calls
        )
        self.assertNotIn(
            VERSION, registry.states[promote.PLATFORM_PACKAGES[0]].versions
        )

    def test_meta_stage_and_promotion_wait_for_public_platform_settlement(self) -> None:
        registry = FakeRegistry(existing_platforms=True)
        npm = FakeNpm(registry)
        with self.assertRaisesRegex(promote.PromotionError, "not public"):
            self.transition("stage-meta", registry, npm)
        self.assertEqual([], npm.calls)
        self.assertEqual([], self.github.promotions)

        for package in self.candidate.packages[:-1]:
            registry.publish(package)
        stage_root = self.root / "stage-meta"
        stage_root.mkdir()
        settlement = promote.execute_transition(
            operation="stage-meta",
            candidate=self.candidate,
            lineage=self.lineage,
            registry=registry,
            npm=npm,
            github=self.github,
            attestations=self.attestations,
            attestation_evidence=self.evidence,
            attestation_evidence_sha256=self.evidence_sha256,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            writer=promote.SettlementWriter(
                stage_root / "npm-publication-settlement.json"
            ),
        )
        self.assertEqual("awaiting-meta-approval", settlement["status"])
        self.assertEqual(("stage", promote.META_PACKAGE), npm.calls[-1])
        settle_root = self.root / "settle-missing-meta"
        settle_root.mkdir()
        with self.assertRaisesRegex(promote.PromotionError, "not public"):
            promote.execute_transition(
                operation="settle-and-promote",
                candidate=self.candidate,
                lineage=self.lineage,
                registry=registry,
                npm=None,
                github=self.github,
                attestations=self.attestations,
                attestation_evidence=self.evidence,
                attestation_evidence_sha256=self.evidence_sha256,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                writer=promote.SettlementWriter(
                    settle_root / "npm-publication-settlement.json"
                ),
            )
        self.assertEqual([], self.github.promotions)
        registry.publish(self.candidate.packages[-1])
        final_root = self.root / "settle-complete"
        final_root.mkdir()
        final = promote.execute_transition(
            operation="settle-and-promote",
            candidate=self.candidate,
            lineage=self.lineage,
            registry=registry,
            npm=None,
            github=self.github,
            attestations=self.attestations,
            attestation_evidence=self.evidence,
            attestation_evidence_sha256=self.evidence_sha256,
            workflow_run_id=WORKFLOW_RUN_ID,
            workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
            writer=promote.SettlementWriter(
                final_root / "npm-publication-settlement.json"
            ),
        )
        self.assertEqual("complete", final["status"])
        self.assertIsNone(final["npmMutationToolchain"])
        self.assertTrue(final["github"]["promoted"])
        self.assertEqual([self.candidate], self.github.promotions)

    def test_registry_name_version_and_meta_dependencies_are_exact(self) -> None:
        for mutation in ("name", "version", "dependencies"):
            with self.subTest(mutation=mutation):
                registry = FakeRegistry(existing_platforms=True)
                for package in self.candidate.packages:
                    registry.publish(package)
                meta = registry.states[promote.META_PACKAGE]
                current = meta.versions[VERSION]
                if mutation == "name":
                    changed = replace(current, name="@openprose/not-the-cli")
                elif mutation == "version":
                    changed = replace(current, version="9.9.9-alpha.9")
                else:
                    changed = replace(current, optional_dependencies={})
                registry.states[promote.META_PACKAGE] = replace(
                    meta, versions={**meta.versions, VERSION: changed}
                )
                root = self.root / f"registry-{mutation}"
                root.mkdir()
                with self.assertRaises(promote.PromotionError):
                    promote.execute_transition(
                        operation="settle-and-promote",
                        candidate=self.candidate,
                        lineage=self.lineage,
                        registry=registry,
                        npm=None,
                        github=self.github,
                        attestations=self.attestations,
                        attestation_evidence=self.evidence,
                        attestation_evidence_sha256=self.evidence_sha256,
                        workflow_run_id=WORKFLOW_RUN_ID,
                        workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                        writer=promote.SettlementWriter(
                            root / "npm-publication-settlement.json"
                        ),
                    )
                self.assertEqual([], self.github.promotions)

    def test_lost_github_promotion_response_is_recorded_as_ambiguous(self) -> None:
        class FailedGitHub:
            def promote(self, value: promote.Candidate) -> None:
                raise promote.PromotionError("GitHub promotion outcome is ambiguous")

        registry = FakeRegistry(existing_platforms=True)
        for package in self.candidate.packages:
            registry.publish(package)
        root = self.root / "github-ambiguous"
        root.mkdir()
        writer = promote.SettlementWriter(root / "npm-publication-settlement.json")
        with self.assertRaisesRegex(promote.PromotionError, "ambiguous"):
            promote.execute_transition(
                operation="settle-and-promote",
                candidate=self.candidate,
                lineage=self.lineage,
                registry=registry,
                npm=None,
                github=FailedGitHub(),
                attestations=self.attestations,
                attestation_evidence=self.evidence,
                attestation_evidence_sha256=self.evidence_sha256,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                writer=writer,
            )
        report = json.loads(writer.path.read_text())
        self.assertEqual("ambiguous", report["status"])
        self.assertTrue(report["github"]["promotionAttempted"])
        self.assertFalse(report["github"]["promoted"])
        self.assertEqual("ambiguous", report["github"]["outcome"])
        with self.assertRaisesRegex(promote.PromotionError, "prior mutation attempt"):
            promote.execute_transition(
                operation="settle-and-promote",
                candidate=self.candidate,
                lineage=self.lineage,
                registry=registry,
                npm=None,
                github=FailedGitHub(),
                attestations=self.attestations,
                attestation_evidence=self.evidence,
                attestation_evidence_sha256=self.evidence_sha256,
                workflow_run_id=WORKFLOW_RUN_ID,
                workflow_run_attempt=WORKFLOW_RUN_ATTEMPT,
                writer=writer,
            )


def _entry(report: dict[str, object], name: str) -> dict[str, object]:
    return next(  # type: ignore[index,union-attr,no-any-return]
        item for item in report["packages"] if item["name"] == name
    )


def _fixture_tree_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, body in sorted(files.items()):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def toolchain_fixture(
    root: Path,
    *,
    members: list[tuple[tarfile.TarInfo, bytes]] | None = None,
) -> tuple[Path, Path, promote.NpmArchiveAuthority]:
    files = {
        "package/bin/npm-cli.js": b"console.log('11.15.0')\n",
        "package/package.json": b'{"name":"npm","version":"11.15.0"}\n',
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        if members is None:
            members = []
            for name, body in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(body)
                member.mode = 0o644
                member.mtime = 0
                members.append((member, body))
        for member, body in members:
            archive.addfile(member, io.BytesIO(body) if member.isfile() else None)
    encoded = output.getvalue()
    tarball = root / "npm tool $(never).tgz"
    tarball.write_bytes(encoded)
    node = root / "node $(never)"
    node.write_bytes(b"fixture node executable\n")
    node.chmod(0o700)
    authority = promote.NpmArchiveAuthority(
        url=promote.NPM_TARBALL_URL,
        byte_length=len(encoded),
        sha1=hashlib.sha1(encoded).hexdigest(),
        sha256=hashlib.sha256(encoded).hexdigest(),
        sha512=hashlib.sha512(encoded).hexdigest(),
        integrity="sha512-"
        + base64.b64encode(hashlib.sha512(encoded).digest()).decode("ascii"),
        tree_sha256=_fixture_tree_digest(files),
        entry_sha256=hashlib.sha256(files["package/bin/npm-cli.js"]).hexdigest(),
    )
    return tarball, node, authority


class FakeAttestationProcess:
    def __init__(self, *, result_count: int = 1, tamper: Path | None = None) -> None:
        self.result_count = result_count
        self.tamper = tamper
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def run(
        self,
        argv,
        *,
        environment,
        timeout_seconds,
        maximum_output,
    ):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                list(argv),
                {
                    "environment": dict(environment),
                    "timeout_seconds": timeout_seconds,
                    "maximum_output": maximum_output,
                },
            )
        )
        if argv[-1] == "--version":
            return promote.ProcessResult(
                exit_code=0,
                stdout=b"gh version 2.74.1 (2025-06-10)\n",
            )
        if self.tamper is not None:
            self.tamper.write_bytes(b"changed verifier bytes\n")
            self.tamper.chmod(0o700)
        subject = Path(argv[3])
        body = subject.read_bytes()
        value = [
            {
                "attestation": {},
                "verificationResult": {
                    "statement": {
                        "predicateType": promote.PROVENANCE_PREDICATE,
                        "subject": [
                            {
                                "name": f"release-assets/{subject.name}",
                                "digest": {"sha256": hashlib.sha256(body).hexdigest()},
                            }
                        ],
                    }
                },
            }
            for _ in range(self.result_count)
        ]
        return promote.ProcessResult(
            exit_code=0,
            stdout=json.dumps(value, separators=(",", ":")).encode("ascii"),
        )


class BoundaryTests(unittest.TestCase):
    def test_immutable_draft_authority_binds_body_inventory_and_producer_run(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_root = root / "candidate"
            candidate_root.mkdir()
            value = candidate(candidate_root)
            body = b"# Retained release body from the producing workflow\n"
            path, digest = write_draft_authority(root, value, body)
            authority = promote.load_draft_authority(
                path,
                expected_sha256=digest,
                repository=REPOSITORY,
                release_id=RELEASE_ID,
                version=VERSION,
                source_sha=SOURCE_SHA,
                producer_run_id=DRAFT_WORKFLOW_RUN_ID,
                producer_run_attempt=DRAFT_WORKFLOW_RUN_ATTEMPT,
            )
            promote.require_draft_authority_candidate(authority, value)
            self.assertEqual(hashlib.sha256(body).hexdigest(), authority.body_sha256)
            self.assertEqual(digest, authority.sha256)

            mutations = {
                "digest": {"expected_sha256": "f" * 64},
                "producer run": {"producer_run_id": DRAFT_WORKFLOW_RUN_ID + 1},
                "producer attempt": {
                    "producer_run_attempt": DRAFT_WORKFLOW_RUN_ATTEMPT + 1
                },
                "repository": {"repository": "someone/else"},
                "release": {"release_id": RELEASE_ID + 1},
                "version": {"version": "0.15.0-alpha.2"},
                "source": {"source_sha": "2" * 40},
            }
            common: dict[str, object] = {
                "expected_sha256": digest,
                "repository": REPOSITORY,
                "release_id": RELEASE_ID,
                "version": VERSION,
                "source_sha": SOURCE_SHA,
                "producer_run_id": DRAFT_WORKFLOW_RUN_ID,
                "producer_run_attempt": DRAFT_WORKFLOW_RUN_ATTEMPT,
            }
            for label, changed in mutations.items():
                with self.subTest(label=label), self.assertRaises(
                    promote.PromotionError
                ):
                    promote.load_draft_authority(path, **(common | changed))

            drifted = replace(
                value,
                assets=(
                    replace(value.assets[0], sha256="a" * 64),
                    *value.assets[1:],
                ),
            )
            with self.assertRaisesRegex(promote.PromotionError, "inventory"):
                promote.require_draft_authority_candidate(authority, drifted)

    def test_read_only_github_boundary_has_no_release_mutation_authority(self) -> None:
        github = promote.GitHubRelease(
            repository=REPOSITORY,
            token="fixture",
            http=object(),  # type: ignore[arg-type]
        )
        with TemporaryDirectory() as directory, self.assertRaisesRegex(
            promote.PromotionError, "mutation authority is unavailable"
        ):
            github.promote(candidate(Path(directory)))

    def test_candidate_byte_authenticator_detects_post_evidence_asset_drift(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package_root = root / "packages"
            package_root.mkdir()
            value = candidate(package_root)
            asset_root = root / "assets"
            asset_root.mkdir()
            packages = {package.artifact: package.body for package in value.packages}
            closed_assets = []
            for asset in value.assets:
                body = packages.get(asset.name, asset.name.encode("ascii"))
                (asset_root / asset.name).write_bytes(body)
                closed_assets.append(
                    promote.ReleaseAsset(
                        name=asset.name,
                        byte_length=len(body),
                        sha256=hashlib.sha256(body).hexdigest(),
                    )
                )
            value = replace(
                value,
                assets=tuple(closed_assets),
                assembly_sha256=next(
                    asset.sha256
                    for asset in closed_assets
                    if asset.name == "SHA256SUMS"
                ),
            )
            authenticator = promote.CandidateAssetAuthenticator(asset_root)
            authenticator.reauthenticate(value)
            (asset_root / value.assets[0].name).write_bytes(b"drifted")
            with self.assertRaisesRegex(promote.PromotionError, "bytes changed"):
                authenticator.reauthenticate(value)

    def test_pinned_npm_archive_authority_is_exact(self) -> None:
        self.assertEqual(2_901_197, promote.PINNED_NPM_AUTHORITY.byte_length)
        self.assertEqual(
            "d1a5bc920f2068774ae4d802916379fad266ba0d",
            promote.PINNED_NPM_AUTHORITY.sha1,
        )
        self.assertEqual(
            "8e5f6f3429f8cdbe693cdc29904e9d5a7b127a494bd15c804bd54c7403bfcbe7",
            promote.PINNED_NPM_AUTHORITY.entry_sha256,
        )
        self.assertEqual(
            "sha512-+k0tk7lRnpMUPnC7kTuU/yrV/mnFoPhJQ75VfLtZ6fwbzOVXaPsTE/"
            "Il9Pn1DHi482byMyqkHv/XsQ76mNjXLw==",
            promote.PINNED_NPM_AUTHORITY.integrity,
        )

    def test_settlement_is_closed_and_contains_no_private_paths_or_secrets(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_value = candidate(root)
            value = promote.new_settlement(
                operation="bootstrap",
                candidate=candidate_value,
                lineage=promote.Lineage(latest="0.14.0", owners=OWNERS),
                draft_authority=promotion_draft_identity(candidate_value),
            )
            value["credential"] = "OSC-SECRET\x1b]8;;file:///private\x07"
            with self.assertRaisesRegex(promote.PromotionError, "unknown or missing"):
                promote.validate_settlement(value)
            del value["credential"]
            writer = promote.SettlementWriter(
                root / "mode" / "npm-publication-settlement.json"
            )
            writer.write(value)
            encoded = writer.path.read_bytes()
            self.assertNotIn(str(root).encode(), encoded)
            self.assertNotIn(b"SECRET", encoded)
            self.assertEqual(0o600, writer.path.stat().st_mode & 0o777)
            self.assertEqual(0o700, writer.path.parent.stat().st_mode & 0o777)

    def test_settlement_toolchain_identity_is_closed_and_operation_bound(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_value = candidate(root)
            value = promote.new_settlement(
                operation="bootstrap",
                candidate=candidate_value,
                lineage=promote.Lineage(latest="0.14.0", owners=OWNERS),
                draft_authority=promotion_draft_identity(candidate_value),
            )
            value["npmMutationToolchain"] = {
                **FAKE_TOOLCHAIN_IDENTITY,
                "npmEntryPath": "/private/npm-cli.js",
            }
            with self.assertRaisesRegex(promote.PromotionError, "unknown or missing"):
                promote.validate_settlement(value)
            settle = promote.new_settlement(
                operation="settle-and-promote",
                candidate=candidate_value,
                lineage=promote.Lineage(latest="0.14.0", owners=OWNERS),
                draft_authority=promotion_draft_identity(candidate_value),
            )
            settle["npmMutationToolchain"] = dict(FAKE_TOOLCHAIN_IDENTITY)
            with self.assertRaisesRegex(promote.PromotionError, "inconsistent"):
                promote.validate_settlement(settle)

    def test_settlement_terminal_states_are_operation_and_evidence_exact(self) -> None:
        schema = Draft202012Validator(
            json.loads(
                Path("cli/release/alpha-promotion-settlement.schema.json").read_text()
            )
        )

        def fresh(root: Path, operation: str) -> dict[str, object]:
            candidate_value = candidate(root)
            return promote.new_settlement(
                operation=operation,
                candidate=candidate_value,
                lineage=promote.Lineage(latest="0.14.0", owners=OWNERS),
                draft_authority=promotion_draft_identity(candidate_value),
            )

        def attest(value: dict[str, object]) -> None:
            state = value["githubArtifactAttestations"]
            assert isinstance(state, dict)
            state.update(
                {
                    "status": "verified",
                    "verifiedAssetCount": 38,
                    "bytesReauthenticated": True,
                    "workflowRunId": WORKFLOW_RUN_ID,
                    "workflowRunAttempt": WORKFLOW_RUN_ATTEMPT,
                    "evidenceSha256": "8" * 64,
                    "tool": dict(FAKE_GH_TOOL_IDENTITY),
                }
            )
            assets = state["assets"]
            assert isinstance(assets, list)
            for asset in assets:
                asset["outcome"] = "verified"

        def public(
            item: dict[str, object], *, action: str, attempted: bool, outcome: str
        ) -> None:
            item.update(
                {
                    "action": action,
                    "attempted": attempted,
                    "outcome": outcome,
                    "registryIntegrity": item["integrity"],
                    "provenancePredicateType": promote.PROVENANCE_PREDICATE,
                }
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            complete = fresh(root, "settle-and-promote")
            attest(complete)
            for item in complete["packages"]:
                public(item, action="verify", attempted=False, outcome="verified")
            complete["github"].update(
                {
                    "promotionAttempted": True,
                    "promoted": True,
                    "outcome": "promoted",
                }
            )
            complete["status"] = "complete"

            npm_settled = fresh(root, "bootstrap")
            attest(npm_settled)
            npm_settled["npmMutationToolchain"] = dict(FAKE_TOOLCHAIN_IDENTITY)
            for item in npm_settled["packages"]:
                public(item, action="publish", attempted=True, outcome="published")
            npm_settled["status"] = "npm-settled"

            awaiting_platforms = fresh(root, "stage-platforms")
            attest(awaiting_platforms)
            awaiting_platforms["npmMutationToolchain"] = dict(FAKE_TOOLCHAIN_IDENTITY)
            for item in awaiting_platforms["packages"][:-1]:
                item.update({"action": "stage", "attempted": True, "outcome": "staged"})
            awaiting_platforms["status"] = "awaiting-platform-approval"

            awaiting_meta = fresh(root, "stage-meta")
            attest(awaiting_meta)
            awaiting_meta["npmMutationToolchain"] = dict(FAKE_TOOLCHAIN_IDENTITY)
            for item in awaiting_meta["packages"][:-1]:
                public(item, action="verify", attempted=False, outcome="verified")
            awaiting_meta["packages"][-1].update(
                {"action": "stage", "attempted": True, "outcome": "staged"}
            )
            awaiting_meta["status"] = "awaiting-meta-approval"

            for value in (
                complete,
                npm_settled,
                awaiting_platforms,
                awaiting_meta,
            ):
                promote.validate_settlement(value)
                schema.validate(value)

            contradictory_complete = fresh(root, "settle-and-promote")
            contradictory_complete["status"] = "complete"
            missing_npm_package = copy.deepcopy(npm_settled)
            missing_npm_package["packages"][-1].update(
                {
                    "action": "none",
                    "attempted": False,
                    "outcome": "not-attempted",
                    "registryIntegrity": None,
                    "provenancePredicateType": None,
                }
            )
            staged_meta_too_early = copy.deepcopy(awaiting_platforms)
            staged_meta_too_early["packages"][-1].update(
                {"action": "stage", "attempted": True, "outcome": "staged"}
            )
            missing_public_platform = copy.deepcopy(awaiting_meta)
            missing_public_platform["packages"][0].update(
                {
                    "action": "none",
                    "attempted": False,
                    "outcome": "not-attempted",
                    "registryIntegrity": None,
                    "provenancePredicateType": None,
                }
            )
            wrong_operation = copy.deepcopy(awaiting_platforms)
            wrong_operation["operation"] = "bootstrap"
            initialized_with_tool = fresh(root, "bootstrap")
            initialized_with_tool["npmMutationToolchain"] = dict(
                FAKE_TOOLCHAIN_IDENTITY
            )
            in_progress_without_attestation = fresh(root, "bootstrap")
            in_progress_without_attestation["status"] = "in-progress"
            failed_without_failure = fresh(root, "bootstrap")
            failed_without_failure["status"] = "failed"
            ambiguous_without_attempt = fresh(root, "stage-platforms")
            attest(ambiguous_without_attempt)
            ambiguous_without_attempt["npmMutationToolchain"] = dict(
                FAKE_TOOLCHAIN_IDENTITY
            )
            ambiguous_without_attempt["status"] = "ambiguous"
            partial_without_attempt = fresh(root, "bootstrap")
            attest(partial_without_attempt)
            partial_without_attempt["npmMutationToolchain"] = dict(
                FAKE_TOOLCHAIN_IDENTITY
            )
            partial_without_attempt["status"] = "partial-publication"

            for label, value in (
                ("complete-without-evidence", contradictory_complete),
                ("npm-settled-without-meta", missing_npm_package),
                ("platform-stage-with-meta", staged_meta_too_early),
                ("meta-stage-without-platform", missing_public_platform),
                ("operation-mismatch", wrong_operation),
                ("initialized-with-tool", initialized_with_tool),
                ("in-progress-without-attestation", in_progress_without_attestation),
                ("failed-without-failure", failed_without_failure),
                ("ambiguous-without-attempt", ambiguous_without_attempt),
                ("partial-without-attempt", partial_without_attempt),
            ):
                with self.subTest(label=label):
                    with self.assertRaisesRegex(
                        promote.PromotionError, "settlement .* inconsistent"
                    ):
                        promote.validate_settlement(value)
                    self.assertTrue(list(schema.iter_errors(value)))

    def test_candidate_manifest_rejects_publication_authority_and_scripts(self) -> None:
        manifest = promote._npm_package_json(
            package_bytes(promote.META_PACKAGE, "meta"), "fixture.tgz"
        )
        for mutation in ("authority", "scripts"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(manifest)
                if mutation == "authority":
                    changed["openproseCohort"]["publicationAuthorized"] = True
                else:
                    changed["scripts"] = {"prepublishOnly": "exfiltrate"}
                with self.assertRaises(promote.PromotionError):
                    promote._validate_package_manifest(
                        changed,
                        name=promote.META_PACKAGE,
                        version=VERSION,
                        source_sha=SOURCE_SHA,
                        role="meta",
                    )

    def test_candidate_manifest_accepts_legacy_absent_public_config_only(self) -> None:
        manifest = promote._npm_package_json(
            package_bytes(promote.META_PACKAGE, "meta"), "fixture.tgz"
        )
        del manifest["publishConfig"]
        promote._validate_package_manifest(
            manifest,
            name=promote.META_PACKAGE,
            version=VERSION,
            source_sha=SOURCE_SHA,
            role="meta",
        )
        manifest["publishConfig"] = {"access": "restricted"}
        with self.assertRaisesRegex(promote.PromotionError, "incompatible"):
            promote._validate_package_manifest(
                manifest,
                name=promote.META_PACKAGE,
                version=VERSION,
                source_sha=SOURCE_SHA,
                role="meta",
            )

    def test_cli_requires_exact_confirmation_before_network(self) -> None:
        arguments = promote.parser().parse_args(
            [
                "--mode",
                "execute-transition",
                "--operation",
                "bootstrap",
                "--repository",
                REPOSITORY,
                "--release-id",
                str(RELEASE_ID),
                "--version",
                VERSION,
                "--source-sha",
                SOURCE_SHA,
                "--workflow-run-id",
                str(WORKFLOW_RUN_ID),
                "--workflow-run-attempt",
                str(WORKFLOW_RUN_ATTEMPT),
                "--draft-authority",
                "alpha-draft-authority.json",
                "--draft-authority-sha256",
                DRAFT_AUTHORITY_SHA256,
                "--draft-authority-run-id",
                str(DRAFT_WORKFLOW_RUN_ID),
                "--draft-authority-run-attempt",
                str(DRAFT_WORKFLOW_RUN_ATTEMPT),
                "--attestation-evidence",
                "github-attestation-evidence.json",
                "--attestation-evidence-sha256",
                "8" * 64,
                "--confirmation",
                "almost",
                "--settlement",
                "npm-publication-settlement.json",
            ]
        )
        with self.assertRaisesRegex(promote.PromotionError, "confirmation"):
            promote._validate_inputs(arguments)

    def test_cli_requires_tarball_only_for_npm_mutation_operations(self) -> None:
        common = [
            "--mode",
            "execute-transition",
            "--repository",
            REPOSITORY,
            "--release-id",
            str(RELEASE_ID),
            "--version",
            VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--workflow-run-id",
            str(WORKFLOW_RUN_ID),
            "--workflow-run-attempt",
            str(WORKFLOW_RUN_ATTEMPT),
            "--draft-authority",
            "alpha-draft-authority.json",
            "--draft-authority-sha256",
            DRAFT_AUTHORITY_SHA256,
            "--draft-authority-run-id",
            str(DRAFT_WORKFLOW_RUN_ID),
            "--draft-authority-run-attempt",
            str(DRAFT_WORKFLOW_RUN_ATTEMPT),
            "--attestation-evidence",
            "github-attestation-evidence.json",
            "--attestation-evidence-sha256",
            "8" * 64,
            "--settlement",
            "npm-publication-settlement.json",
        ]
        for operation in ("bootstrap", "stage-platforms", "stage-meta"):
            with self.subTest(operation=operation):
                arguments = promote.parser().parse_args(
                    [
                        "--operation",
                        operation,
                        "--confirmation",
                        confirmation(operation),
                        *common,
                    ]
                )
                with self.assertRaisesRegex(promote.PromotionError, "tarball"):
                    promote._validate_inputs(arguments)
        operation = "settle-and-promote"
        arguments = promote.parser().parse_args(
            [
                "--operation",
                operation,
                "--confirmation",
                confirmation(operation),
                "--npm-tarball",
                "npm-11.15.0.tgz",
                *common,
            ]
        )
        with self.assertRaisesRegex(promote.PromotionError, "does not accept"):
            promote._validate_inputs(arguments)

    def test_cli_modes_keep_attestation_and_mutation_custody_disjoint(self) -> None:
        common = [
            "--repository",
            REPOSITORY,
            "--release-id",
            str(RELEASE_ID),
            "--version",
            VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--workflow-run-id",
            str(WORKFLOW_RUN_ID),
            "--workflow-run-attempt",
            str(WORKFLOW_RUN_ATTEMPT),
            "--draft-authority",
            "alpha-draft-authority.json",
            "--draft-authority-sha256",
            DRAFT_AUTHORITY_SHA256,
            "--draft-authority-run-id",
            str(DRAFT_WORKFLOW_RUN_ID),
            "--draft-authority-run-attempt",
            str(DRAFT_WORKFLOW_RUN_ATTEMPT),
            "--attestation-evidence",
            "github-attestation-evidence.json",
        ]
        read_only = promote.parser().parse_args(
            [
                "--mode",
                "verify-attestations",
                "--github-cli",
                "/usr/bin/gh",
                "--npm-tarball",
                "npm-11.15.0.tgz",
                *common,
            ]
        )
        with self.assertRaisesRegex(promote.PromotionError, "mutation custody"):
            promote._validate_inputs(read_only)

        mutation = promote.parser().parse_args(
            [
                "--mode",
                "execute-transition",
                "--operation",
                "bootstrap",
                "--confirmation",
                confirmation("bootstrap"),
                "--settlement",
                "npm-publication-settlement.json",
                "--npm-tarball",
                "npm-11.15.0.tgz",
                "--attestation-evidence-sha256",
                "8" * 64,
                "--github-cli",
                "/usr/bin/gh",
                *common,
            ]
        )
        with self.assertRaisesRegex(promote.PromotionError, "does not accept GitHub"):
            promote._validate_inputs(mutation)

        admitted_read_only = promote.parser().parse_args(
            [
                "--mode",
                "verify-attestations",
                "--github-cli",
                "/usr/bin/gh",
                *common,
            ]
        )
        promote._validate_inputs(admitted_read_only)
        self.assertEqual(WORKFLOW_RUN_ID, admitted_read_only.workflow_run_id)

        admitted_mutation = promote.parser().parse_args(
            [
                "--mode",
                "execute-transition",
                "--operation",
                "bootstrap",
                "--confirmation",
                confirmation("bootstrap"),
                "--settlement",
                "npm-publication-settlement.json",
                "--npm-tarball",
                "npm-11.15.0.tgz",
                "--attestation-evidence-sha256",
                "8" * 64,
                *common,
            ]
        )
        promote._validate_inputs(admitted_mutation)
        self.assertIsNone(admitted_mutation.github_cli)

    def test_pinned_lineage_rejects_an_unrelated_numbered_alpha(self) -> None:
        with self.assertRaisesRegex(promote.PromotionError, "outside the pinned"):
            promote.load_lineage(
                Path("cli/release/npm-registry-lineage.v1.json"),
                "9.9.9-alpha.1",
            )

    def test_npm_uses_direct_argv_for_hostile_tarball_path(self) -> None:
        with TemporaryDirectory(prefix="prefix $(never) ") as directory:
            root = Path(directory)
            package = artifacts(root)[0]
            tarball, node, authority = toolchain_fixture(root)
            mutation_calls: list[tuple[list[str], dict[str, object]]] = []
            configs: list[tuple[bytes, bytes]] = []

            def executor(argv, **kwargs):  # type: ignore[no-untyped-def]
                if argv[-1:] == ["--version"]:
                    output = b"11.15.0\n" if len(argv) == 3 else b"v24.20.0\n"
                    return subprocess_result(argv, output)
                mutation_calls.append((argv, kwargs))
                environment = kwargs["env"]
                configs.append(
                    (
                        Path(environment["npm_config_userconfig"]).read_bytes(),
                        Path(environment["npm_config_globalconfig"]).read_bytes(),
                    )
                )
                return subprocess_result(argv, b"")

            npm = promote.NpmCli(
                npm_tarball=tarball,
                node=str(node),
                operation="bootstrap",
                executor=executor,
                authority=authority,
            )
            self.addCleanup(npm.close)
            with patch.dict(
                os.environ,
                {
                    "HOME": "/hostile/ambient/home",
                    "npm_config_userconfig": "/hostile/user.npmrc",
                    "npm_config_globalconfig": "/hostile/global.npmrc",
                    "NPM_TOKEN": "fixture-token",
                },
                clear=False,
            ):
                npm.mutate("publish", package)
            self.assertEqual(1, len(mutation_calls))
            argv, kwargs = mutation_calls[0]
            environment = kwargs["env"]
            self.assertIsInstance(argv, list)
            self.assertEqual(str(node.resolve()), argv[0])
            self.assertEqual("npm-cli.js", Path(argv[1]).name)
            self.assertIn(str(package.path), argv)
            self.assertNotIn("shell", kwargs)
            self.assertNotIn("fixture-token", repr(argv))
            self.assertNotEqual("/hostile/ambient/home", environment["HOME"])
            self.assertNotEqual(
                "/hostile/user.npmrc", environment["npm_config_userconfig"]
            )
            self.assertNotEqual(
                "/hostile/global.npmrc", environment["npm_config_globalconfig"]
            )
            self.assertEqual("fixture-token", environment["NODE_AUTH_TOKEN"])
            self.assertNotIn("NPM_TOKEN", environment)
            self.assertEqual(
                b"registry=https://registry.npmjs.org/\n"
                b"provenance=true\n"
                b"//registry.npmjs.org/:_authToken=${NODE_AUTH_TOKEN}\n"
                b"always-auth=true\n",
                configs[-1][0],
            )
            self.assertEqual(b"registry=https://registry.npmjs.org/\n", configs[-1][1])
            self.assertNotIn(b"fixture-token", configs[-1][0])

    def test_oidc_stage_uses_fresh_token_free_npm_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = artifacts(root)[0]
            tarball, node, authority = toolchain_fixture(root)
            observed: dict[str, object] = {}

            def executor(argv, **kwargs):  # type: ignore[no-untyped-def]
                if argv[-1:] == ["--version"]:
                    output = b"11.15.0\n" if len(argv) == 3 else b"v24.20.0\n"
                    return subprocess_result(argv, output)
                environment = kwargs["env"]
                observed["argv"] = argv
                observed["environment"] = environment
                observed["user"] = Path(
                    environment["npm_config_userconfig"]
                ).read_bytes()
                return subprocess_result(argv, b"")

            npm = promote.NpmCli(
                npm_tarball=tarball,
                node=str(node),
                operation="stage-platforms",
                executor=executor,
                authority=authority,
            )
            self.addCleanup(npm.close)
            with patch.dict(
                os.environ,
                {
                    "HOME": "/hostile/home",
                    "NODE_AUTH_TOKEN": "",
                    "NPM_TOKEN": "",
                    "npm_config_userconfig": "/hostile/user.npmrc",
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.fixture.invalid",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-fixture",
                },
                clear=False,
            ):
                npm.mutate("stage", package)
            environment = observed["environment"]
            self.assertEqual(
                b"registry=https://registry.npmjs.org/\nprovenance=true\n",
                observed["user"],
            )
            self.assertNotIn("NODE_AUTH_TOKEN", environment)
            self.assertNotIn("NPM_TOKEN", environment)
            self.assertNotIn("_authToken", observed["user"].decode())
            self.assertNotEqual("/hostile/home", environment["HOME"])

    def test_toolchain_requires_exact_node_and_npm_versions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tarball, node, authority = toolchain_fixture(root)
            versions = {"node": b"v24.20.0\n", "npm": b"11.15.0\n"}

            def executor(argv, **kwargs):  # type: ignore[no-untyped-def]
                kind = "npm" if len(argv) == 3 else "node"
                return subprocess_result(argv, versions[kind])

            npm = promote.NpmCli(
                npm_tarball=tarball,
                node=str(node),
                operation="stage-platforms",
                executor=executor,
                authority=authority,
            )
            self.addCleanup(npm.close)
            identity = {
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.fixture.invalid",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fixture",
                "NPM_TOKEN": "",
                "NODE_AUTH_TOKEN": "",
                "NPM_CONFIG_TOKEN": "",
            }
            with patch.dict(os.environ, identity, clear=False):
                observed = npm.require_toolchain()
            self.assertEqual("24.20.0", observed["nodeVersion"])
            self.assertEqual("11.15.0", observed["npmVersion"])
            self.assertEqual(authority.tree_sha256, observed["npmTreeSha256"])
            for program, wrong in (
                ("node", b"v24.20.1\n"),
                ("npm", b"11.15.1\n"),
            ):
                with self.subTest(program=program):
                    versions[program] = wrong
                    with patch.dict(
                        os.environ, identity, clear=False
                    ), self.assertRaises(promote.PromotionError):
                        npm.require_toolchain()
                    versions.update({"node": b"v24.20.0\n", "npm": b"11.15.0\n"})

    def test_toolchain_reauthenticates_archive_tree_entry_and_node_after_mutation(
        self,
    ) -> None:
        for target in ("archive", "tree", "entry", "node"):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                root = Path(directory)
                package = artifacts(root)[0]
                tarball, node, authority = toolchain_fixture(root)
                mutation_calls = 0
                npm: promote.NpmCli | None = None

                def executor(argv, **kwargs):  # type: ignore[no-untyped-def]
                    nonlocal mutation_calls
                    if argv[-1:] == ["--version"]:
                        output = b"11.15.0\n" if len(argv) == 3 else b"v24.20.0\n"
                        return subprocess_result(argv, output)
                    mutation_calls += 1
                    assert npm is not None
                    if target == "archive":
                        tarball.write_bytes(b"tampered after npm mutation\n")
                    elif target == "tree":
                        (npm.root / "unexpected").write_bytes(b"unexpected")
                    elif target == "entry":
                        npm.npm_entry.chmod(0o600)
                        npm.npm_entry.write_bytes(b"tampered entry\n")
                    else:
                        node.write_bytes(b"tampered node\n")
                        node.chmod(0o700)
                    return subprocess_result(argv, b"")

                npm = promote.NpmCli(
                    npm_tarball=tarball,
                    node=str(node),
                    operation="stage-platforms",
                    executor=executor,
                    authority=authority,
                )
                try:
                    with self.assertRaisesRegex(
                        promote.PromotionError,
                        "toolchain|tarball|tree|entry|Node.js",
                    ):
                        npm.mutate("stage", package)
                    self.assertEqual(1, mutation_calls)
                finally:
                    npm.close()

    def test_toolchain_reauthenticates_before_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = artifacts(root)[0]
            tarball, node, authority = toolchain_fixture(root)
            mutation_calls = 0

            def executor(argv, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal mutation_calls
                if argv[-1:] == ["--version"]:
                    output = b"11.15.0\n" if len(argv) == 3 else b"v24.20.0\n"
                    return subprocess_result(argv, output)
                mutation_calls += 1
                return subprocess_result(argv, b"")

            npm = promote.NpmCli(
                npm_tarball=tarball,
                node=str(node),
                operation="stage-platforms",
                executor=executor,
                authority=authority,
            )
            try:
                tarball.write_bytes(b"changed before mutation\n")
                with self.assertRaisesRegex(promote.PromotionError, "tarball"):
                    npm.mutate("stage", package)
                self.assertEqual(0, mutation_calls)
            finally:
                npm.close()

    def test_toolchain_rejects_unsafe_archive_members_before_npm(self) -> None:
        for kind in (
            "absolute",
            "traversal",
            "symlink",
            "hardlink",
            "device",
            "duplicate",
            "oversize",
        ):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                root = Path(directory)
                body = b"fixture"
                first = tarfile.TarInfo("package/bin/npm-cli.js")
                first.size = len(body)
                members = [(first, body)]
                if kind == "absolute":
                    unsafe = tarfile.TarInfo("/package/escape")
                    unsafe.size = len(body)
                    members.append((unsafe, body))
                elif kind == "traversal":
                    unsafe = tarfile.TarInfo("package/../escape")
                    unsafe.size = len(body)
                    members.append((unsafe, body))
                elif kind == "symlink":
                    unsafe = tarfile.TarInfo("package/link")
                    unsafe.type = tarfile.SYMTYPE
                    unsafe.linkname = "/etc/passwd"
                    members.append((unsafe, b""))
                elif kind == "hardlink":
                    unsafe = tarfile.TarInfo("package/link")
                    unsafe.type = tarfile.LNKTYPE
                    unsafe.linkname = "package/bin/npm-cli.js"
                    members.append((unsafe, b""))
                elif kind == "device":
                    unsafe = tarfile.TarInfo("package/device")
                    unsafe.type = tarfile.CHRTYPE
                    members.append((unsafe, b""))
                elif kind == "duplicate":
                    duplicate = tarfile.TarInfo("package/bin/npm-cli.js")
                    duplicate.size = len(body)
                    members.append((duplicate, body))
                else:
                    oversized_body = b"x" * (promote.MAX_NPM_MEMBER_BYTES + 1)
                    oversized = tarfile.TarInfo("package/oversized")
                    oversized.size = len(oversized_body)
                    members.append((oversized, oversized_body))
                tarball, node, authority = toolchain_fixture(root, members=members)
                authority = replace(
                    authority,
                    tree_sha256="0" * 64,
                    entry_sha256=hashlib.sha256(body).hexdigest(),
                )
                with self.assertRaisesRegex(promote.PromotionError, "unsafe|closed"):
                    promote.NpmCli(
                        npm_tarball=tarball,
                        node=str(node),
                        operation="bootstrap",
                        authority=authority,
                    )

    def test_unknown_redirect_is_never_followed(self) -> None:
        http = promote.StrictHttp()

        def redirected(request, *, timeout):  # type: ignore[no-untyped-def]
            raise HTTPError(
                request.full_url,
                302,
                "redirect",
                {"Location": "https://attacker.invalid/candidate.tgz"},
                None,
            )

        http._open = redirected
        with self.assertRaisesRegex(promote.PromotionError, "outside the closed"):
            http.request(
                method="GET",
                url="https://api.github.com/repos/openprose/prose/releases/assets/1",
                github_asset_redirect=True,
            )

    def test_expected_asset_inventory_is_closed_at_38(self) -> None:
        names = promote.expected_asset_names(VERSION)
        self.assertEqual(38, len(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertIn(f"openprose-prose-cli-{VERSION}.tgz", names)

    def test_github_attestation_uses_exact_direct_cryptographic_policy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "gh $(never)"
            executable.write_bytes(b"fixture GitHub CLI\n")
            executable.chmod(0o700)
            asset_root = root / "assets"
            asset_root.mkdir()
            value = candidate(root)
            asset = value.assets[0]
            (asset_root / asset.name).write_bytes(asset.name.encode("ascii"))
            process = FakeAttestationProcess()
            verifier = promote.GitHubAttestationVerifier(
                asset_root=asset_root,
                executable=str(executable.resolve()),
                token="fixture-token",
                process=process,
            )
            self.addCleanup(verifier.close)
            identity = verifier.require_toolchain()
            results = verifier.verify(candidate=value, asset=asset)
            self.assertEqual("2.74.1", identity["version"])
            self.assertEqual(
                hashlib.sha256(executable.read_bytes()).hexdigest(),
                identity["executableSha256"],
            )
            self.assertEqual((promote._expected_attestation(value, asset),), results)
            argv, metadata = process.calls[-1]
            self.assertIsInstance(argv, list)
            self.assertEqual(str(executable.resolve()), argv[0])
            self.assertEqual(
                [
                    "--repo",
                    "--source-digest",
                    "--source-ref",
                    "--signer-workflow",
                    "--signer-digest",
                    "--predicate-type",
                    "--cert-oidc-issuer",
                    "--deny-self-hosted-runners",
                    "--format",
                    "--limit",
                ],
                [item for item in argv if item.startswith("--")],
            )
            self.assertEqual("2", argv[-1])
            self.assertEqual(VERSION not in repr(argv), True)
            self.assertNotIn("fixture-token", repr(argv))
            environment = metadata["environment"]
            self.assertEqual("fixture-token", environment["GH_TOKEN"])
            self.assertNotIn("PATH", environment)
            self.assertEqual(
                promote.ATTESTATION_TIMEOUT_SECONDS, metadata["timeout_seconds"]
            )

    def test_github_attestation_rejects_multiple_results_and_tool_tampering(
        self,
    ) -> None:
        for mutation in ("multiple", "tool"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                executable = root / "gh"
                executable.write_bytes(b"fixture GitHub CLI\n")
                executable.chmod(0o700)
                asset_root = root / "assets"
                asset_root.mkdir()
                value = candidate(root)
                asset = value.assets[0]
                (asset_root / asset.name).write_bytes(asset.name.encode("ascii"))
                process = FakeAttestationProcess(
                    result_count=2 if mutation == "multiple" else 1,
                    tamper=executable if mutation == "tool" else None,
                )
                verifier = promote.GitHubAttestationVerifier(
                    asset_root=asset_root,
                    executable=str(executable.resolve()),
                    token="fixture-token",
                    process=process,
                )
                try:
                    verifier.require_toolchain()
                    with self.assertRaises(promote.PromotionError):
                        verifier.verify(candidate=value, asset=asset)
                finally:
                    verifier.close()

    def test_github_attestation_rejects_stable_tool_replacement_between_calls(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "gh"
            executable.write_bytes(b"fixture GitHub CLI A\n")
            executable.chmod(0o700)
            asset_root = root / "assets"
            asset_root.mkdir()
            value = candidate(root)
            asset = value.assets[0]
            (asset_root / asset.name).write_bytes(asset.name.encode("ascii"))
            verifier = promote.GitHubAttestationVerifier(
                asset_root=asset_root,
                executable=str(executable.resolve()),
                token="fixture-token",
                process=FakeAttestationProcess(),
            )
            try:
                recorded = verifier.require_toolchain()
                executable.write_bytes(b"fixture GitHub CLI stable replacement B\n")
                executable.chmod(0o700)
                self.assertNotEqual(
                    recorded["executableSha256"],
                    hashlib.sha256(executable.read_bytes()).hexdigest(),
                )
                with self.assertRaisesRegex(promote.PromotionError, "identity changed"):
                    verifier.verify(candidate=value, asset=asset)
            finally:
                verifier.close()

    def test_workflow_is_manual_protected_and_does_not_build_or_approve(self) -> None:
        workflow = Path(".github/workflows/openprose-cli-alpha-promote.yml").read_text(
            "utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("push:", workflow)
        self.assertEqual(4, workflow.count("environment: openprose-cli-alpha-publish"))
        self.assertEqual(3, workflow.count("id-token: write"))
        bootstrap = workflow.split("  bootstrap:", 1)[1].split("  stage-platforms:", 1)[
            0
        ]
        self.assertIn("      contents: read\n      id-token: write\n", bootstrap)
        self.assertNotIn("contents: write", bootstrap)
        self.assertNotIn("attestations: read", bootstrap)
        self.assertIn(
            "Authenticate the successful functional-alpha draft producer", workflow
        )
        self.assertEqual(1, workflow.count("contents: write"))
        self.assertEqual(1, workflow.count("attestations: read"))
        self.assertEqual(1, workflow.count('test "$GITHUB_REF" = "refs/heads/main"'))
        self.assertEqual(1, workflow.count("OPENPROSE_NPM_BOOTSTRAP_TOKEN"))
        self.assertNotIn("npm install", workflow)
        self.assertEqual(3, workflow.count(promote.NPM_TARBALL_URL))
        self.assertEqual(3, workflow.count('--npm-tarball "$NPM_TARBALL"'))
        self.assertEqual(1, workflow.count('--github-cli "$GH_TOOL"'))
        self.assertEqual(
            1, workflow.count("Resolve the externally provisioned GitHub CLI verifier")
        )
        self.assertEqual(2, workflow.count('GH_TOOL="$(python - /usr/bin/gh'))
        self.assertEqual(4, workflow.count("--mode execute-transition"))
        self.assertEqual(1, workflow.count("--mode verify-attestations"))
        self.assertEqual(
            5,
            workflow.count(
                "openprose-alpha-attestation-evidence-run-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
            ),
        )
        self.assertNotIn("command -v gh", workflow)
        self.assertEqual(3, workflow.count("env -i PATH=/usr/bin:/bin /usr/bin/curl"))
        self.assertEqual(3, workflow.count("--max-filesize 2901197"))
        self.assertEqual(3, workflow.count("--max-redirs 0"))
        self.assertNotIn("--location", workflow)
        self.assertNotRegex(workflow, r"npm\s+(?:publish|pack|stage|approve|reject)\b")
        self.assertNotRegex(workflow, r"\b(?:cargo|bun)\s+build\b")
        uses = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", workflow)
        self.assertTrue(uses)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", value) for value in uses))
        settle = workflow.split("  settle-and-promote:", 1)[1]
        self.assertNotIn("id-token: write", settle)
        self.assertNotIn("NPM_TOKEN", settle)
        self.assertNotIn("setup-node", settle)
        self.assertNotIn("npm-11.15.0.tgz", settle)

    def test_functional_alpha_attestation_source_is_exact_workflow_control_sha(
        self,
    ) -> None:
        workflow = Path(".github/workflows/openprose-cli-alpha-release.yml").read_text(
            "utf-8"
        )
        self.assertNotIn("merge-base --is-ancestor", workflow)
        self.assertEqual(4, workflow.count('test "$SOURCE_SHA_INPUT" = "$GITHUB_SHA"'))
        self.assertEqual(
            2, workflow.count('test "$SOURCE_SHA_INPUT" = "$CONTROL_SHA_INPUT"')
        )
        self.assertLess(
            workflow.index('test "$SOURCE_SHA_INPUT" = "$GITHUB_SHA"'),
            workflow.index("actions/attest@"),
        )

    def test_helper_never_constructs_a_shell_mutation(self) -> None:
        source = Path("cli/ci/promote_alpha_release.py").read_text("utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("subprocess.call", source)
        self.assertIn("self.node,\n                    str(self.npm_entry)", source)
        self.assertIn('"publish",\n                    str(package.path)', source)
        self.assertIn('"stage",\n                    "publish",', source)


def subprocess_result(argv: list[str], stdout: bytes):
    import subprocess

    return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")


class FakeGitHubHttp:
    def __init__(
        self,
        version: str,
        *,
        release_mutation: str | None = None,
        asset_mutation: str | None = None,
    ) -> None:
        self.version = version
        self.release_mutation = release_mutation
        self.asset_mutation = asset_mutation
        self.body = "# Immutable retained release body from the draft run\n"
        self.bodies = {
            name: f"asset:{name}".encode()
            for name in promote.expected_asset_names(version)
        }
        self._open = None

    def draft_authority(self) -> promote.DraftAuthority:
        assets = tuple(
            promote.ReleaseAsset(
                name=name,
                byte_length=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
            for name, body in sorted(self.bodies.items())
        )
        inventory = [
            {
                "name": asset.name,
                "sha256": asset.sha256,
                "byteLength": asset.byte_length,
            }
            for asset in assets
        ]
        encoded_body = self.body.encode("utf-8")
        return promote.DraftAuthority(
            repository=REPOSITORY,
            release_id=RELEASE_ID,
            version=self.version,
            source_sha=SOURCE_SHA,
            control_sha=SOURCE_SHA,
            tag=f"cli-v{self.version}",
            body_byte_length=len(encoded_body),
            body_sha256=hashlib.sha256(encoded_body).hexdigest(),
            assets=assets,
            asset_inventory_sha256=hashlib.sha256(
                json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            workflow_run_id=DRAFT_WORKFLOW_RUN_ID,
            workflow_run_attempt=DRAFT_WORKFLOW_RUN_ATTEMPT,
            outcome="created",
            sha256="9" * 64,
        )

    def request(  # type: ignore[no-untyped-def]
        self, *, method: str, url: str, **kwargs
    ):
        if "/releases/187/assets?" in url:
            values = []
            for index, (name, body) in enumerate(sorted(self.bodies.items()), 1):
                values.append(
                    {
                        "id": index,
                        "name": name,
                        "url": f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{index}",
                        "state": "uploaded",
                        "content_type": "application/octet-stream",
                        "size": len(body),
                        "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
                    }
                )
            if self.asset_mutation == "extra":
                values.append(dict(values[0], id=999, name="unexpected.bin"))
            if self.asset_mutation == "digest":
                values[0]["digest"] = "sha256:" + "f" * 64
            return 200, json.dumps(values).encode()
        if "/releases/assets/" in url:
            asset_id = int(url.rsplit("/", 1)[-1])
            name = sorted(self.bodies)[asset_id - 1]
            return 200, self.bodies[name]
        value = {
            "id": RELEASE_ID,
            "url": f"https://api.github.com/repos/{REPOSITORY}/releases/{RELEASE_ID}",
            "assets_url": f"https://api.github.com/repos/{REPOSITORY}/releases/{RELEASE_ID}/assets",
            "tag_name": f"cli-v{self.version}",
            "name": f"OpenProse CLI v{self.version} functional alpha",
            "draft": True,
            "prerelease": True,
            "immutable": False,
            "body": self.body,
        }
        if self.release_mutation == "tag":
            value["tag_name"] = "cli-v9.9.9-alpha.9"
        if self.release_mutation == "draft":
            value["draft"] = False
        if self.release_mutation == "body":
            value["body"] = "# Regenerated moving-template body\n"
        return 200, json.dumps(value).encode()


class GitHubDownloadTests(unittest.TestCase):
    def test_draft_tag_asset_and_digest_drift_fail_closed(self) -> None:
        for release_mutation, asset_mutation in (
            ("tag", None),
            ("draft", None),
            ("body", None),
            (None, "extra"),
            (None, "digest"),
        ):
            with self.subTest(release=release_mutation, asset=asset_mutation):
                http = FakeGitHubHttp(
                    VERSION,
                    release_mutation=release_mutation,
                    asset_mutation=asset_mutation,
                )
                github = promote.GitHubRelease(
                    repository=REPOSITORY,
                    token="fixture",
                    http=http,  # type: ignore[arg-type]
                    draft_authority=http.draft_authority(),
                )
                github._resolve_tag = (  # type: ignore[method-assign]
                    lambda version, sha: None
                )
                with TemporaryDirectory() as directory, self.assertRaises(
                    promote.PromotionError
                ):
                    github.download_candidate(
                        release_id=RELEASE_ID,
                        version=VERSION,
                        source_sha=SOURCE_SHA,
                        root=Path(directory) / "assets",
                    )

    def test_release_body_uses_immutable_authority_not_current_template(self) -> None:
        http = FakeGitHubHttp(VERSION)
        github = promote.GitHubRelease(
            repository=REPOSITORY,
            token="fixture",
            http=http,  # type: ignore[arg-type]
            draft_authority=http.draft_authority(),
        )
        self.assertEqual(
            http.body,
            github._release(RELEASE_ID, VERSION, draft_expected=True)["body"],
        )
        source = Path("cli/ci/promote_alpha_release.py").read_text("utf-8")
        self.assertNotIn("_functional_alpha_release_notes", source)


if __name__ == "__main__":
    unittest.main()
