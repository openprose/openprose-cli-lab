#[cfg(not(windows))]
use prose_process_supervisor::windows_host_execution_admitted;
use prose_process_supervisor::{
    WINDOWS_PROCESS_HOST_FILENAME, WindowsHostBuildIdentity, compiled_windows_host_identity,
    validate_windows_host_identity, verify_windows_host_sibling,
};
use serde_json::{Value, json};
use std::fs;

fn identity() -> Value {
    json!({
        "schema":"openprose.windows-process-host.identity/1",
        "component":"openprose-windows-process-host",
        "componentVersion":"0.1.0",
        "target":{
            "os":"windows",
            "architecture":"x86_64",
            "nativeWindowsImplementation":true
        },
        "protocol":{
            "request":"openprose.windows-process-host.request/1",
            "control":"openprose.windows-process-host.control/1",
            "event":"openprose.windows-process-host.event/1",
            "error":"openprose.windows-process-host.error/1",
            "maxRequestBytes":67_108_864,
            "maxControlBytes":4096,
            "limits":{
                "maxDecodedStdinBytes":16_777_215,
                "maxStdinBase64Characters":22_369_620,
                "maxArgvItems":64,
                "maxArgumentCharacters":32766,
                "maxEnvironmentItems":64,
                "maxEnvironmentNameCharacters":128,
                "maxEnvironmentValueCharacters":8192,
                "maxPathCharacters":32766,
                "maxEnvironmentBlockBytes":4_194_304,
                "maxRenderedCommandLineUtf16Units":32766
            }
        },
        "claims":{
            "providerCallsMade":false,
            "nativeWindowsRuntimeEvidence":false,
            "strictWindowsContainmentReady":false
        }
    })
}

#[test]
fn compiled_identity_is_closed_and_never_promotes_without_both_inputs() {
    let compiled = compiled_windows_host_identity();
    assert!(!compiled.admitted || compiled.sha256.is_some());
    if let Some(digest) = compiled.sha256 {
        assert_eq!(digest.len(), 64);
        assert!(
            digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        );
    }
    assert_eq!(
        WINDOWS_PROCESS_HOST_FILENAME,
        "openprose-windows-process-host.exe"
    );

    assert_eq!(
        WindowsHostBuildIdentity::resolve(None, None).unwrap(),
        WindowsHostBuildIdentity {
            admitted: false,
            sha256: None
        }
    );
    assert_eq!(
        WindowsHostBuildIdentity::resolve(Some("0"), None).unwrap(),
        WindowsHostBuildIdentity {
            admitted: false,
            sha256: None
        }
    );
    assert_eq!(
        WindowsHostBuildIdentity::resolve(
            Some("0"),
            Some("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        )
        .unwrap(),
        WindowsHostBuildIdentity {
            admitted: false,
            sha256: Some(
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad".to_owned()
            )
        }
    );
    assert!(WindowsHostBuildIdentity::resolve(Some("1"), None).is_err());
    assert!(WindowsHostBuildIdentity::resolve(Some("yes"), None).is_err());
    assert!(WindowsHostBuildIdentity::resolve(Some("0"), Some("ABC")).is_err());
}

#[cfg(not(windows))]
#[test]
fn non_windows_build_never_admits_the_windows_product_path() {
    assert!(!windows_host_execution_admitted());
}

#[test]
fn sibling_resolution_hashes_exact_regular_bytes_without_path_search() {
    let root = tempfile::tempdir().unwrap();
    let wrapper = root.path().join("prose.exe");
    let helper = root.path().join(WINDOWS_PROCESS_HOST_FILENAME);
    fs::write(&wrapper, b"wrapper").unwrap();
    fs::write(&helper, b"abc").unwrap();

    let verified = verify_windows_host_sibling(
        &wrapper,
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
    .unwrap();
    assert_eq!(verified.path(), fs::canonicalize(helper).unwrap());
}

#[test]
fn sibling_resolution_rejects_missing_wrong_digest_and_non_regular_candidates() {
    let root = tempfile::tempdir().unwrap();
    let wrapper = root.path().join("prose.exe");
    fs::write(&wrapper, b"wrapper").unwrap();
    assert!(
        verify_windows_host_sibling(
            &wrapper,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        .is_err()
    );
    fs::create_dir(root.path().join(WINDOWS_PROCESS_HOST_FILENAME)).unwrap();
    assert!(
        verify_windows_host_sibling(
            &wrapper,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        .is_err()
    );
}

#[cfg(unix)]
#[test]
fn sibling_resolution_rejects_symlinks_even_when_target_bytes_match() {
    use std::os::unix::fs::symlink;

    let root = tempfile::tempdir().unwrap();
    let wrapper = root.path().join("prose.exe");
    let target = root.path().join("elsewhere.exe");
    let helper = root.path().join(WINDOWS_PROCESS_HOST_FILENAME);
    fs::write(&wrapper, b"wrapper").unwrap();
    fs::write(&target, b"abc").unwrap();
    symlink(target, helper).unwrap();
    assert!(
        verify_windows_host_sibling(
            &wrapper,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        .is_err()
    );
}

#[test]
fn identity_probe_is_closed_and_matches_every_frozen_protocol_cap() {
    let bytes = serde_json::to_vec(&identity()).unwrap();
    let parsed = validate_windows_host_identity(&bytes, 64 * 1024).unwrap();
    assert_eq!(parsed.component_version, "0.1.0");
    assert_eq!(parsed.architecture, "x86_64");
}

#[test]
fn identity_probe_rejects_overclaims_schema_drift_extra_fields_and_size() {
    let mut cases = Vec::new();
    let mut overclaim = identity();
    overclaim["claims"]["strictWindowsContainmentReady"] = json!(true);
    cases.push(overclaim);
    let mut protocol = identity();
    protocol["protocol"]["limits"]["maxDecodedStdinBytes"] = json!(16_777_216);
    cases.push(protocol);
    let mut schema = identity();
    schema["schema"] = json!("openprose.windows-process-host.identity/2");
    cases.push(schema);
    let mut version = identity();
    version["componentVersion"] = json!("0.1.0-");
    cases.push(version);
    let mut extra = identity();
    extra["unexpected"] = json!(true);
    cases.push(extra);
    let mut target = identity();
    target["target"]["nativeWindowsImplementation"] = json!(false);
    cases.push(target);
    let mut non_windows = identity();
    non_windows["target"]["os"] = json!("linux");
    cases.push(non_windows);
    for value in cases {
        assert!(
            validate_windows_host_identity(&serde_json::to_vec(&value).unwrap(), 64 * 1024)
                .is_err()
        );
    }
    assert!(validate_windows_host_identity(&vec![b'x'; 65_537], 64 * 1024).is_err());
}
