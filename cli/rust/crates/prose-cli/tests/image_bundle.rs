use prose_runner_core::image::sha256_hex;
use serde_json::{Value, json};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use tempfile::TempDir;

const SYNTHETIC_AGGREGATE: &str =
    "04a8fd447aea030a8839e17957ea04f2eb5d0216630b439a4b9fb0e8d36dab33";
const SYNTHETIC_MODEL_VISIBLE: &str =
    "ec42270e03e7163904e5a02d46351e1e543a3cac0ddcd42ecc1885c1561be3d6";

fn put(root: &Path, path: &str, bytes: &[u8]) {
    let target = root.join(path);
    fs::create_dir_all(target.parent().unwrap()).unwrap();
    fs::write(target, bytes).unwrap();
}

struct SyntheticImage {
    aggregate_sha256: String,
    model_visible_sha256: String,
}

fn manifest(
    payload: &[Value],
    aggregate_sha256: &str,
    model_visible_sha256: &str,
    model_visible_byte_length: usize,
    artifacts: &[(&str, Vec<u8>); 3],
) -> Value {
    json!({
        "schema": "openprose.skill-runtime-image-manifest/1",
        "imageFormatVersion": "openprose.skill-runtime-image/1",
        "imageVersion": "synthetic-four-v1",
        "languageVersion": "synthetic",
        "skillVersion": "synthetic",
        "runtimeContractVersion": "synthetic",
        "semanticSourceRevision": "synthetic",
        "purpose": "canonical-language-runtime",
        "releaseEligible": false,
        "normalization": {
            "encoding": "utf-8",
            "newlines": "lf",
            "byteOrderMark": "forbidden",
            "pathSeparator": "/",
        },
        "payload": payload,
        "modelVisibleBytes": {
            "serialization": "ordered-raw-concatenation-v1",
            "byteLength": model_visible_byte_length,
            "sha256": model_visible_sha256,
        },
        "aggregateSha256": {
            "algorithm": "sha256-path-length-nul-v1",
            "sha256": aggregate_sha256,
        },
        "instructionPlacements": [{
            "id": "developer",
            "strictness": "strict",
            "preservesHarnessBasePrompt": true,
        }],
        "taskEnvelope": {
            "schemaId": "openprose.task-envelope/1",
            "path": artifacts[0].0,
            "sha256": sha256_hex(&artifacts[0].1),
        },
        "oneFieldFraming": {
            "id": "openprose.one-field-framing/1",
            "path": artifacts[1].0,
            "sha256": sha256_hex(&artifacts[1].1),
        },
        "terminalEnvelope": {
            "schemaId": "openprose.sentinel-terminal-envelope/1",
            "path": artifacts[2].0,
            "sha256": sha256_hex(&artifacts[2].1),
        },
        "minimumTransportRequirements": {
            "nonInteractive": "required",
            "structuredOutput": "required",
            "ambientIsolation": "required",
            "terminalEnvelope": "required",
            "boundedStreaming": "required",
        },
    })
}

fn synthetic_image(root: &Path) -> SyntheticImage {
    let payloads: [(&str, &[u8]); 4] = [
        ("payload/first.md", b"first\n"),
        ("payload/nested/second.md", "second snowman ☃\n".as_bytes()),
        ("payload/third.txt", b"third\n"),
        ("payload/fourth.prose", b"fourth\n"),
    ];
    let cli_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../");
    let sentinel_contracts = cli_root.join("shared/image/sentinel-v1/contracts");
    let artifacts = [
        (
            "contracts/renamed-task.json",
            fs::read(sentinel_contracts.join("task-envelope.schema.json")).unwrap(),
        ),
        (
            "contracts/renamed-frame.txt",
            fs::read(sentinel_contracts.join("one-field-framing.txt")).unwrap(),
        ),
        (
            "contracts/renamed-terminal.json",
            fs::read(sentinel_contracts.join("terminal-envelope.schema.json")).unwrap(),
        ),
    ];
    for (path, bytes) in payloads {
        put(root, path, bytes);
    }
    for (path, bytes) in &artifacts {
        put(root, path, bytes);
    }

    let mut aggregate = Vec::new();
    let mut model_visible = Vec::new();
    let payload = payloads
        .iter()
        .map(|(path, bytes)| {
            aggregate.extend_from_slice(path.as_bytes());
            aggregate.push(0);
            aggregate.extend_from_slice(bytes.len().to_string().as_bytes());
            aggregate.push(0);
            aggregate.extend_from_slice(bytes);
            aggregate.push(0);
            model_visible.extend_from_slice(bytes);
            json!({
                "path": path,
                "mediaType": "text/markdown; charset=utf-8",
                "byteLength": bytes.len(),
                "sha256": sha256_hex(bytes),
            })
        })
        .collect::<Vec<_>>();
    let aggregate_sha256 = sha256_hex(&aggregate);
    let model_visible_sha256 = sha256_hex(&model_visible);
    let manifest = manifest(
        &payload,
        &aggregate_sha256,
        &model_visible_sha256,
        model_visible.len(),
        &artifacts,
    );
    let mut manifest_bytes = serde_json::to_vec_pretty(&manifest).unwrap();
    manifest_bytes.push(b'\n');
    put(root, "manifest.json", &manifest_bytes);
    SyntheticImage {
        aggregate_sha256,
        model_visible_sha256,
    }
}

