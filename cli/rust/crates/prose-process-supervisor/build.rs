mod build_support;

use build_support::WindowsHostBuildIdentity;

fn main() {
    println!("cargo:rerun-if-env-changed=OPENPROSE_WINDOWS_HOST_ADMISSION");
    println!("cargo:rerun-if-env-changed=OPENPROSE_WINDOWS_HOST_SHA256");
    let admission = std::env::var("OPENPROSE_WINDOWS_HOST_ADMISSION").ok();
    let digest = std::env::var("OPENPROSE_WINDOWS_HOST_SHA256").ok();
    let identity = match WindowsHostBuildIdentity::resolve(admission.as_deref(), digest.as_deref())
    {
        Ok(identity) => identity,
        Err(message) => panic!("{message}"),
    };
    println!(
        "cargo:rustc-env=OPENPROSE_COMPILED_WINDOWS_HOST_ADMISSION={}",
        u8::from(identity.admitted)
    );
    println!(
        "cargo:rustc-env=OPENPROSE_COMPILED_WINDOWS_HOST_SHA256={}",
        identity.sha256.as_deref().unwrap_or("")
    );
}
