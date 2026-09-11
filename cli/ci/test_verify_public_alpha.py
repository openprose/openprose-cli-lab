from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError

import promote_alpha_release as promotion
import verify_public_alpha as verify


VERSION = "0.15.0-alpha.1"
SOURCE_SHA = "1" * 40
REPOSITORY = "openprose/prose"
RELEASE_ID = 187
TARGET_ID = "linux-x64"
OWNERS = ("jose_at_prose", "dan_openprose")
TIMESTAMP = "2026-09-01T03:00:00Z"
DRAFT_WORKFLOW_RUN_ID = 811
DRAFT_WORKFLOW_RUN_ATTEMPT = 3
RELEASE_BODY = b"# Immutable functional-alpha release body\n"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "release"
    / "alpha-public-verification.schema.json"
)


def package_bytes(name: str, role: str) -> bytes:
    cohort = {
        "schema": "openprose.npm-cohort/1",
        "version": VERSION,
        "sourceRevision": SOURCE_SHA,
        "releaseChannel": "functional-alpha",
        "purpose": "provisional-echo-runtime",
        "image": {"version": "echo-v0"},
        "admittedPlatforms": sorted(promotion.PLATFORMS),
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
            package: VERSION for package in promotion.PLATFORM_PACKAGES
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


def fixture_bodies() -> dict[str, bytes]:
    bodies: dict[str, bytes] = {}
    for name in promotion.expected_asset_names(VERSION):
        if name == "SHA256SUMS":
            continue
        package_name: str | None = None
        if name == f"openprose-prose-cli-{VERSION}.tgz":
            package_name = promotion.META_PACKAGE
        else:
            for candidate in promotion.PLATFORM_PACKAGES:
                platform = candidate.removeprefix("@openprose/prose-cli-")
                if name == f"openprose-prose-cli-{platform}-{VERSION}.tgz":
                    package_name = candidate
                    break
        if package_name is None:
            bodies[name] = f"fixture:{name}\n".encode()
        else:
            bodies[name] = package_bytes(
                package_name,
                "meta" if package_name == promotion.META_PACKAGE else "platform",
            )
    checksum = "".join(
        f"{hashlib.sha256(bodies[name]).hexdigest()}  {name}\n"
        for name in sorted(bodies)
    ).encode("ascii")
    return {"SHA256SUMS": checksum, **bodies}


def remote_assets(bodies: dict[str, bytes]) -> tuple[verify.RemoteAsset, ...]:
    return tuple(
        verify.RemoteAsset(
            name=name,
            asset_id=index,
            api_url=f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{index}",
            byte_length=len(bodies[name]),
            sha256=hashlib.sha256(bodies[name]).hexdigest(),
        )
        for index, name in enumerate(sorted(bodies), start=1)
    )


def authority_value(bodies: dict[str, bytes], **changes: object) -> dict[str, object]:
    assets = [
        {
            "name": name,
            "sha256": hashlib.sha256(bodies[name]).hexdigest(),
            "byteLength": len(bodies[name]),
        }
        for name in sorted(bodies)
    ]
    value: dict[str, object] = {
        "schema": "openprose.alpha-draft-authority/1",
        "repository": REPOSITORY,
        "version": VERSION,
        "sourceSha": SOURCE_SHA,
        "controlSha": SOURCE_SHA,
        "tag": f"cli-v{VERSION}",
        "releaseId": RELEASE_ID,
        "draft": True,
        "prerelease": True,
        "publicationAuthorized": False,
        "releaseBody": {
            "byteLength": len(RELEASE_BODY),
            "sha256": hashlib.sha256(RELEASE_BODY).hexdigest(),
        },
        "assetCount": 38,
        "assets": assets,
        "assetInventorySha256": hashlib.sha256(
            json.dumps(assets, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "workflowRun": {
            "path": ".github/workflows/openprose-cli-alpha-release.yml",
            "id": DRAFT_WORKFLOW_RUN_ID,
            "attempt": DRAFT_WORKFLOW_RUN_ATTEMPT,
        },
        "outcome": "created",
    }
    value.update(changes)
    return value


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_authority(
    root: Path,
    bodies: dict[str, bytes],
    *,
    value: dict[str, object] | None = None,
) -> verify.DraftAuthorityInput:
    encoded = canonical_json(value or authority_value(bodies))
    path = root / "alpha-draft-authority.json"
    path.write_bytes(encoded)
    return verify.DraftAuthorityInput(
        path=path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        workflow_run_id=DRAFT_WORKFLOW_RUN_ID,
        workflow_run_attempt=DRAFT_WORKFLOW_RUN_ATTEMPT,
    )


def authenticate_fixture(
    *, root: Path, repository: str, release_id: int, version: str, source_sha: str
) -> promotion.Candidate:
    checksums = root.joinpath("SHA256SUMS").read_text("ascii")
    declared = {}
    for line in checksums.splitlines():
        digest, name = line.split("  ", 1)
        declared[name] = digest
    if tuple(declared) != tuple(sorted(declared)):
        raise promotion.PromotionError("fixture checksums are not ordered")
    if set(declared) != {
        name for name in promotion.expected_asset_names(version) if name != "SHA256SUMS"
    }:
        raise promotion.PromotionError("fixture checksums are not closed")
    for name, digest in declared.items():
        if hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() != digest:
            raise promotion.PromotionError("fixture digest differs")
    assets = tuple(
        promotion.ReleaseAsset(
            name=name,
            byte_length=root.joinpath(name).stat().st_size,
            sha256=hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest(),
        )
        for name in promotion.expected_asset_names(version)
    )
    packages = []
    for name in promotion.PACKAGE_ORDER:
        role = "meta" if name == promotion.META_PACKAGE else "platform"
        platform = name.removeprefix("@openprose/prose-cli-")
        artifact = (
            f"openprose-prose-cli-{version}.tgz"
            if role == "meta"
            else f"openprose-prose-cli-{platform}-{version}.tgz"
        )
        body = root.joinpath(artifact).read_bytes()
        packages.append(
            promotion.PackageArtifact(
                name=name,
                role=role,
                artifact=artifact,
                path=root / artifact,
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                shasum=hashlib.sha1(body).hexdigest(),  # noqa: S324 - npm identity
                integrity="sha512-"
                + base64.b64encode(hashlib.sha512(body).digest()).decode("ascii"),
            )
        )
    return promotion.Candidate(
        repository=repository,
        release_id=release_id,
        version=version,
        source_sha=source_sha,
        tag=f"cli-v{version}",
        assembly_sha256=hashlib.sha256(
            root.joinpath("SHA256SUMS").read_bytes()
        ).hexdigest(),
        assets=assets,
        packages=tuple(packages),
    )


def inputs(**changes: object) -> verify.VerificationInputs:
    base: dict[str, object] = {
        "repository": REPOSITORY,
        "release_id": RELEASE_ID,
        "version": VERSION,
        "source_sha": SOURCE_SHA,
        "target_id": TARGET_ID,
        "workflow_path": verify.WORKFLOW_PATH,
        "workflow_run_id": 991,
        "workflow_run_attempt": 2,
        "confirmation": (
            f"VERIFY PUBLIC ALPHA {REPOSITORY} {VERSION} {SOURCE_SHA} "
            f"{RELEASE_ID} {TARGET_ID} 991/2"
        ),
    }
    base.update(changes)
    return verify.VerificationInputs(**base)  # type: ignore[arg-type]


class FakeGitHub:
    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.inventory = remote_assets(bodies)
        self.calls: list[tuple[str, str]] = []
        self.release_value = verify.ReleaseIdentity(
            release_id=RELEASE_ID,
            tag=f"cli-v{VERSION}",
            name=f"OpenProse CLI v{VERSION} functional alpha",
            draft=False,
            prerelease=True,
            immutable=False,
            body_byte_length=len(RELEASE_BODY),
            body_sha256=hashlib.sha256(RELEASE_BODY).hexdigest(),
        )
        self.tag_value = SOURCE_SHA
        self.fail_download: str | None = None
        self.secret = "token-super-secret"

    def release(self, release_id: int, version: str) -> verify.ReleaseIdentity:
        self.calls.append(("GET-release", str(release_id)))
        return self.release_value

    def tag_source(self, version: str) -> str:
        self.calls.append(("GET-tag", version))
        return self.tag_value

    def assets(self, release_id: int, version: str) -> tuple[verify.RemoteAsset, ...]:
        self.calls.append(("GET-assets", str(release_id)))
        return self.inventory

    def download(self, asset: verify.RemoteAsset) -> bytes:
        self.calls.append(("GET-download", asset.name))
        if asset.name == self.fail_download:
            raise RuntimeError(f"credential={self.secret} /Users/private/secret")
        return self.bodies[asset.name]


class FakeAttestations:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result_count = 1
        self.mismatch = False
        self.raise_secret = False

    def verify(
        self,
        *,
        path: Path,
        name: str,
        sha256: str,
        repository: str,
        source_sha: str,
    ) -> tuple[verify.AttestationResult, ...]:
        self.calls.append(name)
        if self.raise_secret:
            raise RuntimeError("bearer ghp_top_secret /Users/private/key")
        result = verify.AttestationResult(
            repository=repository,
            source_sha=source_sha,
            predicate_type=verify.PROVENANCE_PREDICATE,
            subject_name=name,
            subject_sha256="0" * 64 if self.mismatch else sha256,
        )
        return tuple(result for _ in range(self.result_count))


class FakeRegistry:
    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.states: dict[str, promotion.RegistryPackage] = {}
        self.tarballs: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        for name in promotion.PACKAGE_ORDER:
            role = "meta" if name == promotion.META_PACKAGE else "platform"
            platform = name.removeprefix("@openprose/prose-cli-")
            artifact = (
                f"openprose-prose-cli-{VERSION}.tgz"
                if role == "meta"
                else f"openprose-prose-cli-{platform}-{VERSION}.tgz"
            )
            body = bodies[artifact]
            url = f"https://registry.npmjs.org/fixture/-/{artifact}"
            version = promotion.RegistryVersion(
                name=name,
                version=VERSION,
                shasum=hashlib.sha1(body).hexdigest(),  # noqa: S324 - npm identity
                integrity="sha512-"
                + base64.b64encode(hashlib.sha512(body).digest()).decode("ascii"),
                tarball_url=url,
                optional_dependencies=(
                    {package: VERSION for package in promotion.PLATFORM_PACKAGES}
                    if role == "meta"
                    else None
                ),
                provenance_predicate_type=verify.PROVENANCE_PREDICATE,
            )
            self.states[name] = promotion.RegistryPackage(
                name=name,
                owners=OWNERS,
                tags={
                    "alpha": VERSION,
                    **({"latest": "0.14.0"} if role == "meta" else {}),
                },
                versions={VERSION: version},
            )
            self.tarballs[url] = body

    def package(self, name: str) -> promotion.RegistryPackage | None:
        self.calls.append(("GET-package", name))
        return copy.deepcopy(self.states.get(name))

    def tarball(self, url: str) -> bytes:
        self.calls.append(("GET-tarball", url))
        return self.tarballs[url]


class FakeJourneys:
    def __init__(self) -> None:
        self.calls: list[verify.JourneyRequest] = []
        self.omit = False
        self.failed_surface: str | None = None
        self.raise_secret = False

    def run(self, request: verify.JourneyRequest) -> tuple[verify.JourneyResult, ...]:
        self.calls.append(request)
        if self.raise_secret:
            raise RuntimeError("OPENAI_API_KEY=private /Users/private/.env")
        result = tuple(
            verify.JourneyResult(
                surface=item.surface,
                target_id=item.target_id,
                version=item.version,
                source_sha=item.source_sha,
                schema=verify.JOURNEY_SCHEMA,
                status=(
                    "failed"
                    if item.surface == self.failed_surface
                    else verify.JOURNEY_STATUS
                ),
                provider_calls=verify.PROVIDER_CALLS,
                semantic_evaluation=False,
                artifact_sha256=tuple(artifact.sha256 for artifact in item.artifacts),
            )
            for item in request.journeys
        )
        return result[:-1] if self.omit else result


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="public-alpha-verifier-test-")
        self.root = Path(self.temporary.name)
        self.bodies = fixture_bodies()
        self.github = FakeGitHub(self.bodies)
        self.attestations = FakeAttestations()
        self.registry = FakeRegistry(self.bodies)
        self.journeys = FakeJourneys()
        self.lineage = promotion.Lineage(latest="0.14.0", owners=OWNERS)
        self.evidence_path = self.root / "evidence" / "alpha-public-verification.json"
        self.authority = write_authority(self.root, self.bodies)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def execute(self) -> dict[str, object]:
        with patch.object(
            verify.promotion, "authenticate_assembly", side_effect=authenticate_fixture
        ):
            return verify.execute_verification(
                inputs=inputs(),
                draft_authority=self.authority,
                lineage=self.lineage,
                github=self.github,
                attestations=self.attestations,
                registry=self.registry,
                journeys=self.journeys,
                writer=verify.EvidenceWriter(self.evidence_path),
                workspace=self.root / "workspace",
                clock=lambda: TIMESTAMP,
            )

    def retained(self) -> dict[str, object]:
        return json.loads(self.evidence_path.read_text("utf-8"))

    def test_complete_verification_is_closed_and_every_boundary_is_visible(
        self,
    ) -> None:
        report = self.execute()
        self.assertEqual("complete", report["status"])
        self.assertTrue(report["draftAuthority"]["verified"])
        self.assertTrue(report["github"]["releaseBodyVerified"])
        self.assertEqual(self.authority.sha256, report["draftAuthority"]["sha256"])
        self.assertEqual(SOURCE_SHA, report["draftAuthority"]["controlSha"])
        self.assertEqual(
            {
                "byteLength": len(RELEASE_BODY),
                "sha256": hashlib.sha256(RELEASE_BODY).hexdigest(),
            },
            report["draftAuthority"]["releaseBody"],
        )
        self.assertEqual(38, report["draftAuthority"]["assetCount"])
        self.assertEqual(
            {
                "path": ".github/workflows/openprose-cli-alpha-release.yml",
                "id": DRAFT_WORKFLOW_RUN_ID,
                "attempt": DRAFT_WORKFLOW_RUN_ATTEMPT,
            },
            report["draftAuthority"]["workflowRun"],
        )
        self.assertEqual(38, report["github"]["assetCount"])
        self.assertEqual(38, len(self.attestations.calls))
        self.assertEqual(1, len(self.journeys.calls))
        self.assertEqual(2, sum(call[0] == "GET-release" for call in self.github.calls))
        self.assertEqual(2, sum(call[0] == "GET-tag" for call in self.github.calls))
        self.assertEqual(2, sum(call[0] == "GET-assets" for call in self.github.calls))
        self.assertEqual(
            38, sum(call[0] == "GET-download" for call in self.github.calls)
        )
        self.assertEqual(
            5, sum(call[0] == "GET-tarball" for call in self.registry.calls)
        )
        self.assertEqual(92, len(report["attempts"]))
        self.assertEqual("draft-authority", report["attempts"][0]["boundary"])
        self.assertTrue(
            all(item["outcome"] == "verified" for item in report["attempts"])
        )
        self.assertFalse(
            any(
                call[0].startswith(("POST", "PATCH", "PUT", "DELETE"))
                for call in self.github.calls + self.registry.calls
            )
        )
        schema = json.loads(SCHEMA_PATH.read_text())
        Draft202012Validator(schema).validate(report)
        self.assertEqual(report, self.retained())
        self.assertEqual(0o600, stat.S_IMODE(self.evidence_path.stat().st_mode))
        encoded = self.evidence_path.read_text("utf-8")
        self.assertNotIn(str(self.authority.path), encoded)
        self.assertNotIn(RELEASE_BODY.decode(), encoded)

    def test_exact_three_journeys_bind_public_artifact_digests(self) -> None:
        report = self.execute()
        request = self.journeys.calls[0]
        self.assertEqual(
            verify.SURFACES, tuple(item.surface for item in request.journeys)
        )
        self.assertEqual([1, 1, 2], [len(item.artifacts) for item in request.journeys])
        self.assertEqual(
            [
                [artifact.sha256 for artifact in item.artifacts]
                for item in request.journeys
            ],
            [item["artifactSha256"] for item in report["journeys"]],
        )

    def test_public_release_must_be_a_prerelease_not_a_draft(self) -> None:
        self.github.release_value = verify.ReleaseIdentity(
            release_id=RELEASE_ID,
            tag=f"cli-v{VERSION}",
            name=f"OpenProse CLI v{VERSION} functional alpha",
            draft=True,
            prerelease=True,
            immutable=False,
            body_byte_length=len(RELEASE_BODY),
            body_sha256=hashlib.sha256(RELEASE_BODY).hexdigest(),
        )
        with self.assertRaisesRegex(verify.VerificationError, "github-release"):
            self.execute()
        report = self.retained()
        self.assertEqual("failed", report["status"])
        self.assertEqual("MISMATCH", report["failure"]["code"])

    def test_closed_asset_inventory_rejects_missing_or_reordered_assets(self) -> None:
        self.github.inventory = self.github.inventory[:-1]
        with self.assertRaisesRegex(verify.VerificationError, "github-inventory"):
            self.execute()
        self.assertEqual("MISMATCH", self.retained()["failure"]["code"])

    def test_authority_byte_tamper_fails_first_without_public_observation(self) -> None:
        self.authority.path.write_bytes(self.authority.path.read_bytes() + b" ")
        with self.assertRaisesRegex(verify.VerificationError, "draft-authority"):
            self.execute()
        report = self.retained()
        self.assertEqual("BOUNDARY_FAILED", report["failure"]["code"])
        self.assertEqual([], self.github.calls)
        self.assertEqual([], self.registry.calls)
        self.assertEqual([], self.journeys.calls)

    def test_authority_identity_and_producer_replay_are_rejected(self) -> None:
        mutations: dict[str, object] = {
            "repository": "other/repository",
            "version": "0.15.0-alpha.2",
            "sourceSha": "2" * 40,
            "controlSha": "2" * 40,
            "tag": "cli-v0.15.0-alpha.2",
            "releaseId": RELEASE_ID + 1,
            "workflowRun": {
                "path": ".github/workflows/openprose-cli-alpha-release.yml",
                "id": DRAFT_WORKFLOW_RUN_ID + 1,
                "attempt": DRAFT_WORKFLOW_RUN_ATTEMPT,
            },
        }
        for field, changed in mutations.items():
            with self.subTest(field=field), TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.evidence_path = (
                    self.root / "evidence" / "alpha-public-verification.json"
                )
                self.github = FakeGitHub(self.bodies)
                value = authority_value(self.bodies, **{field: changed})
                self.authority = write_authority(self.root, self.bodies, value=value)
                with self.assertRaisesRegex(
                    verify.VerificationError, "draft-authority"
                ):
                    self.execute()
                self.assertEqual([], self.github.calls)

    def test_public_release_body_must_match_recorded_bytes_not_current_main(
        self,
    ) -> None:
        self.github.release_value = copy.copy(self.github.release_value)
        object.__setattr__(
            self.github.release_value,
            "body_sha256",
            hashlib.sha256(b"body regenerated from moving main").hexdigest(),
        )
        with patch.object(
            verify.draft,
            "_functional_alpha_release_notes",
            side_effect=AssertionError("current main must not render release notes"),
        ):
            with self.assertRaisesRegex(verify.VerificationError, "github-release"):
                self.execute()
        report = self.retained()
        self.assertEqual("MISMATCH", report["failure"]["code"])
        self.assertFalse(report["github"]["releaseBodyVerified"])

    def test_coherent_authority_inventory_tamper_is_rejected_as_public_drift(
        self,
    ) -> None:
        value = authority_value(self.bodies)
        assets = copy.deepcopy(value["assets"])
        assert isinstance(assets, list)
        assets[0]["sha256"] = "f" * 64
        value["assets"] = assets
        value["assetInventorySha256"] = hashlib.sha256(
            json.dumps(assets, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.authority.path.unlink()
        self.authority = write_authority(self.root, self.bodies, value=value)
        with self.assertRaisesRegex(verify.VerificationError, "github-inventory"):
            self.execute()
        self.assertEqual("MISMATCH", self.retained()["failure"]["code"])
        self.assertFalse(any(call[0] == "GET-download" for call in self.github.calls))

    def test_download_failure_retains_partial_evidence_without_credentials_or_paths(
        self,
    ) -> None:
        self.github.fail_download = self.github.inventory[3].name
        with self.assertRaisesRegex(
            verify.VerificationError, "github-asset-download"
        ) as caught:
            self.execute()
        encoded = self.evidence_path.read_text("utf-8")
        self.assertNotIn(self.github.secret, str(caught.exception))
        self.assertNotIn(self.github.secret, encoded)
        self.assertNotIn("/Users/private", encoded)
        report = json.loads(encoded)
        self.assertEqual("failed", report["status"])
        self.assertEqual(
            "AMBIGUOUS" if False else "BOUNDARY_FAILED", report["failure"]["code"]
        )
        self.assertTrue(any(item["downloaded"] for item in report["github"]["assets"]))

    def test_download_digest_mismatch_fails_closed(self) -> None:
        name = self.github.inventory[0].name
        self.github.bodies[name] += b"tamper"
        with self.assertRaisesRegex(verify.VerificationError, "github-asset-download"):
            self.execute()
        self.assertEqual("MISMATCH", self.retained()["failure"]["code"])

    def test_sha256s_mismatch_is_retained(self) -> None:
        def refuse(**kwargs: object) -> promotion.Candidate:
            raise promotion.PromotionError("host path and token must not escape")

        with patch.object(
            verify.promotion, "authenticate_assembly", side_effect=refuse
        ):
            with self.assertRaisesRegex(verify.VerificationError, "github-sha256s"):
                verify.execute_verification(
                    inputs=inputs(),
                    draft_authority=self.authority,
                    lineage=self.lineage,
                    github=self.github,
                    attestations=self.attestations,
                    registry=self.registry,
                    journeys=self.journeys,
                    writer=verify.EvidenceWriter(self.evidence_path),
                    workspace=self.root / "workspace",
                    clock=lambda: TIMESTAMP,
                )
        self.assertEqual("BOUNDARY_FAILED", self.retained()["failure"]["code"])
        self.assertNotIn(str(self.root), self.evidence_path.read_text())

    def test_each_asset_requires_exactly_one_attestation(self) -> None:
        for count in (0, 2):
            with self.subTest(count=count), TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.evidence_path = (
                    self.root / "evidence" / "alpha-public-verification.json"
                )
                self.github = FakeGitHub(self.bodies)
                self.attestations = FakeAttestations()
                self.attestations.result_count = count
                with self.assertRaisesRegex(
                    verify.VerificationError, "github-attestation"
                ):
                    self.execute()
                self.assertEqual("MISMATCH", self.retained()["failure"]["code"])

    def test_attestation_identity_mismatch_fails_closed(self) -> None:
        self.attestations.mismatch = True
        with self.assertRaisesRegex(verify.VerificationError, "github-attestation"):
            self.execute()

    def test_attestation_exception_is_sanitized(self) -> None:
        self.attestations.raise_secret = True
        with self.assertRaisesRegex(
            verify.VerificationError, "github-attestation"
        ) as caught:
            self.execute()
        encoded = self.evidence_path.read_text()
        self.assertNotIn("ghp_top_secret", str(caught.exception) + encoded)
        self.assertNotIn("/Users/private", encoded)

    def test_boundary_timeout_is_ambiguous_and_never_retried(self) -> None:
        class TimeoutAttestations(FakeAttestations):
            def verify(self, **kwargs: object) -> tuple[verify.AttestationResult, ...]:
                self.calls.append(str(kwargs["name"]))
                raise TimeoutError("secret timeout detail")

        self.attestations = TimeoutAttestations()
        with self.assertRaisesRegex(verify.VerificationError, "timed out"):
            self.execute()
        report = self.retained()
        self.assertEqual(1, len(self.attestations.calls))
        self.assertEqual("TIMEOUT", report["failure"]["code"])
        self.assertEqual("ambiguous", report["attempts"][-1]["outcome"])
        self.assertNotIn("secret timeout detail", self.evidence_path.read_text())

    def test_npm_integrity_provenance_alpha_and_latest_are_all_required(self) -> None:
        mutations = (
            "version",
            "owners",
            "integrity",
            "provenance",
            "alpha",
            "latest",
            "bytes",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.evidence_path = (
                    self.root / "evidence" / "alpha-public-verification.json"
                )
                self.github = FakeGitHub(self.bodies)
                self.attestations = FakeAttestations()
                self.registry = FakeRegistry(self.bodies)
                self.journeys = FakeJourneys()
                state = self.registry.states[promotion.PLATFORM_PACKAGES[0]]
                version = state.versions[VERSION]
                if mutation == "version":
                    state.versions.clear()
                    state.versions["0.15.0-alpha.2"] = version
                elif mutation == "owners":
                    self.registry.states[
                        promotion.PLATFORM_PACKAGES[0]
                    ] = promotion.RegistryPackage(
                        name=state.name,
                        owners=("unexpected-owner",),
                        tags=state.tags,
                        versions=state.versions,
                    )
                elif mutation == "integrity":
                    state.versions[VERSION] = copy.copy(version)
                    object.__setattr__(
                        state.versions[VERSION],
                        "integrity",
                        "sha512-" + base64.b64encode(b"bad").decode(),
                    )
                elif mutation == "provenance":
                    state.versions[VERSION] = copy.copy(version)
                    object.__setattr__(
                        state.versions[VERSION], "provenance_predicate_type", None
                    )
                elif mutation == "alpha":
                    state.tags["alpha"] = "0.14.0"
                elif mutation == "latest":
                    self.registry.states[promotion.META_PACKAGE].tags[
                        "latest"
                    ] = "0.14.1"
                else:
                    self.registry.tarballs[version.tarball_url] += b"tamper"
                with self.assertRaises(verify.VerificationError):
                    self.execute()
                self.assertEqual("failed", self.retained()["status"])

    def test_missing_or_failed_journey_is_rejected_without_retry(self) -> None:
        for mutation in ("missing", "failed"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.evidence_path = (
                    self.root / "evidence" / "alpha-public-verification.json"
                )
                self.github = FakeGitHub(self.bodies)
                self.attestations = FakeAttestations()
                self.registry = FakeRegistry(self.bodies)
                self.journeys = FakeJourneys()
                self.journeys.omit = mutation == "missing"
                self.journeys.failed_surface = (
                    "direct-bun" if mutation == "failed" else None
                )
                with self.assertRaisesRegex(
                    verify.VerificationError, "package-journeys"
                ):
                    self.execute()
                self.assertEqual(1, len(self.journeys.calls))
                self.assertEqual("MISMATCH", self.retained()["failure"]["code"])

    def test_journey_exception_is_sanitized_and_not_retried(self) -> None:
        self.journeys.raise_secret = True
        with self.assertRaisesRegex(
            verify.VerificationError, "package-journeys"
        ) as caught:
            self.execute()
        encoded = self.evidence_path.read_text()
        self.assertEqual(1, len(self.journeys.calls))
        self.assertNotIn("OPENAI_API_KEY", str(caught.exception) + encoded)
        self.assertNotIn("/Users/private", encoded)


class InputAndWriterTests(unittest.TestCase):
    def test_public_github_hashes_observed_body_without_rendering_current_main(
        self,
    ) -> None:
        class FakeReleaseApi:
            def _json(self, method: str, url: str) -> dict[str, object]:
                self.method = method
                self.url = url
                return {
                    "id": RELEASE_ID,
                    "url": (
                        f"https://api.github.com/repos/{REPOSITORY}/releases/"
                        f"{RELEASE_ID}"
                    ),
                    "assets_url": (
                        f"https://api.github.com/repos/{REPOSITORY}/releases/"
                        f"{RELEASE_ID}/assets"
                    ),
                    "tag_name": f"cli-v{VERSION}",
                    "name": f"OpenProse CLI v{VERSION} functional alpha",
                    "draft": False,
                    "prerelease": True,
                    "immutable": False,
                    "body": RELEASE_BODY.decode("utf-8"),
                }

        github = verify.PublicGitHub.__new__(verify.PublicGitHub)
        github.repository = REPOSITORY
        github.source_sha = SOURCE_SHA
        github._release = FakeReleaseApi()
        with patch.object(
            verify.draft,
            "_functional_alpha_release_notes",
            side_effect=AssertionError("moving main is not release-body authority"),
        ):
            identity = github.release(RELEASE_ID, VERSION)
        self.assertEqual(len(RELEASE_BODY), identity.body_byte_length)
        self.assertEqual(hashlib.sha256(RELEASE_BODY).hexdigest(), identity.body_sha256)

    def test_identity_and_confirmation_are_exact(self) -> None:
        verify.validate_inputs(inputs())
        bad_values = {
            "repository": "https://github.com/openprose/prose",
            "version": "0.15.0-alpha",
            "source_sha": "A" * 40,
            "target_id": "windows-x64",
            "workflow_path": "../workflow.yml",
            "workflow_run_id": 0,
            "confirmation": "VERIFY PUBLIC ALPHA",
        }
        for field, value in bad_values.items():
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                verify.validate_inputs(inputs(**{field: value}))

    def test_cli_numeric_inputs_reject_signs_whitespace_and_leading_zeroes(
        self,
    ) -> None:
        for value in ("0", "01", "+1", " 1", "1 ", "1.0"):
            args = verify.parser().parse_args(
                [
                    "--repository",
                    REPOSITORY,
                    "--release-id",
                    value,
                    "--version",
                    VERSION,
                    "--source-sha",
                    SOURCE_SHA,
                    "--target-id",
                    TARGET_ID,
                    "--workflow-path",
                    verify.WORKFLOW_PATH,
                    "--workflow-run-id",
                    "1",
                    "--workflow-run-attempt",
                    "1",
                    "--confirmation",
                    "no",
                    "--draft-authority",
                    "/authority/alpha-draft-authority.json",
                    "--draft-authority-sha256",
                    "0" * 64,
                    "--draft-workflow-run-id",
                    str(DRAFT_WORKFLOW_RUN_ID),
                    "--draft-workflow-run-attempt",
                    str(DRAFT_WORKFLOW_RUN_ATTEMPT),
                    "--evidence",
                    "alpha-public-verification.json",
                ]
            )
            with self.subTest(value=value), self.assertRaises(verify.VerificationError):
                verify.inputs_from_args(args)

    def test_evidence_writer_refuses_existing_file_and_symlink(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = write_authority(root, fixture_bodies())
            value = verify.new_evidence(
                inputs(),
                draft_authority=authority,
                stable_latest="0.14.0",
                clock=lambda: TIMESTAMP,
            )
            path = root / "alpha-public-verification.json"
            writer = verify.EvidenceWriter(path)
            writer.start(value)
            before = path.read_bytes()
            with self.assertRaisesRegex(verify.VerificationError, "already exists"):
                verify.EvidenceWriter(path).start(value)
            self.assertEqual(before, path.read_bytes())
            path.unlink()
            target = root / "target"
            target.write_text("private")
            path.symlink_to(target)
            with self.assertRaisesRegex(verify.VerificationError, "already exists"):
                verify.EvidenceWriter(path).start(value)
            self.assertEqual("private", target.read_text())

    def test_evidence_writer_detects_interposed_content_before_update(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "alpha-public-verification.json"
            authority = write_authority(root, fixture_bodies())
            value = verify.new_evidence(
                inputs(),
                draft_authority=authority,
                stable_latest="0.14.0",
                clock=lambda: TIMESTAMP,
            )
            writer = verify.EvidenceWriter(path)
            writer.start(value)
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(verify.VerificationError, "changed"):
                writer.update(value)
            self.assertEqual("{}\n", path.read_text())

    def test_existing_or_symlink_workspace_is_rejected_before_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = root / "workspace"
            workspace.symlink_to(target, target_is_directory=True)
            evidence = root / "evidence" / "alpha-public-verification.json"
            authority = write_authority(root, fixture_bodies())
            with self.assertRaisesRegex(
                verify.VerificationError, "must not already exist"
            ):
                verify.execute_verification(
                    inputs=inputs(),
                    draft_authority=authority,
                    lineage=promotion.Lineage(latest="0.14.0", owners=OWNERS),
                    github=FakeGitHub(fixture_bodies()),
                    attestations=FakeAttestations(),
                    registry=FakeRegistry(fixture_bodies()),
                    journeys=FakeJourneys(),
                    writer=verify.EvidenceWriter(evidence),
                    workspace=workspace,
                    clock=lambda: TIMESTAMP,
                )
            self.assertFalse(evidence.exists())

    def test_evidence_schema_and_runtime_reject_unknown_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            authority = write_authority(Path(temporary), fixture_bodies())
            value = verify.new_evidence(
                inputs(),
                draft_authority=authority,
                stable_latest="0.14.0",
                clock=lambda: TIMESTAMP,
            )
            changed = copy.deepcopy(value)
            changed["credential"] = "secret"
            with self.assertRaises(verify.VerificationError):
                verify.validate_evidence(changed)
            schema = json.loads(SCHEMA_PATH.read_text())
            with self.assertRaises(ValidationError):
                Draft202012Validator(schema).validate(changed)

    def test_draft_authority_cli_identity_is_exact(self) -> None:
        base = [
            "--repository",
            REPOSITORY,
            "--release-id",
            str(RELEASE_ID),
            "--version",
            VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--target-id",
            TARGET_ID,
            "--workflow-path",
            verify.WORKFLOW_PATH,
            "--workflow-run-id",
            "991",
            "--workflow-run-attempt",
            "2",
            "--confirmation",
            inputs().confirmation,
            "--draft-authority",
            "/authority/alpha-draft-authority.json",
            "--draft-authority-sha256",
            "0" * 64,
            "--draft-workflow-run-id",
            str(DRAFT_WORKFLOW_RUN_ID),
            "--draft-workflow-run-attempt",
            str(DRAFT_WORKFLOW_RUN_ATTEMPT),
            "--evidence",
            "alpha-public-verification.json",
        ]
        args = verify.parser().parse_args(base)
        authority = verify.draft_authority_from_args(args)
        self.assertEqual(Path("/authority/alpha-draft-authority.json"), authority.path)
        for attribute, value in (
            ("draft_authority_sha256", "f" * 63),
            ("draft_workflow_run_id", "0"),
            ("draft_workflow_run_attempt", "01"),
        ):
            changed = copy.copy(args)
            setattr(changed, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaises(
                verify.VerificationError
            ):
                verify.draft_authority_from_args(changed)


class AttestationProcessTests(unittest.TestCase):
    class FakeProcess:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.calls: list[tuple[list[str], dict[str, str], int, int]] = []

        def run(
            self,
            argv: list[str],
            *,
            environment: dict[str, str],
            timeout_seconds: int,
            maximum_stdout: int,
        ) -> verify.ProcessResult:
            self.calls.append((argv, environment, timeout_seconds, maximum_stdout))
            return verify.ProcessResult(exit_code=0, stdout=self.payload)

    def test_gh_verifier_uses_one_exact_direct_bounded_argv(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "gh"
            executable.write_text("#!/bin/sh\nexit 1\n")
            executable.chmod(0o700)
            asset = root / "asset.tgz"
            asset.write_bytes(b"asset")
            digest = hashlib.sha256(b"asset").hexdigest()
            payload = json.dumps(
                [
                    {
                        "attestation": {},
                        "verificationResult": {
                            "statement": {
                                "predicateType": verify.PROVENANCE_PREDICATE,
                                "subject": [
                                    {
                                        "name": "release-assets/asset.tgz",
                                        "digest": {"sha256": digest},
                                    }
                                ],
                            }
                        },
                    }
                ]
            ).encode()
            process = self.FakeProcess(payload)
            boundary = verify.GhAttestationVerifier(
                executable=str(executable), token="token-value", process=process
            )
            result = boundary.verify(
                path=asset,
                name="asset.tgz",
                sha256=digest,
                repository=REPOSITORY,
                source_sha=SOURCE_SHA,
            )
            self.assertEqual(1, len(result))
            argv, environment, timeout, maximum = process.calls[0]
            self.assertEqual(str(executable.resolve()), argv[0])
            self.assertEqual("attestation", argv[1])
            self.assertEqual("verify", argv[2])
            self.assertEqual("2", argv[-1])
            self.assertNotIn("token-value", argv)
            self.assertEqual("token-value", environment["GH_TOKEN"])
            self.assertEqual(verify.ATTESTATION_TIMEOUT_SECONDS, timeout)
            self.assertEqual(verify.MAX_ATTESTATION_BYTES, maximum)

    def test_read_only_http_rejects_every_non_get_or_request_body(self) -> None:
        class Delegate:
            def request(self, **kwargs: object) -> tuple[int, bytes]:
                raise AssertionError("non-read request reached delegate")

        adapter = verify.ReadOnlyHttp(Delegate())  # type: ignore[arg-type]
        for method, data in (("POST", None), ("PATCH", b"{}"), ("GET", b"{}")):
            with self.subTest(method=method), self.assertRaisesRegex(
                verify.VerificationError, "non-read"
            ):
                adapter.request(
                    method=method, url="https://registry.npmjs.org/x", data=data
                )


if __name__ == "__main__":
    unittest.main()