fn assert_custom_image_blocks_mock(
    executable: &Path,
    working_directory: &Path,
    home: &Path,
    xdg: &Path,
    observation: &Path,
    expected: &SyntheticImage,
) {
    let listed = Command::new(executable)
        .args(["--output", "json", "cli", "harness", "list"])
        .current_dir(working_directory)
        .env_clear()
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", xdg)
        .env("LANG", "C.UTF-8")
        .output()
        .unwrap();
    assert!(listed.status.success());
    let inventory: Value = serde_json::from_slice(&listed.stdout).unwrap();
    let listed_mock = inventory["harnesses"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["id"] == "mock")
        .unwrap();
    assert_eq!(listed_mock["availability"], "blocked");
    assert_eq!(listed_mock["detectedVersion"], Value::Null);
    assert_eq!(listed_mock["strictWrapperConformant"], false);
    assert_eq!(listed_mock["admissionBlock"], "release-image");

    let diagnosed = Command::new(executable)
        .args(["--harness", "mock", "--output", "json", "cli", "doctor"])
        .current_dir(working_directory)
        .env_clear()
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", xdg)
        .env("LANG", "C.UTF-8")
        .output()
        .unwrap();
    assert_eq!(diagnosed.status.code(), Some(10));
    let report: Value = serde_json::from_slice(&diagnosed.stdout).unwrap();
    assert_eq!(report["image"]["sha256"], expected.aggregate_sha256);
    assert_eq!(report["image"]["version"], "synthetic-four-v1");
    assert_eq!(report["ready"], false);
    assert_eq!(report["selectedHarnessVersion"], Value::Null);
    assert_eq!(report["problems"][0]["code"], "HARNESS_UNAVAILABLE");
    let mock = report["harnesses"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["id"] == "mock")
        .unwrap();
    assert_eq!(mock["availability"], "blocked");
    assert_eq!(mock["admissionBlock"], "release-image");

    let executed = Command::new(executable)
        .args([
            "--harness",
            "mock",
            "--transport",
            "fake-process",
            "--output",
            "json",
            "run",
            "portable.prose.md",
        ])
        .current_dir(working_directory)
        .env_clear()
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", xdg)
        .env("LANG", "C.UTF-8")
        .env("PATH", std::env::var_os("PATH").unwrap_or_default())
        .env(
            "OPENPROSE_CONFORMANCE_FAKE_HARNESS",
            cli_root().join("conformance/fake-harness/fake_harness.py"),
        )
        .env("OPENPROSE_CONFORMANCE_FAKE_SCENARIO", "success")
        .env("OPENPROSE_CONFORMANCE_FAKE_OBSERVATION", observation)
        .output()
        .unwrap();
    assert_eq!(executed.status.code(), Some(10));
    let result: Value = serde_json::from_slice(&executed.stdout).unwrap();
    assert_eq!(result["languageImage"]["sha256"], expected.aggregate_sha256);
    assert_eq!(result["error"]["code"], "HARNESS_UNAVAILABLE");
    assert_eq!(result["digests"]["deliveredImageSha256"], Value::Null);
    assert!(!observation.exists());
}

fn cli_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../")
}

fn generator() -> PathBuf {
    cli_root().join("shared/image/bundle/image_bundle.py")
}

fn cargo_build(
    workspace: &Path,
    target: &Path,
    image: &Path,
    bundle: &Path,
    checksum: &Path,
) -> Output {
    cargo_build_with_release_gate(workspace, target, image, bundle, checksum, None)
}

