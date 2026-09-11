use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use tempfile::TempDir;

const OVERRIDE: &str = "0.1.0-alpha.1";

fn workspace() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn build(target: &Path, version: Option<&str>) -> Output {
    let mut command = Command::new("cargo");
    command
        .args([
            "build",
            "--manifest-path",
            workspace().join("Cargo.toml").to_str().unwrap(),
            "--locked",
            "--offline",
            "-p",
            "prose-cli",
            "--bin",
            "prose",
        ])
        .env("CARGO_TARGET_DIR", target)
        .env_remove("OPENPROSE_BUILD_VERSION");
    if let Some(version) = version {
        command.env("OPENPROSE_BUILD_VERSION", version);
    }
    command.output().unwrap()
}

#[test]
fn build_version_override_is_exact_and_invalid_semver_fails_the_build() {
    let temporary = TempDir::new().unwrap();
    let target = temporary.path().join("target");
    let default_build = build(&target, None);
    assert!(
        default_build.status.success(),
        "{}",
        String::from_utf8_lossy(&default_build.stderr)
    );
    let executable = target.join(if cfg!(windows) {
        "debug/prose.exe"
    } else {
        "debug/prose"
    });
    let default_version = Command::new(&executable).arg("--version").output().unwrap();
    assert_eq!(
        default_version.stdout,
        format!("prose {} (rust)\n", env!("CARGO_PKG_VERSION")).as_bytes()
    );

    let built = build(&target, Some(OVERRIDE));
    assert!(
        built.status.success(),
        "{}",
        String::from_utf8_lossy(&built.stderr)
    );
    let version = Command::new(&executable).arg("--version").output().unwrap();
    assert!(version.status.success());
    assert_eq!(
        version.stdout,
        format!("prose {OVERRIDE} (rust)\n").as_bytes()
    );

    let home = temporary.path().join("home");
    let xdg = home.join("xdg");
    fs::create_dir_all(&xdg).unwrap();
    let doctor = Command::new(&executable)
        .args(["--output", "json", "cli", "doctor"])
        .current_dir(temporary.path())
        .env_clear()
        .env("HOME", &home)
        .env("XDG_CONFIG_HOME", &xdg)
        .env("LANG", "C.UTF-8")
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&doctor.stdout).unwrap();
    assert_eq!(report["runner"]["version"], OVERRIDE);

    let rejected = build(&target, Some("01.0.0"));
    assert!(!rejected.status.success());
    assert!(
        String::from_utf8_lossy(&rejected.stderr)
            .contains("OPENPROSE_BUILD_VERSION must be valid SemVer"),
        "{}",
        String::from_utf8_lossy(&rejected.stderr)
    );
}
