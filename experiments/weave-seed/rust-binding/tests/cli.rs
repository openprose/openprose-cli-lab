use std::{io::Write, process::{Command, Stdio}};
fn invoke(input: &[u8]) -> std::process::Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_weave-file-binding-experiment"))
        .env_clear().stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    child.stdin.take().unwrap().write_all(input).unwrap();
    child.wait_with_output().unwrap()
}
#[test]
fn structured_gap_and_invalid_requests() {
    let root = std::env::temp_dir().canonicalize().unwrap();
    let request = serde_json::json!({"root":root,"kernel":"weave-binding-definitely-missing/kernel","contracts":["missing"],"evidence":["missing"],"policy":"test","now":0});
    let result = invoke(request.to_string().as_bytes());
    assert!(result.status.success()); assert!(result.stderr.is_empty());
    let value: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(value["evidence"]["gap"], true); assert_eq!(value["evidence"]["observedAt"], 0);
    assert_eq!(value["binding"].as_str().unwrap().len(), 64);
    for input in [b"not-json".as_slice(), b"\xff", b"{}"] {
        let result = invoke(input); assert!(!result.status.success()); assert!(result.stdout.is_empty()); assert!(!result.stderr.is_empty());
    }
    let mut invalid=request.clone(); invalid["now"]=serde_json::Value::Null;
    assert!(!invoke(invalid.to_string().as_bytes()).status.success());
}
#[test]
fn request_size_cap() {
    // Whitespace is valid framing but cannot exceed the observer's transport bound.
    let mut input=vec![b' ';1_048_577];input.extend_from_slice(b"{}");
    let result=invoke(&input);assert!(!result.status.success());assert!(result.stdout.is_empty());
    assert!(String::from_utf8(result.stderr).unwrap().contains("exceeds 1 MiB"));
}