fn cargo_build_with_release_gate(
    workspace: &Path,
    target: &Path,
    image: &Path,
    bundle: &Path,
    checksum: &Path,
    release_gate: Option<&str>,
) -> Output {
    let mut command = Command::new("cargo");
    command
        .args([
            "build",
            "--manifest-path",
            workspace.join("Cargo.toml").to_str().unwrap(),
            "--locked",
            "--offline",
            "-p",
            "prose-cli",
            "--bin",
            "prose",
            "--features",
            "prose-cli/test-seams",
        ])
        .env("CARGO_TARGET_DIR", target)
        .env("OPENPROSE_IMAGE_SOURCE_DIR", image)
        .env("OPENPROSE_IMAGE_BUNDLE", bundle)
        .env("OPENPROSE_IMAGE_BUNDLE_CHECKSUM", checksum)
        .env_remove("OPENPROSE_REQUIRE_RELEASE_IMAGE");
    if let Some(value) = release_gate {
        command.env("OPENPROSE_REQUIRE_RELEASE_IMAGE", value);
    }
    command.output().unwrap()
}

#[test]
fn release_admission_build_rejects_the_sentinel_image() {
    let temporary = TempDir::new().unwrap();
    let cli = cli_root();
    let image = cli.join("shared/image/sentinel-v1");
    let bundle = cli.join("shared/image/embedded/current.bundle.bin");
    let checksum = cli.join("shared/image/embedded/current.bundle.sha256");
    let rejected = cargo_build_with_release_gate(
        &cli.join("rust"),
        &temporary.path().join("target"),
        &image,
        &bundle,
        &checksum,
        Some("1"),
    );

    assert!(!rejected.status.success());
    assert!(
        String::from_utf8_lossy(&rejected.stderr).contains("image is not release eligible"),
        "{}",
        String::from_utf8_lossy(&rejected.stderr)
    );
}

#[test]
fn release_admission_build_rejects_ambiguous_gate_values() {
    let temporary = TempDir::new().unwrap();
    let cli = cli_root();
    let rejected = cargo_build_with_release_gate(
        &cli.join("rust"),
        &temporary.path().join("target"),
        &cli.join("shared/image/sentinel-v1"),
        &cli.join("shared/image/embedded/current.bundle.bin"),
        &cli.join("shared/image/embedded/current.bundle.sha256"),
        Some("true"),
    );

    assert!(!rejected.status.success());
    assert!(
        String::from_utf8_lossy(&rejected.stderr)
            .contains("OPENPROSE_REQUIRE_RELEASE_IMAGE must be unset or exactly 1"),
        "{}",
        String::from_utf8_lossy(&rejected.stderr)
    );
}

#[test]
fn arbitrary_four_payload_bundle_builds_reports_identity_and_tamper_fails_closed() {
    let temporary = TempDir::new().unwrap();
    let image = temporary.path().join("image");
    let bundle = temporary.path().join("custom.bundle.bin");
    let checksum = temporary.path().join("custom.bundle.sha256");
    let target = temporary.path().join("target");
    let observation = temporary.path().join("observation.json");
    fs::create_dir(&image).unwrap();
    let expected = synthetic_image(&image);
    assert_eq!(expected.aggregate_sha256, SYNTHETIC_AGGREGATE);
    assert_eq!(expected.model_visible_sha256, SYNTHETIC_MODEL_VISIBLE);

    let generated = Command::new("python3")
        .arg(generator())
        .args(["build", image.to_str().unwrap(), bundle.to_str().unwrap()])
        .args(["--checksum", checksum.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(
        generated.status.success(),
        "{}",
        String::from_utf8_lossy(&generated.stderr)
    );

    let workspace = cli_root().join("rust");
    let built = cargo_build(&workspace, &target, &image, &bundle, &checksum);
    assert!(
        built.status.success(),
        "{}",
        String::from_utf8_lossy(&built.stderr)
    );
    let home = temporary.path().join("home");
    let xdg = home.join("xdg");
    fs::create_dir_all(&xdg).unwrap();
    let executable = target.join("debug/prose");
    assert_custom_image_blocks_mock(
        &executable,
        temporary.path(),
        &home,
        &xdg,
        &observation,
        &expected,
    );

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;

        let loop_path = image.join("directory-loop");
        symlink(".", &loop_path).unwrap();
        let rejected = cargo_build(&workspace, &target, &image, &bundle, &checksum);
        assert!(!rejected.status.success());
        assert!(String::from_utf8_lossy(&rejected.stderr).contains("symlink"));
        fs::remove_file(&loop_path).unwrap();
    }

    let mut tampered = fs::read(&bundle).unwrap();
    tampered.push(1);
    fs::write(&bundle, &tampered).unwrap();
    fs::write(&checksum, format!("{}\n", sha256_hex(&tampered))).unwrap();
    let rejected = cargo_build(&workspace, &target, &image, &bundle, &checksum);
    assert!(!rejected.status.success());
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("embedded image bundle drift"));
}
