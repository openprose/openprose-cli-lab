from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import create_draft_release as draft
from jsonschema import Draft202012Validator, ValidationError
from test_check_draft_release import (
    CONTROL_SHA,
    FakeGitHub,
    FakeResponse,
    SOURCE_SHA,
    rewrite_checksums,
    rewrite_json,
    write_assembly,
)


ALPHA_VERSION = "0.1.0-alpha.1"


def digest_record(encoded: bytes) -> dict[str, object]:
    return {"byteLength": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def canonical_alpha_admission(
    *,
    target: str,
    platform: str,
    manifest: dict[str, object],
    files: dict[str, bytes],
) -> dict[str, object]:
    alpha = draft._alpha_admission_verifier()
    benchmark = alpha.load_benchmark()
    image, _ = alpha.image_identity(benchmark, draft.CONTROL_ECHO_IMAGE_MANIFEST)
    adapter_manifest = draft._alpha_adapter_manifest_identity()
    fixture = alpha.FAKE_HARNESS.read_bytes()
    artifact_names = [record["path"] for record in manifest["artifacts"]]
    package_sources = {
        "release-manifest.json": files[f"{target}-release-manifest.json"],
        "sbom.cdx.json": files[f"{target}-sbom.cdx.json"],
        "provenance.json": files[f"{target}-provenance.json"],
        "dependency-evidence.json": files[f"{target}-dependency-evidence.json"],
        "SHA256SUMS": files[f"{target}-SHA256SUMS"],
        **{name: files[name] for name in artifact_names},
    }
    package = {
        "mode": "alpha",
        "platform": platform,
        "files": sorted(
            ({"path": name, **digest_record(value)} for name, value in package_sources.items()),
            key=lambda item: item["path"],
        ),
        "sha256Sums": digest_record(package_sources["SHA256SUMS"]),
        "releaseManifest": digest_record(package_sources["release-manifest.json"]),
        "buildProfiles": manifest["buildProfiles"],
    }
    installations = [
        {
            "surface": surface,
            "method": (
                "npm-global-offline-two-local-tarballs"
                if surface == "npm-launcher"
                else "validated-archive-extraction"
            ),
            "installedByteCount": index + 1,
            "treeSha256": hashlib.sha256(f"tree:{surface}".encode()).hexdigest(),
        }
        for index, surface in enumerate(alpha.SURFACES)
    ]
    installation_map = {item["surface"]: item for item in installations}
    artifact_digests = {name: hashlib.sha256(files[name]).hexdigest() for name in artifact_names}
    rust_name = f"openprose-prose-cli-rust-{ALPHA_VERSION}-{platform}.tar.gz"
    bun_name = f"openprose-prose-cli-bun-{ALPHA_VERSION}-{platform}.tar.gz"
    npm_platform = f"openprose-prose-cli-{platform}-{ALPHA_VERSION}.tgz"
    npm_meta = f"openprose-prose-cli-{ALPHA_VERSION}.tgz"
    rust_binary = hashlib.sha256(b"fixture-rust-binary").hexdigest()
    bun_binary = hashlib.sha256(b"fixture-bun-binary").hexdigest()
    surfaces = {
        "direct-rust": {
            "runner": "rust",
            "binarySha256": rust_binary,
            "packageArtifactSha256": artifact_digests[rust_name],
            "installationTreeSha256": installation_map["direct-rust"]["treeSha256"],
            "execution": "verified-provider-free-echo",
        },
        "direct-bun": {
            "runner": "bun",
            "binarySha256": bun_binary,
            "packageArtifactSha256": artifact_digests[bun_name],
            "installationTreeSha256": installation_map["direct-bun"]["treeSha256"],
            "execution": "verified-provider-free-echo",
        },
        "npm-launcher": {
            "runner": "bun",
            "binarySha256": bun_binary,
            "packageArtifactSha256": artifact_digests[npm_platform],
            "installationTreeSha256": installation_map["npm-launcher"]["treeSha256"],
            "execution": "verified-provider-free-echo",
            "metaPackageSha256": artifact_digests[npm_meta],
            "launcherSourceSha256": hashlib.sha256(b"fixture-launcher").hexdigest(),
        },
    }
    task = {
        "schema": "openprose.task-envelope/1",
        "argv": ["prose", "run", "hello.prose.md"],
        "interactionMode": "non-interactive",
    }
    codex_stdin, task_sha = alpha.codex_wire(
        benchmark, draft.CONTROL_ECHO_IMAGE_MANIFEST, task
    )
    image_bytes = alpha.image_payload_bytes(
        benchmark, draft.CONTROL_ECHO_IMAGE_MANIFEST
    )
    task_json = alpha.canonical_json(task).decode().strip()
    invocation_id = "01234567-89ab-7cde-8abc-0123456789ab"

    def stdin_for(adapter_id: str) -> bytes:
        if adapter_id == "codex/exec-json":
            return codex_stdin
        if adapter_id == "claude/print-stream-json":
            return b""
        prompt_id = (
            f"{invocation_id}.omp.prompt.1"
            if adapter_id == "omp/rpc"
            else invocation_id
        )
        prompt = alpha.canonical_json(
            {"id": prompt_id, "message": task_json, "type": "prompt"}
        )
        if adapter_id != "omp/rpc":
            return prompt
        state = alpha.canonical_json(
            {"id": f"{invocation_id}.omp.state.1", "type": "get_state"}
        )
        return state + prompt

    def observation(adapter: dict[str, object]) -> dict[str, object]:
        adapter_id = adapter["adapterId"]
        stdin = stdin_for(adapter_id)
        files_value: list[dict[str, object]] = []
        if adapter_id in {"claude/print-stream-json", "omp/rpc"}:
            files_value.append(
                {
                    "flag": (
                        "--append-system-prompt-file"
                        if adapter_id == "claude/print-stream-json"
                        else "--append-system-prompt"
                    ),
                    "path": "$IMAGE_PATH",
                    "byteLength": len(image_bytes),
                    "sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "base64": base64.b64encode(image_bytes).decode("ascii"),
                    "mode": "0600",
                }
            )
        if adapter_id == "omp/rpc":
            files_value.append(
                {
                    "flag": "--config",
                    "path": "$CONFIG_PATH",
                    "byteLength": len(alpha.OMP_CONFIG_BYTES),
                    "sha256": hashlib.sha256(alpha.OMP_CONFIG_BYTES).hexdigest(),
                    "base64": base64.b64encode(alpha.OMP_CONFIG_BYTES).decode("ascii"),
                    "mode": "0600",
                }
            )
        argv = {
            "codex/exec-json": ["$FIXTURE/codex", "exec", "--skip-git-repo-check", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--cd", "$WORKSPACE", "-"],
            "claude/print-stream-json": ["$FIXTURE/claude", "--safe-mode", "--print", "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--append-system-prompt-file", "$IMAGE_PATH", task_json],
            "prime/rpc": ["$FIXTURE/prime-agent", "--mode", "rpc", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files", "--daemon-socket", "$DAEMON_SOCKET", "--cwd", "$WORKSPACE", "--append-system-prompt", "$IMAGE_UTF8", "--model", alpha.FIXTURE_MODEL],
            "omp/rpc": ["$FIXTURE/omp", "--mode", "rpc", "--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-lsp", "--no-title", "--no-tools", "--append-system-prompt", "$IMAGE_PATH", "--model", alpha.FIXTURE_MODEL, "--config", "$CONFIG_PATH"],
        }[adapter_id]
        return {
            "adapterId": adapter_id,
            "argv": argv,
            "environmentNames": [],
            "stdin": {"base64": base64.b64encode(stdin).decode("ascii"), **digest_record(stdin)},
            "files": files_value,
            "daemonSocket": ({"path": "$DAEMON_SOCKET", "absolute": True, "existedAtHarnessStart": False, "parentMode": "0700"} if adapter_id == "prime/rpc" else None),
            "credentialConfig": None,
            "adapterControls": ({"PRIME_AGENT_TELEMETRY": "0"} if adapter_id == "prime/rpc" else {}),
            "taskSha256": task_sha,
        }

    executions: list[dict[str, object]] = []
    for surface in alpha.SURFACES:
        for adapter in alpha.supported_adapters(platform):
            harness = adapter["harness"]
            runner = surfaces[surface]["runner"]
            empty = digest_record(b"")
            selection_result = {
                "schema": "openprose.harness-selection/1",
                "harness": harness,
                "scope": "user",
                "path": "$USER_CONFIG",
                "changed": True,
            }
            result = {
                "runner": {"name": runner, "version": ALPHA_VERSION, "commit": SOURCE_SHA},
                "adapterId": adapter["adapterId"],
                "harnessVersion": adapter["harnessVersion"],
                "image": {key: image[key] for key in ("formatVersion", "version", "sha256")},
                "taskSha256": task_sha,
                "terminal": {"classification": "success", "transportCompleted": True, "terminalEventObserved": True},
                "semanticStatus": "not-applicable",
                "billingOwner": "user-provider",
                "runnerExitCode": 0,
            }
            doctor_result = {
                "runner": {"name": runner, "version": ALPHA_VERSION, "commit": SOURCE_SHA},
                "build": {"profile": "release", "testSeamsEnabled": False},
                "ready": True,
                "selectedHarness": harness,
                "selectedHarnessVersion": adapter["harnessVersion"],
                "selectedTransport": adapter["transport"],
                "selectedAdapterId": adapter["adapterId"],
                "promptPlacement": adapter["promptPlacement"],
                "isolation": alpha.reported_isolation(adapter, runner),
                "authCategory": "harness-managed",
                "billingOwner": "user-provider",
                "configurationHarness": {"value": harness, "sourceKind": "user-config"},
                "problems": [],
            }
            documented: dict[str, object] | str = "not-applicable"
            if harness == "codex":
                documented = {
                    "source": ("npm-meta/README.md" if surface == "npm-launcher" else f"{surface.removeprefix('direct-')}-standalone/README.txt"),
                    "command": ["$INSTALLED_CANDIDATE", "run", "$PACKAGED_EXAMPLE"],
                    "shell": False,
                    "hostilePathShadowed": True,
                    "exitCode": 0,
                    "fixtureReached": True,
                }
            executions.append(
                {
                    "surface": surface,
                    "harness": harness,
                    "exitCode": 0,
                    "stdout": digest_record(b"execution"),
                    "stderr": empty,
                    "harnessSelection": {"exitCode": 0, "stdout": digest_record(b"selection"), "stderr": empty, "result": selection_result, "config": digest_record(alpha.persisted_config(harness))},
                    "doctor": {"exitCode": 0, "stdout": digest_record(b"doctor"), "stderr": empty, "result": doctor_result},
                    "result": result,
                    "fixtureObservation": observation(adapter),
                    "documentedFirstRun": documented,
                }
            )
    return {
        "schema": alpha.SCHEMA,
        "status": "passed-provider-free-functional-alpha",
        "targetId": target,
        "version": ALPHA_VERSION,
        "sourceSha": SOURCE_SHA,
        "image": image,
        "adapterManifest": adapter_manifest,
        "fixture": {"path": str(alpha.FAKE_HARNESS.relative_to(alpha.CLI.parent)), **digest_record(fixture)},
        "package": package,
        "installations": installations,
        "executionToolchain": {"node": {**digest_record(b"node"), "version": "v24.20.0"}, "npm": digest_record(b"npm")},
        "surfaces": surfaces,
        "executions": executions,
        "claims": alpha.CLAIMS,
    }


def write_alpha_assembly(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for target, platform in draft.ALPHA_TARGET_PLATFORMS.items():
        artifact_names = (
            f"openprose-prose-cli-rust-{ALPHA_VERSION}-{platform}.tar.gz",
            f"openprose-prose-cli-bun-{ALPHA_VERSION}-{platform}.tar.gz",
            f"openprose-prose-cli-{platform}-{ALPHA_VERSION}.tgz",
            f"openprose-prose-cli-{ALPHA_VERSION}.tgz",
        )
        for artifact_name in artifact_names:
            files.setdefault(artifact_name, f"alpha artifact {artifact_name}\n".encode("ascii"))
        manifest = {
            "schema": "openprose.local-release-manifest/1",
            "mode": "alpha",
            "platform": platform,
            "version": ALPHA_VERSION,
            "releaseEligible": False,
            "publicationAuthorized": False,
            "source": {
                "revision": SOURCE_SHA,
                "verification": "matched-product-doctor",
            },
            "buildProfiles": {
                "rust": {"profile": "release", "testSeamsEnabled": False},
                "bun": {"profile": "release", "testSeamsEnabled": False},
            },
            "artifacts": [
                {
                    "path": artifact_name,
                    "byteLength": len(files[artifact_name]),
                    "sha256": hashlib.sha256(files[artifact_name]).hexdigest(),
                }
                for artifact_name in artifact_names
            ],
        }
        files[f"{target}-release-manifest.json"] = (
            json.dumps(manifest, sort_keys=True) + "\n"
        ).encode()
        for evidence in (
            "sbom.cdx.json",
            "provenance.json",
            "dependency-evidence.json",
        ):
            files[f"{target}-{evidence}"] = (
                json.dumps({"target": target, "kind": evidence}, sort_keys=True) + "\n"
            ).encode()
        files[f"{target}-SHA256SUMS"] = f"package checksum for {target}\n".encode()
        admission = canonical_alpha_admission(
            target=target,
            platform=platform,
            manifest=manifest,
            files=files,
        )
        files[f"{target}-alpha-admission.json"] = (
            json.dumps(admission, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
    for name, encoded in files.items():
        (root / name).write_bytes(encoded)
    rewrite_checksums(root)
    return {path.name: path.read_bytes() for path in root.iterdir()}


def write_alpha_notes(path: Path) -> bytes:
    encoded = draft._functional_alpha_release_notes(ALPHA_VERSION).encode("utf-8")
    path.write_bytes(encoded)
    return encoded


def rewrite_alpha_admission(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(
        (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    )


def git_object(kind: str, sha: str) -> dict[str, str]:
    return {
        "type": kind,
        "sha": sha,
        "url": (
            "https://api.github.com/repos/openprose/prose/git/"
            f"{'commits' if kind == 'commit' else 'tags'}/{sha}"
        ),
    }


def tag_ref(value: dict[str, str]) -> dict[str, object]:
    return {
        "ref": "refs/tags/openprose-cli-v0.1.0",
        "node_id": "fixture-tag-ref",
        "url": (
            "https://api.github.com/repos/openprose/prose/git/refs/tags/"
            "openprose-cli-v0.1.0"
        ),
        "object": value,
    }


def annotated_tag(sha: str, value: dict[str, str]) -> dict[str, object]:
    return {
        "node_id": f"fixture-tag-{sha}",
        "tag": f"fixture-{sha}",
        "sha": sha,
        "url": f"https://api.github.com/repos/openprose/prose/git/tags/{sha}",
        "message": "fixture annotated tag",
        "tagger": {
            "name": "OpenProse Release",
            "email": "release@example.invalid",
            "date": "2026-08-28T00:00:00Z",
        },
        "object": value,
        "verification": {
            "verified": False,
            "reason": "unsigned",
            "signature": None,
            "payload": None,
            "verified_at": None,
        },
    }


class ScriptedTagGitHub(FakeGitHub):
    def __init__(
        self,
        *,
        ref: dict[str, object],
        ref_status: int = 200,
        tags: dict[str, tuple[int, dict[str, object]]] | None = None,
    ) -> None:
        super().__init__()
        self.ref = ref
        self.ref_status = ref_status
        self.tags = tags or {}

    def __call__(self, request, *, timeout: int):
        path = urlparse(request.full_url).path
        if request.get_method() == "GET" and "/git/" in path:
            self.requests.append(request)
            if "/git/ref/tags/" in path:
                return FakeResponse(self.ref, status=self.ref_status)
            sha = path.rsplit("/", 1)[-1]
            status, value = self.tags.get(sha, (404, {}))
            return FakeResponse(value, status=status)
        return super().__call__(request, timeout=timeout)


class AcceptThenFailUploadGitHub(FakeGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def __call__(self, request, *, timeout: int):
        response = super().__call__(request, timeout=timeout)
        if (
            request.get_method() == "POST"
            and urlparse(request.full_url).netloc == "uploads.github.com"
            and len(self.assets) == 2
            and not self.failed
        ):
            self.failed = True
            raise URLError("secret response lost after server accepted upload")
        return response


class AlphaFakeGitHub(FakeGitHub):
    def _release_response(self, payload: dict[str, object]) -> dict[str, object]:
        value = super()._release_response(payload)
        value["html_url"] = (
            "https://github.com/openprose/prose/releases/tag/"
            f"cli-v{ALPHA_VERSION}"
        )
        return value

    def __call__(self, request, *, timeout: int):
        path = urlparse(request.full_url).path
        if request.get_method() == "GET" and "/git/ref/tags/" in path:
            self.requests.append(request)
            return FakeResponse(
                {
                    "ref": f"refs/tags/cli-v{ALPHA_VERSION}",
                    "node_id": "fixture-alpha-tag-ref",
                    "url": (
                        "https://api.github.com/repos/openprose/prose/git/refs/tags/"
                        f"cli-v{ALPHA_VERSION}"
                    ),
                    "object": git_object("commit", SOURCE_SHA),
                },
                status=200,
            )
        return super().__call__(request, timeout=timeout)


class AlphaAcceptThenFailUploadGitHub(AlphaFakeGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def __call__(self, request, *, timeout: int):
        response = super().__call__(request, timeout=timeout)
        if (
            request.get_method() == "POST"
            and urlparse(request.full_url).netloc == "uploads.github.com"
            and len(self.assets) == 2
            and not self.failed
        ):
            self.failed = True
            raise URLError("response lost after accepted alpha upload")
        return response


class FinalTagMutationGitHub(FakeGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.tag_reads = 0

    def __call__(self, request, *, timeout: int):
        if request.get_method() == "GET" and "/git/ref/tags/" in urlparse(request.full_url).path:
            self.tag_reads += 1
            if self.tag_reads > 1:
                self.requests.append(request)
                return FakeResponse(tag_ref(git_object("commit", "f" * 40)), status=200)
        return super().__call__(request, timeout=timeout)


class SaturatedReleaseDiscoveryGitHub(FakeGitHub):
    def __call__(self, request, *, timeout: int):
        parsed = urlparse(request.full_url)
        if (
            request.get_method() == "GET"
            and parsed.path == "/repos/openprose/prose/releases"
        ):
            self.requests.append(request)
            page = int(parse_qs(parsed.query)["page"][0])
            return FakeResponse(
                [
                    {"tag_name": f"unrelated-{page}-{index}"}
                    for index in range(draft.MAX_RELEASES_PER_PAGE)
                ],
                status=200,
            )
        return super().__call__(request, timeout=timeout)


class ImmutableDraftSnapshotTests(unittest.TestCase):
    def alpha_arguments(
        self,
        assembly: Path,
        notes: Path,
        github: FakeGitHub,
    ) -> dict[str, object]:
        return {
            "repository": "openprose/prose",
            "version": ALPHA_VERSION,
            "source_sha": SOURCE_SHA,
            "control_sha": CONTROL_SHA,
            "token": "test-token",
            "assembly": assembly,
            "release_kind": "functional-alpha",
            "release_notes_path": notes,
            "workflow_run_id": 246813579,
            "workflow_run_attempt": 2,
            "authority_output": (
                assembly.parent.resolve() / "alpha-draft-authority.json"
            ),
            "opener": github,
        }

    def test_functional_alpha_writes_one_closed_canonical_handoff_after_revalidation(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            admitted = write_alpha_assembly(assembly)
            notes = root / "notes.md"
            release_body = write_alpha_notes(notes)
            authority_path = root / "alpha-draft-authority.json"

            class ObservingGitHub(AlphaFakeGitHub):
                def __init__(self) -> None:
                    super().__init__()
                    self.tag_reads = 0

                def __call__(self, request, *, timeout: int):
                    if (
                        request.get_method() == "GET"
                        and "/git/ref/tags/" in urlparse(request.full_url).path
                    ):
                        self.tag_reads += 1
                        self.case.assertFalse(authority_path.exists())
                    return super().__call__(request, timeout=timeout)

            github = ObservingGitHub()
            github.case = self
            result = draft.create_draft_release(
                **self.alpha_arguments(assembly, notes, github)
            )
            encoded = authority_path.read_bytes()
            authority = json.loads(encoded)
            schema = json.loads(
                (
                    draft.CONTROL_ROOT / "cli/release/alpha-draft-authority.schema.json"
                ).read_text("utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(authority)
            self.assertEqual(authority, draft.validate_alpha_draft_authority(authority))

            self.assertEqual(
                (
                    json.dumps(authority, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode(),
                encoded,
            )
            self.assertEqual(0o400, authority_path.stat().st_mode & 0o777)
            self.assertEqual("openprose.alpha-draft-authority/1", authority["schema"])
            self.assertEqual("openprose/prose", authority["repository"])
            self.assertEqual(ALPHA_VERSION, authority["version"])
            self.assertEqual(SOURCE_SHA, authority["sourceSha"])
            self.assertEqual(CONTROL_SHA, authority["controlSha"])
            self.assertEqual(result["releaseId"], authority["releaseId"])
            self.assertEqual("created", authority["outcome"])
            self.assertTrue(authority["draft"])
            self.assertTrue(authority["prerelease"])
            self.assertFalse(authority["publicationAuthorized"])
            self.assertEqual(digest_record(release_body), authority["releaseBody"])
            self.assertEqual(38, authority["assetCount"])
            self.assertEqual(38, len(authority["assets"]))
            self.assertEqual(
                sorted(admitted), [item["name"] for item in authority["assets"]]
            )
            self.assertEqual(
                hashlib.sha256(
                    json.dumps(
                        authority["assets"], sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                authority["assetInventorySha256"],
            )
            serialized = encoded.decode("utf-8")
            self.assertNotIn("test-token", serialized)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn(release_body.decode("utf-8"), serialized)

    def test_functional_alpha_refuses_unsafe_or_reused_handoff_before_network(
        self,
    ) -> None:
        for mutation in (
            "missing",
            "relative",
            "existing",
            "symlink",
            "noncanonical-parent",
        ):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                assembly = root / "assembly"
                assembly.mkdir()
                write_alpha_assembly(assembly)
                notes = root / "notes.md"
                write_alpha_notes(notes)
                github = AlphaFakeGitHub()
                arguments = self.alpha_arguments(assembly, notes, github)
                if mutation == "missing":
                    arguments["authority_output"] = None
                elif mutation == "relative":
                    arguments["authority_output"] = Path("authority.json")
                elif mutation == "existing":
                    output = root / "existing.json"
                    output.write_text("preserve me\n")
                    arguments["authority_output"] = output
                elif mutation == "symlink":
                    target = root / "target.json"
                    target.write_text("preserve me\n")
                    output = root / "authority-link.json"
                    output.symlink_to(target)
                    arguments["authority_output"] = output
                else:
                    real = root / "real"
                    real.mkdir()
                    alias = root / "alias"
                    alias.symlink_to(real, target_is_directory=True)
                    arguments["authority_output"] = alias / "authority.json"
                with self.assertRaisesRegex(
                    draft.DraftReleaseError, "authority output"
                ):
                    draft.create_draft_release(**arguments)
                self.assertEqual([], github.requests)

    def test_functional_alpha_requires_exact_positive_workflow_identity(self) -> None:
        for field, value in (
            ("workflow_run_id", None),
            ("workflow_run_id", 0),
            ("workflow_run_id", True),
            ("workflow_run_attempt", None),
            ("workflow_run_attempt", -1),
        ):
            with self.subTest(
                field=field, value=value
            ), TemporaryDirectory() as directory:
                root = Path(directory)
                assembly = root / "assembly"
                assembly.mkdir()
                write_alpha_assembly(assembly)
                notes = root / "notes.md"
                write_alpha_notes(notes)
                github = AlphaFakeGitHub()
                arguments = self.alpha_arguments(assembly, notes, github)
                arguments[field] = value
                with self.assertRaisesRegex(
                    draft.DraftReleaseError, "positive integer"
                ):
                    draft.create_draft_release(**arguments)
                self.assertEqual([], github.requests)

    def test_cli_workflow_identity_rejects_noncanonical_positive_integers(self) -> None:
        for value in ("0", "01", "+1", " 1", "1 ", "1.0"):
            with self.subTest(value=value), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    draft.parser().parse_args(
                        [
                            "--repository",
                            "openprose/prose",
                            "--version",
                            ALPHA_VERSION,
                            "--source-sha",
                            SOURCE_SHA,
                            "--control-sha",
                            CONTROL_SHA,
                            "--assembly",
                            "/assembly",
                            "--release-kind",
                            "functional-alpha",
                            "--release-notes",
                            "/notes.md",
                            "--workflow-run-id",
                            value,
                            "--workflow-run-attempt",
                            "1",
                            "--authority-output",
                            "/authority.json",
                        ]
                    )

    def test_existing_handoff_is_never_overwritten_after_success(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            write_alpha_assembly(assembly)
            notes = root / "notes.md"
            write_alpha_notes(notes)
            github = AlphaFakeGitHub()
            arguments = self.alpha_arguments(assembly, notes, github)
            draft.create_draft_release(**arguments)
            authority_path = arguments["authority_output"]
            assert isinstance(authority_path, Path)
            before = authority_path.read_bytes()
            request_count = len(github.requests)
            with self.assertRaisesRegex(draft.DraftReleaseError, "authority output"):
                draft.create_draft_release(**arguments)
            self.assertEqual(before, authority_path.read_bytes())
            self.assertEqual(request_count, len(github.requests))

    def test_body_template_evolution_changes_new_digest_without_invalidating_prior_record(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            def create(
                name: str, body: bytes, github: AlphaFakeGitHub
            ) -> dict[str, object]:
                scope = root / name
                scope.mkdir()
                assembly = scope / "assembly"
                assembly.mkdir()
                write_alpha_assembly(assembly)
                notes = scope / "notes.md"
                notes.write_bytes(body)
                arguments = self.alpha_arguments(assembly, notes, github)
                arguments["authority_output"] = scope.resolve() / "authority.json"
                draft.create_draft_release(**arguments)
                return json.loads((scope / "authority.json").read_text("utf-8"))

            original = draft._functional_alpha_release_notes
            first_body = original(ALPHA_VERSION).encode()
            first = create("first", first_body, AlphaFakeGitHub())
            evolved_body = b"# Evolved functional-alpha body\n"
            with patch.object(
                draft,
                "_functional_alpha_release_notes",
                return_value=evolved_body.decode(),
            ):
                self.assertEqual(first, draft.validate_alpha_draft_authority(first))
                second = create("second", evolved_body, AlphaFakeGitHub())
            self.assertNotEqual(first["releaseBody"], second["releaseBody"])
            self.assertEqual(first, draft.validate_alpha_draft_authority(first))

    def test_authority_validator_rejects_unknown_fields_and_inventory_drift(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            write_alpha_assembly(assembly)
            notes = root / "notes.md"
            write_alpha_notes(notes)
            arguments = self.alpha_arguments(assembly, notes, AlphaFakeGitHub())
            draft.create_draft_release(**arguments)
            output = arguments["authority_output"]
            assert isinstance(output, Path)
            authority = json.loads(output.read_text("utf-8"))
            schema = json.loads(
                (
                    draft.CONTROL_ROOT / "cli/release/alpha-draft-authority.schema.json"
                ).read_text("utf-8")
            )
            for mutation in ("unknown", "digest", "asset"):
                with self.subTest(mutation=mutation):
                    changed = json.loads(json.dumps(authority))
                    if mutation == "unknown":
                        changed["token"] = "forbidden"
                    elif mutation == "digest":
                        changed["assetInventorySha256"] = "0" * 64
                    else:
                        changed["assets"][0]["byteLength"] += 1
                    with self.assertRaises(draft.DraftReleaseError):
                        draft.validate_alpha_draft_authority(changed)
                    if mutation == "unknown":
                        with self.assertRaises(ValidationError):
                            Draft202012Validator(schema).validate(changed)

    def test_functional_alpha_cli_summary_is_minimal_and_path_free(self) -> None:
        result = {
            "schema": "openprose.draft-release-result/2",
            "draft": True,
            "prerelease": True,
            "publicationAuthorized": False,
            "tag": f"cli-v{ALPHA_VERSION}",
            "releaseId": 123,
            "resumed": False,
            "assetCount": 38,
            "assets": [{"name": "private-path", "sha256": "a" * 64}],
        }
        stdout = io.StringIO()
        argv = [
            "--repository",
            "openprose/prose",
            "--version",
            ALPHA_VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--control-sha",
            CONTROL_SHA,
            "--assembly",
            "/private/assembly",
            "--release-kind",
            "functional-alpha",
            "--release-notes",
            "/private/notes.md",
            "--workflow-run-id",
            "246813579",
            "--workflow-run-attempt",
            "2",
            "--authority-output",
            "/private/authority.json",
        ]
        with patch.object(
            draft, "create_draft_release", return_value=result
        ), patch.dict(draft.os.environ, {"GITHUB_TOKEN": "token"}), redirect_stdout(
            stdout
        ):
            self.assertEqual(0, draft.main(argv))
        summary = json.loads(stdout.getvalue())
        self.assertEqual(
            {
                "schema": "openprose.alpha-draft-handoff-summary/1",
                "status": "written",
                "tag": f"cli-v{ALPHA_VERSION}",
                "releaseId": 123,
                "outcome": "created",
                "assetCount": 38,
            },
            summary,
        )
        self.assertNotIn("/private", stdout.getvalue())

    def test_functional_alpha_uses_one_immutable_body_and_asset_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            admitted = write_alpha_assembly(assembly)
            notes = root / "notes.md"
            admitted_notes = write_alpha_notes(notes)

            class MutatingGitHub(AlphaFakeGitHub):
                def __init__(self) -> None:
                    super().__init__()
                    self.mutated = False

                def __call__(self, request, *, timeout: int):
                    if not self.mutated:
                        self.mutated = True
                        asset = next(
                            path
                            for path in assembly.iterdir()
                            if path.name != "SHA256SUMS"
                        )
                        asset.unlink()
                        replacement = root / "replacement"
                        replacement.write_bytes(b"substituted bytes\n")
                        asset.symlink_to(replacement)
                        notes.write_text("substituted notes\n", "utf-8")
                    return super().__call__(request, timeout=timeout)

            github = MutatingGitHub()
            result = draft.create_draft_release(
                **self.alpha_arguments(assembly, notes, github)
            )

        release_request = next(
            request
            for request in github.requests
            if request.get_method() == "POST"
            and urlparse(request.full_url).netloc == "api.github.com"
        )
        self.assertEqual(
            admitted_notes.decode("utf-8"), json.loads(release_request.data)["body"]
        )
        uploads = {
            parse_qs(urlparse(request.full_url).query)["name"][0]: request.data
            for request in github.requests
            if request.get_method() == "POST"
            and urlparse(request.full_url).netloc == "uploads.github.com"
        }
        self.assertEqual(admitted, uploads)
        self.assertEqual(len(admitted), result["assetCount"])
        self.assertTrue(result["prerelease"])
        self.assertFalse(result["publicationAuthorized"])

    def test_functional_alpha_reauthenticates_canonical_notes_before_network(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            write_alpha_assembly(assembly)
            notes = root / "notes.md"
            notes.write_text("valid UTF-8 but substituted alpha notes\n", "utf-8")
            github = AlphaFakeGitHub()
            with self.assertRaisesRegex(
                draft.DraftReleaseError, "differ from canonical authority"
            ):
                draft.create_draft_release(
                    **self.alpha_arguments(assembly, notes, github)
                )
            self.assertEqual([], github.requests)

    def test_functional_alpha_reauthenticates_admission_authority_before_network(
        self,
    ) -> None:
        for mutation in (
            "omission",
            "runtime-drift",
            "recipe-substitution",
            "raw-path-secret",
            "nested-absolute-path",
            "secret-environment-name",
            "nested-unknown-key",
            "forged-execution",
            "forged-package",
        ):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                assembly = root / "assembly"
                assembly.mkdir()
                write_alpha_assembly(assembly)
                target = next(iter(draft.ALPHA_TARGET_PLATFORMS))
                admission_path = assembly / f"{target}-alpha-admission.json"
                admission = json.loads(admission_path.read_text("utf-8"))
                if mutation == "omission":
                    admission.pop("adapterManifest")
                elif mutation == "runtime-drift":
                    admission["adapterManifest"]["ompRuntimePrerequisite"][
                        "versionRange"
                    ] = ">=1.3.13"
                elif mutation == "recipe-substitution":
                    admission["adapterManifest"]["ompRecipe"]["sha256"] = "0" * 64
                elif mutation == "raw-path-secret":
                    admission["fixture"]["resolvedPath"] = "/Users/alice/.config/private"
                    admission["fixture"]["token"] = "sk-adversarial-secret"
                elif mutation == "nested-absolute-path":
                    admission["executions"][0]["harnessSelection"]["result"][
                        "path"
                    ] = "/Users/alice/.config/openprose/cli.toml"
                elif mutation == "secret-environment-name":
                    admission["executions"][0]["fixtureObservation"][
                        "environmentNames"
                    ] = ["AWS_SECRET_ACCESS_KEY"]
                elif mutation == "nested-unknown-key":
                    admission["executions"][0]["secret"] = "adversarial"
                elif mutation == "forged-execution":
                    admission["executions"] = [
                        {"adapterId": "forged/adapter", "stdout": "fabricated"}
                    ]
                else:
                    admission["package"] = {
                        "files": [
                            {
                                "path": "/Users/alice/private",
                                "sha256": "0" * 64,
                            }
                        ]
                    }
                rewrite_alpha_admission(admission_path, admission)
                rewrite_checksums(assembly)
                notes = root / "notes.md"
                write_alpha_notes(notes)
                github = AlphaFakeGitHub()
                with self.assertRaisesRegex(
                    draft.DraftReleaseError,
                    "unknown or missing fields|adapter authority differs|deep validation failed",
                ):
                    draft.create_draft_release(
                        **self.alpha_arguments(assembly, notes, github)
                    )
                self.assertEqual([], github.requests)

    def test_functional_alpha_rejects_forged_aggregate_checksum_before_network(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            write_alpha_assembly(assembly)
            notes = root / "notes.md"
            write_alpha_notes(notes)
            sums = (assembly / "SHA256SUMS").read_text("ascii")
            (assembly / "SHA256SUMS").write_text(
                sums.replace(sums[:64], "f" * 64, 1), "ascii"
            )
            github = AlphaFakeGitHub()
            with self.assertRaisesRegex(draft.DraftReleaseError, "digest mismatch"):
                draft.create_draft_release(
                    **self.alpha_arguments(assembly, notes, github)
                )
            self.assertEqual([], github.requests)

    def test_functional_alpha_lost_upload_response_resumes_exactly(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / "assembly"
            assembly.mkdir()
            admitted = write_alpha_assembly(assembly)
            notes = root / "notes.md"
            write_alpha_notes(notes)
            github = AlphaAcceptThenFailUploadGitHub()
            arguments = self.alpha_arguments(assembly, notes, github)
            with self.assertRaisesRegex(draft.DraftReleaseError, "request failed"):
                draft.create_draft_release(**arguments)
            retry = draft.create_draft_release(**arguments)
            self.assertTrue(retry["resumed"])
            self.assertEqual(2, retry["reusedAssetCount"])
            self.assertEqual(len(admitted) - 2, retry["uploadedAssetCount"])
            retry_authority = arguments["authority_output"]
            assert isinstance(retry_authority, Path)
            self.assertEqual(
                "resumed", json.loads(retry_authority.read_text("utf-8"))["outcome"]
            )
            arguments["authority_output"] = (
                root.resolve() / "alpha-draft-authority-complete.json"
            )
            complete = draft.create_draft_release(**arguments)
            self.assertEqual(0, complete["uploadedAssetCount"])
            self.assertEqual(len(admitted), complete["reusedAssetCount"])
            request_count = len(github.requests)
            arguments["authority_output"] = (
                root.resolve() / "alpha-draft-authority-invalid-notes.json"
            )
            notes.write_text("different alpha notes\n", "utf-8")
            with self.assertRaisesRegex(
                draft.DraftReleaseError, "differ from canonical authority"
            ):
                draft.create_draft_release(**arguments)
            self.assertFalse(
                any(
                    request.get_method() == "POST"
                    for request in github.requests[request_count:]
                )
            )

    def test_functional_alpha_absent_moved_tag_and_transport_fail_closed(self) -> None:
        class MissingTag(AlphaFakeGitHub):
            def __call__(self, request, *, timeout: int):
                if request.get_method() == "GET" and "/git/ref/tags/" in urlparse(request.full_url).path:
                    self.requests.append(request)
                    return FakeResponse({}, status=404)
                return super().__call__(request, timeout=timeout)

        class MovedTag(AlphaFakeGitHub):
            def __init__(self) -> None:
                super().__init__()
                self.tag_reads = 0

            def __call__(self, request, *, timeout: int):
                if request.get_method() == "GET" and "/git/ref/tags/" in urlparse(request.full_url).path:
                    self.tag_reads += 1
                    if self.tag_reads > 1:
                        self.requests.append(request)
                        return FakeResponse(
                            {
                                "ref": f"refs/tags/cli-v{ALPHA_VERSION}",
                                "node_id": "fixture-alpha-tag-ref",
                                "url": (
                                    "https://api.github.com/repos/openprose/prose/git/refs/tags/"
                                    f"cli-v{ALPHA_VERSION}"
                                ),
                                "object": git_object("commit", "f" * 40),
                            },
                            status=200,
                        )
                return super().__call__(request, timeout=timeout)

        class TransientDiscovery(AlphaFakeGitHub):
            def __call__(self, request, *, timeout: int):
                if (
                    request.get_method() == "GET"
                    and urlparse(request.full_url).path == "/repos/openprose/prose/releases"
                ):
                    self.requests.append(request)
                    raise URLError("transient release discovery failure")
                return super().__call__(request, timeout=timeout)

        class FailedDiscovery(AlphaFakeGitHub):
            def __init__(self, status: int) -> None:
                super().__init__()
                self.status = status

            def __call__(self, request, *, timeout: int):
                if (
                    request.get_method() == "GET"
                    and urlparse(request.full_url).path == "/repos/openprose/prose/releases"
                ):
                    self.requests.append(request)
                    return FakeResponse({}, status=self.status)
                return super().__call__(request, timeout=timeout)

        for label, github, message in (
            ("missing", MissingTag(), "status or response size: 404"),
            ("moved", MovedTag(), "requested source SHA"),
            ("transient", TransientDiscovery(), "request failed before a response"),
            ("auth", FailedDiscovery(401), "status or response size: 401"),
            ("server", FailedDiscovery(500), "status or response size: 500"),
        ):
            with self.subTest(label=label), TemporaryDirectory() as directory:
                root = Path(directory)
                assembly = root / "assembly"
                assembly.mkdir()
                write_alpha_assembly(assembly)
                notes = root / "notes.md"
                write_alpha_notes(notes)
                with self.assertRaisesRegex(draft.DraftReleaseError, message):
                    draft.create_draft_release(
                        **self.alpha_arguments(assembly, notes, github)
                    )
                if label != "moved":
                    self.assertFalse(
                        any(request.get_method() == "POST" for request in github.requests)
                    )

    def test_github_transport_failures_are_sanitized_draft_errors(self) -> None:
        secret = "Bearer super-secret-token response-body-secret"
        request = Request(
            "https://api.github.com/repos/openprose/prose/git/ref/tags/release",
            headers={"Authorization": secret},
            method="GET",
        )

        def raising(error: Exception):
            def opener(_request, *, timeout: int):
                raise error

            return opener

        class BrokenResponse:
            def __init__(self, phase: str) -> None:
                self.phase = phase
                self.status: object = secret if phase == "status" else 200

            def __enter__(self):
                if self.phase == "enter":
                    raise RuntimeError(secret)
                return self

            def __exit__(self, *_args: object):
                if self.phase == "exit":
                    raise RuntimeError(secret)
                return None

            def read(self, maximum: int):
                if self.phase == "read":
                    raise RuntimeError(secret)
                if self.phase == "body":
                    return secret
                return b"{}"

        def returning(value):
            def opener(_request, *, timeout: int):
                return value

            return opener

        failures = (
            raising(URLError(secret)),
            raising(OSError(secret)),
            raising(TimeoutError(secret)),
            returning(object()),
            returning(BrokenResponse("enter")),
            returning(BrokenResponse("read")),
            returning(BrokenResponse("exit")),
            returning(BrokenResponse("body")),
            returning(BrokenResponse("status")),
        )
        for opener in failures:
            with self.subTest(opener=opener), self.assertRaises(
                draft.DraftReleaseError
            ) as raised:
                draft._json_request(request, expected_status=200, opener=opener)
            self.assertNotIn("super-secret", str(raised.exception))
            self.assertNotIn("response-body", str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)

        http_error = HTTPError(
            request.full_url,
            404,
            secret,
            {},
            io.BytesIO(secret.encode()),
        )
        with self.assertRaises(draft.DraftReleaseError) as raised:
            draft._json_request(
                request,
                expected_status=200,
                opener=raising(http_error),
            )
        self.assertEqual(
            "GitHub API returned unexpected status: 404",
            str(raised.exception),
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_release_tag_must_exist_match_and_have_a_closed_reference(self) -> None:
        malformed_ref = tag_ref(git_object("commit", SOURCE_SHA))
        malformed_ref["unexpected"] = True
        cases = {
            "missing": ScriptedTagGitHub(
                ref={},
                ref_status=404,
            ),
            "mismatch": ScriptedTagGitHub(
                ref=tag_ref(git_object("commit", "f" * 40)),
            ),
            "malformed": ScriptedTagGitHub(ref=malformed_ref),
        }
        for label, github in cases.items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                assembly = Path(directory)
                write_assembly(assembly)
                with self.assertRaises(draft.DraftReleaseError):
                    draft.create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=assembly,
                        opener=github,
                    )
                self.assertEqual(1, len(github.requests))
                self.assertEqual("GET", github.requests[0].get_method())

    def test_annotated_release_tag_chain_is_peeled_before_draft_post(self) -> None:
        first = "a" * 40
        second = "b" * 40
        github = ScriptedTagGitHub(
            ref=tag_ref(git_object("tag", first)),
            tags={
                first: (200, annotated_tag(first, git_object("tag", second))),
                second: (200, annotated_tag(second, git_object("commit", SOURCE_SHA))),
            },
        )
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            draft.create_draft_release(
                repository="openprose/prose",
                version="0.1.0",
                source_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                token="test-token",
                assembly=assembly,
                opener=github,
            )
        self.assertEqual(["GET", "GET", "GET", "GET", "POST"], [
            request.get_method() for request in github.requests[:5]
        ])
        payload = json.loads(github.requests[4].data)
        self.assertEqual(
            "refs/tags/openprose-cli-v0.1.0",
            payload["target_commitish"],
        )

    def test_annotated_release_tag_cycle_depth_and_schema_fail_before_post(self) -> None:
        first = "a" * 40
        second = "b" * 40
        malformed = annotated_tag(first, git_object("commit", SOURCE_SHA))
        malformed["unexpected"] = True

        cycle = ScriptedTagGitHub(
            ref=tag_ref(git_object("tag", first)),
            tags={
                first: (200, annotated_tag(first, git_object("tag", second))),
                second: (200, annotated_tag(second, git_object("tag", first))),
            },
        )
        malformed_schema = ScriptedTagGitHub(
            ref=tag_ref(git_object("tag", first)),
            tags={first: (200, malformed)},
        )
        chain = [f"{index:040x}" for index in range(1, draft.MAX_TAG_PEEL_DEPTH + 2)]
        deep_tags = {
            sha: (200, annotated_tag(sha, git_object("tag", chain[index + 1])))
            for index, sha in enumerate(chain[:-1])
        }
        deep_tags[chain[-1]] = (
            200,
            annotated_tag(chain[-1], git_object("commit", SOURCE_SHA)),
        )
        too_deep = ScriptedTagGitHub(
            ref=tag_ref(git_object("tag", chain[0])),
            tags=deep_tags,
        )

        for label, github in {
            "cycle": cycle,
            "schema": malformed_schema,
            "depth": too_deep,
        }.items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                assembly = Path(directory)
                write_assembly(assembly)
                with self.assertRaises(draft.DraftReleaseError):
                    draft.create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=assembly,
                        opener=github,
                    )
                self.assertTrue(github.requests)
                self.assertTrue(
                    all(request.get_method() == "GET" for request in github.requests)
                )

    def test_partial_upload_failure_is_exactly_resumed_without_duplication(self) -> None:
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            expected = draft.load_assembly(
                assembly, SOURCE_SHA, "0.1.0", "openprose/prose", CONTROL_SHA
            )
            github = AcceptThenFailUploadGitHub()
            arguments = {
                "repository": "openprose/prose",
                "version": "0.1.0",
                "source_sha": SOURCE_SHA,
                "control_sha": CONTROL_SHA,
                "token": "test-token",
                "assembly": assembly,
                "opener": github,
            }

            with self.assertRaisesRegex(
                draft.DraftReleaseError, "request failed before a response"
            ):
                draft.create_draft_release(**arguments)
            self.assertEqual(2, len(github.assets))

            retry = draft.create_draft_release(**arguments)
            self.assertTrue(retry["resumed"])
            self.assertEqual(2, retry["reusedAssetCount"])
            self.assertEqual(len(expected) - 2, retry["uploadedAssetCount"])
            self.assertEqual(len(expected), retry["assetCount"])

            upload_names = [
                parse_qs(urlparse(request.full_url).query)["name"][0]
                for request in github.requests
                if request.get_method() == "POST"
                and urlparse(request.full_url).netloc == "uploads.github.com"
            ]
            self.assertEqual(len(expected), len(upload_names))
            self.assertEqual(len(upload_names), len(set(upload_names)))
            release_posts = [
                request
                for request in github.requests
                if request.get_method() == "POST"
                and urlparse(request.full_url).netloc == "api.github.com"
            ]
            self.assertEqual(1, len(release_posts))

            request_count = len(github.requests)
            complete_retry = draft.create_draft_release(**arguments)
            later_requests = github.requests[request_count:]
            self.assertTrue(complete_retry["resumed"])
            self.assertEqual(len(expected), complete_retry["reusedAssetCount"])
            self.assertEqual(0, complete_retry["uploadedAssetCount"])
            self.assertFalse(
                any(request.get_method() == "POST" for request in later_requests)
            )

    def test_stale_or_mismatched_draft_is_refused_without_remote_mutation(self) -> None:
        mutations = {
            "published": ("draft", False),
            "prerelease": ("prerelease", True),
            "other-source-notes": ("body", "stale release notes"),
        }
        for label, (field, value) in mutations.items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                assembly = Path(directory)
                write_assembly(assembly)
                github = FakeGitHub()
                arguments = {
                    "repository": "openprose/prose",
                    "version": "0.1.0",
                    "source_sha": SOURCE_SHA,
                    "control_sha": CONTROL_SHA,
                    "token": "test-token",
                    "assembly": assembly,
                    "opener": github,
                }
                draft.create_draft_release(**arguments)
                assert github.release is not None
                github.release[field] = value
                preserved_assets = json.loads(json.dumps(github.assets))
                request_count = len(github.requests)

                with self.assertRaisesRegex(
                    draft.DraftReleaseError, "draft release identity"
                ):
                    draft.create_draft_release(**arguments)
                later_requests = github.requests[request_count:]
                self.assertEqual(preserved_assets, github.assets)
                self.assertFalse(
                    any(request.get_method() == "POST" for request in later_requests)
                )

    def test_ambiguous_or_unbounded_draft_discovery_fails_before_creation(self) -> None:
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            arguments = {
                "repository": "openprose/prose",
                "version": "0.1.0",
                "source_sha": SOURCE_SHA,
                "control_sha": CONTROL_SHA,
                "token": "test-token",
                "assembly": assembly,
            }

            ambiguous = FakeGitHub()
            draft.create_draft_release(**arguments, opener=ambiguous)
            ambiguous.release_copies = 2
            request_count = len(ambiguous.requests)
            with self.assertRaisesRegex(draft.DraftReleaseError, "ambiguous"):
                draft.create_draft_release(**arguments, opener=ambiguous)
            self.assertTrue(
                all(
                    request.get_method() == "GET"
                    for request in ambiguous.requests[request_count:]
                )
            )

            saturated = SaturatedReleaseDiscoveryGitHub()
            with self.assertRaisesRegex(draft.DraftReleaseError, "pagination limit"):
                draft.create_draft_release(**arguments, opener=saturated)
            release_lists = [
                request
                for request in saturated.requests
                if urlparse(request.full_url).path
                == "/repos/openprose/prose/releases"
            ]
            self.assertEqual(draft.MAX_RELEASE_DISCOVERY_PAGES, len(release_lists))
            self.assertTrue(
                all(request.get_method() == "GET" for request in saturated.requests)
            )

    def test_substituted_existing_asset_is_refused_without_upload_or_delete(self) -> None:
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            github = FakeGitHub()
            arguments = {
                "repository": "openprose/prose",
                "version": "0.1.0",
                "source_sha": SOURCE_SHA,
                "control_sha": CONTROL_SHA,
                "token": "test-token",
                "assembly": assembly,
                "opener": github,
            }
            draft.create_draft_release(**arguments)
            first_name = next(iter(github.assets))
            github.assets[first_name]["digest"] = "sha256:" + "f" * 64
            preserved_assets = json.loads(json.dumps(github.assets))
            request_count = len(github.requests)

            with self.assertRaisesRegex(
                draft.DraftReleaseError, "does not match the admitted bytes"
            ):
                draft.create_draft_release(**arguments)
            later_requests = github.requests[request_count:]
            self.assertEqual(preserved_assets, github.assets)
            self.assertFalse(
                any(request.get_method() != "GET" for request in later_requests)
            )

    def test_tag_source_is_reauthenticated_after_asset_reconciliation(self) -> None:
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            github = FinalTagMutationGitHub()
            with self.assertRaisesRegex(
                draft.DraftReleaseError, "requested source SHA"
            ):
                draft.create_draft_release(
                    repository="openprose/prose",
                    version="0.1.0",
                    source_sha=SOURCE_SHA,
                    control_sha=CONTROL_SHA,
                    token="test-token",
                    assembly=assembly,
                    opener=github,
                )
            self.assertEqual(2, github.tag_reads)
            self.assertTrue(github.assets)
            self.assertFalse(
                any(
                    request.get_method() in {"PATCH", "PUT", "DELETE"}
                    for request in github.requests
                )
            )

    def test_notes_and_uploads_use_one_admitted_snapshot_and_include_root_checksums(self) -> None:
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            admitted = {path.name: path.read_bytes() for path in assembly.iterdir()}
            github = FakeGitHub()
            original_release_notes = draft._release_notes

            def mutate_source_then_render(assets):
                for target in draft.TARGETS:
                    path = assembly / f"{target}-release-manifest.json"
                    value = json.loads(path.read_text("utf-8"))
                    value["source"]["revision"] = "c" * 40
                    rewrite_json(path, value)
                rewrite_checksums(assembly)
                return original_release_notes(assets)

            draft._release_notes = mutate_source_then_render
            try:
                result = draft.create_draft_release(
                    repository="openprose/prose",
                    version="0.1.0",
                    source_sha=SOURCE_SHA,
                    control_sha=CONTROL_SHA,
                    token="test-token",
                    assembly=assembly,
                    opener=github,
                )
            finally:
                draft._release_notes = original_release_notes

        release_request = next(
            request
            for request in github.requests
            if request.get_method() == "POST"
            and urlparse(request.full_url).netloc == "api.github.com"
        )
        payload = json.loads(release_request.data)
        self.assertIn(SOURCE_SHA, payload["body"])
        self.assertNotIn("c" * 40, payload["body"])
        uploads = {
            parse_qs(urlparse(request.full_url).query)["name"][0]: request.data
            for request in github.requests
            if request.get_method() == "POST"
            and urlparse(request.full_url).netloc == "uploads.github.com"
        }
        self.assertEqual(set(admitted), set(uploads))
        self.assertEqual(admitted, uploads)
        self.assertEqual(len(admitted), result["assetCount"])
        self.assertIn("SHA256SUMS", uploads)

    def test_draft_dependency_integrity_records_are_closed(self) -> None:
        with TemporaryDirectory() as directory:
            assembly = Path(directory)
            write_assembly(assembly)
            report = json.loads((assembly / "linux-x64-dependency-evidence.json").read_text())

        declared = json.loads(json.dumps(report))
        declared["inventories"]["cargo"]["packages"][0]["integrity"]["unexpected"] = True
        with self.assertRaisesRegex(draft.DraftReleaseError, "integrity"):
            draft._dependency_components(declared, "linux-x64")

        not_applicable = json.loads(json.dumps(report))
        package = not_applicable["inventories"]["bun"]["packages"][0]
        package["integrity"] = {"status": "not-applicable", "reason": "invented"}
        with self.assertRaisesRegex(draft.DraftReleaseError, "integrity"):
            draft._dependency_components(not_applicable, "linux-x64")

    def test_protected_dependency_omission_binding_and_substitution_fail_before_network(self) -> None:
        mutations = ("omission", "admission-digest", "all-target-substitution")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                assembly = Path(directory)
                write_assembly(assembly)
                protected = assembly / "protected-dependency-evidence.json"
                admission_path = assembly / "profile-admission.json"
                if mutation == "omission":
                    protected.unlink()
                elif mutation == "admission-digest":
                    admission = json.loads(admission_path.read_text("utf-8"))
                    admission["dependencyEvidence"]["sha256"] = "0" * 64
                    rewrite_json(admission_path, admission)
                else:
                    for target in draft.TARGETS:
                        evidence_path = assembly / f"{target}-dependency-evidence.json"
                        manifest_path = assembly / f"{target}-release-manifest.json"
                        sbom_path = assembly / f"{target}-sbom.cdx.json"
                        provenance_path = assembly / f"{target}-provenance.json"
                        report = json.loads(evidence_path.read_text("utf-8"))
                        report["inventories"]["cargo"]["packages"][0]["name"] = (
                            "substituted-dependency"
                        )
                        encoded = (
                            json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
                        ).encode()
                        digest = hashlib.sha256(encoded).hexdigest()
                        evidence_path.write_bytes(encoded)
                        manifest = json.loads(manifest_path.read_text("utf-8"))
                        manifest["dependencyEvidence"].update(
                            {"byteLength": len(encoded), "sha256": digest}
                        )
                        rewrite_json(manifest_path, manifest)
                        sbom = json.loads(sbom_path.read_text("utf-8"))
                        sbom["components"] = [
                            component
                            for component in sbom["components"]
                            if {"name": "openprose:kind", "value": "resolved-dependency"}
                            not in component.get("properties", [])
                        ] + draft._dependency_components(report, target)
                        for item in sbom["properties"]:
                            if item.get("name") == "openprose:dependency-evidence-sha256":
                                item["value"] = digest
                        rewrite_json(sbom_path, sbom)
                        provenance = json.loads(provenance_path.read_text("utf-8"))
                        for item in provenance["predicate"]["buildDefinition"][
                            "resolvedDependencies"
                        ]:
                            if item.get("uri") == "openprose:dependency-evidence":
                                item["digest"]["sha256"] = digest
                        rewrite_json(provenance_path, provenance)
                rewrite_checksums(assembly)
                github = FakeGitHub()
                with self.assertRaises(draft.DraftReleaseError):
                    draft.create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=assembly,
                        opener=github,
                    )
                self.assertEqual([], github.requests)

    def test_protected_authority_omission_substitution_and_mutation_fail_before_network(
        self,
    ) -> None:
        mutations = (
            "omit-run",
            "omit-provenance",
            "omit-canonical",
            "omit-evidence",
            "substitution",
            "producer-repository",
            "producer-run",
            "producer-environment",
            "canonical-source",
            "evidence-check",
            "preflight-authority",
            "preflight-image",
            "preflight-placeholder-purpose",
            "package-gates",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                assembly = Path(directory)
                write_assembly(assembly)
                canonical_path = assembly / "canonical-profile-attestation.json"
                evidence_path = assembly / "release-evidence-attestation.json"
                metadata_path = assembly / "protected-authority-run.json"
                provenance_path = assembly / "authority-provenance.json"
                preflight_path = assembly / "profile-preflight.json"
                if mutation == "omit-run":
                    metadata_path.unlink()
                elif mutation == "omit-provenance":
                    provenance_path.unlink()
                elif mutation == "omit-canonical":
                    canonical_path.unlink()
                elif mutation == "omit-evidence":
                    evidence_path.unlink()
                elif mutation == "substitution":
                    canonical_path.write_bytes(evidence_path.read_bytes())
                elif mutation == "producer-repository":
                    pass
                elif mutation == "producer-run":
                    metadata = json.loads(metadata_path.read_text("utf-8"))
                    metadata["runId"] += 1
                    rewrite_json(metadata_path, metadata)
                elif mutation == "producer-environment":
                    provenance = json.loads(provenance_path.read_text("utf-8"))
                    provenance["producer"]["environment"] = "unprotected"
                    rewrite_json(provenance_path, provenance)
                elif mutation == "canonical-source":
                    canonical = json.loads(canonical_path.read_text("utf-8"))
                    canonical["sourceSha"] = "f" * 40
                    rewrite_json(canonical_path, canonical)
                elif mutation == "evidence-check":
                    evidence = json.loads(evidence_path.read_text("utf-8"))
                    evidence["checks"]["benchmarkTrust"] = "unknown"
                    rewrite_json(evidence_path, evidence)
                elif mutation == "preflight-authority":
                    preflight = json.loads(preflight_path.read_text("utf-8"))
                    preflight["protectedAuthority"]["artifactId"] = "1"
                    rewrite_json(preflight_path, preflight)
                elif mutation == "preflight-image":
                    preflight = json.loads(preflight_path.read_text("utf-8"))
                    preflight["image"]["imageSha256"] = "0" * 64
                    rewrite_json(preflight_path, preflight)
                elif mutation == "preflight-placeholder-purpose":
                    preflight = json.loads(preflight_path.read_text("utf-8"))
                    preflight["image"]["purpose"] = "functional-alpha-placeholder"
                    rewrite_json(preflight_path, preflight)
                else:
                    for target in draft.TARGETS:
                        manifest_path = assembly / f"{target}-release-manifest.json"
                        manifest = json.loads(manifest_path.read_text("utf-8"))
                        manifest["externalGates"]["canonicalProfile"]["sha256"] = (
                            "0" * 64
                        )
                        rewrite_json(manifest_path, manifest)
                rewrite_checksums(assembly)
                github = FakeGitHub()
                with self.assertRaises(draft.DraftReleaseError):
                    draft.create_draft_release(
                        repository=(
                            "someone/else"
                            if mutation == "producer-repository"
                            else "openprose/prose"
                        ),
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=assembly,
                        opener=github,
                    )
                self.assertEqual([], github.requests)


if __name__ == "__main__":
    unittest.main(verbosity=2)
