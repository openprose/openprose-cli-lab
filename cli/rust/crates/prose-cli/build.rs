use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

const SOURCE_ENV: &str = "OPENPROSE_IMAGE_SOURCE_DIR";
const BUNDLE_ENV: &str = "OPENPROSE_IMAGE_BUNDLE";
const CHECKSUM_ENV: &str = "OPENPROSE_IMAGE_BUNDLE_CHECKSUM";
const REQUIRE_RELEASE_IMAGE_ENV: &str = "OPENPROSE_REQUIRE_RELEASE_IMAGE";
const TEST_SEAMS_FEATURE_ENV: &str = "CARGO_FEATURE_TEST_SEAMS";

fn main() {
    println!("cargo:rerun-if-env-changed={SOURCE_ENV}");
    println!("cargo:rerun-if-env-changed={BUNDLE_ENV}");
    println!("cargo:rerun-if-env-changed={CHECKSUM_ENV}");
    println!("cargo:rerun-if-env-changed={REQUIRE_RELEASE_IMAGE_ENV}");

    let crate_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let image_dir = crate_dir.join("../../../shared/image");
    let generator = image_dir.join("bundle/image_bundle.py");
    let output_directory = PathBuf::from(env::var_os("OUT_DIR").expect("build output dir"));
    let test_seams_enabled = env::var_os(TEST_SEAMS_FEATURE_ENV).is_some();
    assert!(
        !(test_seams_enabled && env::var("PROFILE").as_deref() == Ok("release")),
        "test-seams cannot be enabled for a release build"
    );
    let default_source = image_dir.join(if test_seams_enabled {
        "sentinel-v1"
    } else {
        "echo-v0"
    });
    let source = selected_path(SOURCE_ENV, &default_source);
    let (bundle, checksum, generated_test_bundle) = match (
        env::var_os(BUNDLE_ENV).map(PathBuf::from),
        env::var_os(CHECKSUM_ENV).map(PathBuf::from),
    ) {
        (Some(bundle), Some(checksum)) => (bundle, checksum, false),
        (None, None) if test_seams_enabled => {
            let bundle = output_directory.join("test-seams.bundle.bin");
            let checksum = output_directory.join("test-seams.bundle.sha256");
            build_bundle(&generator, &source, &bundle, &checksum);
            (bundle, checksum, true)
        }
        (None, None) => (
            image_dir.join("embedded/current.bundle.bin"),
            image_dir.join("embedded/current.bundle.sha256"),
            false,
        ),
        _ => panic!("{BUNDLE_ENV} and {CHECKSUM_ENV} must be set together"),
    };

    println!("cargo:rerun-if-changed={}", generator.display());
    for path in [&bundle, &checksum] {
        if generated_test_bundle {
            continue;
        }
        println!("cargo:rerun-if-changed={}", path.display());
    }
    watch_tree(&source);
    let require_release_image = match env::var_os(REQUIRE_RELEASE_IMAGE_ENV) {
        None => false,
        Some(value) if value == "1" => true,
        Some(value) => panic!(
            "{REQUIRE_RELEASE_IMAGE_ENV} must be unset or exactly 1, got {}",
            value.to_string_lossy()
        ),
    };
    assert!(!(require_release_image && !test_seams_enabled && [SOURCE_ENV, BUNDLE_ENV, CHECKSUM_ENV].iter().all(|key| env::var_os(key).is_none())), "Published kernel startup is not release-qualified; select an explicit image for release qualification");
    check_bundle(
        &generator,
        &source,
        &bundle,
        &checksum,
        require_release_image,
    );

    let output = output_directory.join("current.bundle.bin");
    fs::copy(&bundle, &output).unwrap_or_else(|error| {
        panic!(
            "cannot stage verified embedded image {} to {}: {error}",
            bundle.display(),
            output.display()
        )
    });
}

fn build_bundle(generator: &Path, source: &Path, bundle: &Path, checksum: &Path) {
    let output = Command::new("python3")
        .arg(generator)
        .arg("build")
        .arg(source)
        .arg(bundle)
        .arg("--checksum")
        .arg(checksum)
        .output()
        .unwrap_or_else(|error| {
            panic!(
                "cannot execute stdlib image bundle generator {}: {error}",
                generator.display()
            )
        });
    assert!(
        output.status.success(),
        "test-seams image bundle generation failed (status {}): {}{}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn selected_path(name: &str, fallback: &Path) -> PathBuf {
    env::var_os(name).map_or_else(|| fallback.to_path_buf(), PathBuf::from)
}

fn watch_tree(path: &Path) {
    println!("cargo:rerun-if-changed={}", path.display());
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    let mut entries = entries
        .map(|entry| entry.expect("cannot inspect image source").path())
        .collect::<Vec<_>>();
    entries.sort();
    for entry in entries {
        let metadata = fs::symlink_metadata(&entry).expect("cannot inspect image source entry");
        if metadata.file_type().is_symlink() {
            println!("cargo:rerun-if-changed={}", entry.display());
        } else if metadata.is_dir() {
            watch_tree(&entry);
        } else {
            println!("cargo:rerun-if-changed={}", entry.display());
        }
    }
}

fn check_bundle(
    generator: &Path,
    source: &Path,
    bundle: &Path,
    checksum: &Path,
    require_release_image: bool,
) {
    let mut command = Command::new("python3");
    command
        .arg(generator)
        .arg("check")
        .arg(source)
        .arg(bundle)
        .arg("--checksum")
        .arg(checksum);
    if require_release_image {
        command.arg("--require-release-eligible");
    }
    let output = command.output().unwrap_or_else(|error| {
        panic!(
            "cannot execute stdlib image bundle validator {}: {error}",
            generator.display()
        )
    });
    assert!(
        output.status.success(),
        "embedded image bundle drift/validation failed (status {}): {}{}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}
