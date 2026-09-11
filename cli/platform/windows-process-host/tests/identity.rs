use openprose_windows_process_host::{
    COMPONENT_NAME, COMPONENT_VERSION, CONTROL_SCHEMA, ERROR_SCHEMA, EVENT_SCHEMA, IDENTITY_SCHEMA,
    MAX_CHILD_STDIN_BYTES, MAX_COMMAND_LINE_UTF16_UNITS, MAX_CONTROL_BYTES, MAX_REQUEST_BYTES,
    REQUEST_SCHEMA,
};
use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;

fn host_executable() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_openprose-windows-process-host"))
}

#[test]
fn version_is_stable_and_diagnostic_free() {
    let output = Command::new(host_executable())
        .arg("--version")
        .output()
        .expect("run version probe");
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("{COMPONENT_NAME} {COMPONENT_VERSION}\n")
    );
}

#[test]
fn identity_is_closed_machine_readable_and_does_not_overclaim_native_evidence() {
    let output = Command::new(host_executable())
        .arg("--identity-json")
        .output()
        .expect("run identity probe");
    assert!(output.status.success());
    assert!(output.stderr.is_empty());
    let identity: Value = serde_json::from_slice(&output.stdout).expect("identity JSON");
    assert_eq!(identity["schema"], IDENTITY_SCHEMA);
    assert_eq!(identity["component"], COMPONENT_NAME);
    assert_eq!(identity["componentVersion"], COMPONENT_VERSION);
    assert_eq!(identity["protocol"]["request"], REQUEST_SCHEMA);
    assert_eq!(identity["protocol"]["control"], CONTROL_SCHEMA);
    assert_eq!(identity["protocol"]["event"], EVENT_SCHEMA);
    assert_eq!(identity["protocol"]["error"], ERROR_SCHEMA);
    assert_eq!(identity["protocol"]["maxRequestBytes"], MAX_REQUEST_BYTES);
    assert_eq!(identity["protocol"]["maxControlBytes"], MAX_CONTROL_BYTES);
    assert_eq!(
        identity["protocol"]["limits"]["maxDecodedStdinBytes"],
        MAX_CHILD_STDIN_BYTES
    );
    assert_eq!(
        identity["protocol"]["limits"]["maxRenderedCommandLineUtf16Units"],
        MAX_COMMAND_LINE_UTF16_UNITS
    );
    assert_eq!(identity["claims"]["providerCallsMade"], false);
    assert_eq!(identity["claims"]["nativeWindowsRuntimeEvidence"], false);
    assert_eq!(identity["claims"]["strictWindowsContainmentReady"], false);
}

#[test]
fn unknown_cli_argument_fails_with_one_bounded_machine_error() {
    let output = Command::new(host_executable())
        .arg("--unknown")
        .output()
        .expect("run invalid probe");
    assert_eq!(output.status.code(), Some(64));
    assert!(output.stdout.is_empty());
    assert!(output.stderr.len() < 1024);
    let error: Value = serde_json::from_slice(&output.stderr).expect("error JSON");
    assert_eq!(error["schema"], ERROR_SCHEMA);
    assert_eq!(error["code"], "CLI_ARGUMENT_INVALID");
    assert_eq!(error["win32"], Value::Null);
}
