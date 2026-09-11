#![cfg(windows)]

use openprose_windows_process_host::{
    CONTROL_SCHEMA, CancellationPolicy, EnvironmentEntry, GracefulControl, HostLimits, HostRequest,
    REQUEST_SCHEMA, decode_base64, encode_base64,
};
use serde_json::{Value, json};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, ExitStatus, Output, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const TEST_TIMEOUT: Duration = Duration::from_secs(15);

fn host_executable() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_openprose-windows-process-host"))
}

fn fixture_executable() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_openprose-windows-process-fixture"))
}

fn environment() -> Vec<EnvironmentEntry> {
    [
        ("OPENPROSE_INVOCATION_ID", "fixture-invocation"),
        ("OPENPROSE_RECURSION_TOKEN", "fixture-recursion"),
        ("OPENPROSE_RUN_NONCE", "fixture-nonce"),
        ("OPENPROSE_FIXTURE_SENTINEL", "fixture-only"),
    ]
    .into_iter()
    .map(|(name, value)| EnvironmentEntry {
        name: name.into(),
        value: value.into(),
    })
    .collect()
}

fn request(mode: &str) -> HostRequest {
    HostRequest {
        schema: REQUEST_SCHEMA.into(),
        request_id: format!("windows-{mode}"),
        executable: fixture_executable().to_string_lossy().into_owned(),
        wrapper_executable: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        argv: vec![mode.into()],
        cwd: std::env::current_dir()
            .expect("test cwd")
            .to_string_lossy()
            .into_owned(),
        environment: environment(),
        stdin_base64: String::new(),
        limits: HostLimits {
            max_stdout_bytes: 1_048_576,
            max_stderr_bytes: 1_048_576,
            max_queued_chunks: 64,
        },
        cancellation: CancellationPolicy {
            graceful: GracefulControl::CtrlBreak,
            grace_ms: 20,
            hard_kill_after_ms: 5000,
        },
        run_timeout_ms: 5000,
    }
}

struct HostGuard {
    child: Option<Child>,
}

impl HostGuard {
    fn child(&mut self) -> &mut Child {
        self.child.as_mut().expect("live host")
    }

    fn finish(&mut self) {
        self.child = None;
    }
}

impl Drop for HostGuard {
    fn drop(&mut self) {
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

struct RemoveFile(PathBuf);

impl Drop for RemoveFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn spawn_host() -> HostGuard {
    let child = Command::new(host_executable())
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn provider-free host");
    HostGuard { child: Some(child) }
}

fn run_natural(request: &HostRequest) -> Output {
    let mut host = spawn_host();
    let mut input = host.child().stdin.take().expect("host stdin");
    serde_json::to_writer(&mut input, request).expect("request JSON");
    input.write_all(b"\n").expect("request newline");
    input.flush().expect("request flush");
    capture_bounded(host, input)
}

fn capture_bounded(mut host: HostGuard, input: ChildStdin) -> Output {
    let stdout = host.child().stdout.take().expect("host stdout");
    let stderr = host.child().stderr.take().expect("host stderr");
    let stdout_reader = thread::spawn(move || read_all(stdout));
    let stderr_reader = thread::spawn(move || read_all(stderr));
    let status = wait_bounded(&mut host);
    drop(input);
    let stdout = stdout_reader.join().expect("stdout reader");
    let stderr = stderr_reader.join().expect("stderr reader");
    host.finish();
    Output {
        status,
        stdout,
        stderr,
    }
}

fn read_all(mut reader: impl Read) -> Vec<u8> {
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes).expect("read host stream");
    bytes
}

fn wait_bounded(host: &mut HostGuard) -> ExitStatus {
    let deadline = Instant::now() + TEST_TIMEOUT;
    loop {
        if let Some(status) = host.child().try_wait().expect("poll host") {
            return status;
        }
        if Instant::now() >= deadline {
            let _ = host.child().kill();
            let _ = host.child().wait();
            panic!("provider-free Windows host exceeded {TEST_TIMEOUT:?}");
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn parse_events(bytes: &[u8]) -> Vec<Value> {
    bytes
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
        .map(|line| serde_json::from_slice(line).expect("event JSON"))
        .collect()
}

fn child_stream(events: &[Value], stream: &str) -> Vec<u8> {
    let mut output = Vec::new();
    for event in events {
        if event["type"] == format!("child.{stream}") {
            output.extend(
                decode_base64(event["payload"]["data"].as_str().expect("chunk data"))
                    .expect("chunk base64"),
            );
        }
    }
    output
}

fn terminal(events: &[Value]) -> &Value {
    events
        .iter()
        .find(|event| event["type"] == "host.exited")
        .expect("terminal host event")
}

#[derive(Debug, Clone, Copy)]
enum TreeStop {
    Cancel,
    Malformed,
    Disconnect,
}

fn run_tree(stop: TreeStop) -> (Value, Vec<Value>, Vec<u8>) {
    let run = request("tree");
    let mut host = spawn_host();
    let mut input = Some(host.child().stdin.take().expect("host stdin"));
    let stdout = host.child().stdout.take().expect("host stdout");
    let stderr = host.child().stderr.take().expect("host stderr");
    let (line_tx, line_rx) = mpsc::channel();
    let stdout_reader = thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut raw = Vec::new();
        loop {
            let mut line = Vec::new();
            match reader.read_until(b'\n', &mut line) {
                Ok(0) => break,
                Ok(_) => {
                    raw.extend_from_slice(&line);
                    let _ = line_tx.send(line);
                }
                Err(error) => panic!("read host event: {error}"),
            }
        }
        raw
    });
    let stderr_reader = thread::spawn(move || read_all(stderr));

    let writer = input.as_mut().expect("host input");
    writeln!(
        writer,
        "{}",
        serde_json::to_string(&run).expect("request JSON")
    )
    .expect("request line");
    writer.flush().expect("request flush");

    let mut fixture_output = Vec::new();
    while !fixture_output.contains(&b'\n') {
        let line = line_rx
            .recv_timeout(TEST_TIMEOUT)
            .expect("bounded wait for fixture observation");
        let event: Value = serde_json::from_slice(&line).expect("event JSON");
        if event["type"] == "child.stdout" {
            fixture_output.extend(
                decode_base64(event["payload"]["data"].as_str().expect("chunk data"))
                    .expect("chunk base64"),
            );
        }
    }

    match stop {
        TreeStop::Cancel => {
            let writer = input.as_mut().expect("host input");
            writeln!(
                writer,
                "{}",
                json!({"type":"cancel","schema":CONTROL_SCHEMA,"reason":"fixture-ready"})
            )
            .expect("cancel line");
            writer.flush().expect("cancel flush");
        }
        TreeStop::Malformed => {
            let writer = input.as_mut().expect("host input");
            writeln!(writer, "{{}}").expect("malformed control line");
            writer.flush().expect("malformed control flush");
        }
        TreeStop::Disconnect => drop(input.take()),
    }

    let status = wait_bounded(&mut host);
    drop(input);
    let stdout = stdout_reader.join().expect("stdout reader");
    let stderr = stderr_reader.join().expect("stderr reader");
    host.finish();
    assert_eq!(status.code(), Some(0), "{stderr:?}");
    let tree = serde_json::from_slice(&fixture_output).expect("tree observation");
    (tree, parse_events(&stdout), stderr)
}

#[test]
fn exact_argv_environment_cwd_stdin_and_native_exit_are_observed() {
    let mut run = request("echo");
    let expected_arguments = ["", "two words", "a\\\"b", "ends slash \\", "雪 λ"];
    run.argv.extend(expected_arguments.map(str::to_owned));
    run.stdin_base64 = encode_base64(b"binary\0stdin\xff");

    let output = run_natural(&run);
    assert_eq!(output.status.code(), Some(0), "{:?}", output.stderr);
    let events = parse_events(&output.stdout);
    assert_eq!(
        events
            .iter()
            .map(|event| event["sequence"].as_u64().expect("sequence"))
            .collect::<Vec<_>>(),
        (0..u64::try_from(events.len()).expect("event count")).collect::<Vec<_>>()
    );
    let started = &events[0]["payload"];
    assert_eq!(started["suspendedCreate"], true);
    assert_eq!(started["jobAssignedBeforeResume"], true);
    assert_eq!(started["strictHandleList"], true);
    assert_eq!(started["inheritedHandleCount"], 3);
    assert_eq!(started["shell"], false);
    assert_eq!(started["outerPty"], false);

    let observation: Value =
        serde_json::from_slice(&child_stream(&events, "stdout")).expect("fixture observation JSON");
    assert_eq!(observation["argv"], json!(expected_arguments));
    assert_eq!(observation["stdinBase64"], run.stdin_base64);
    assert_eq!(
        observation["environment"]["OPENPROSE_FIXTURE_SENTINEL"],
        "fixture-only"
    );
    let expected_names = environment()
        .into_iter()
        .map(|entry| entry.name)
        .collect::<std::collections::BTreeSet<_>>();
    let observed_names = observation["environmentNames"]
        .as_array()
        .expect("environment names")
        .iter()
        .map(|name| name.as_str().expect("environment name").to_owned())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        observed_names, expected_names,
        "child environment must be closed"
    );
    assert_eq!(child_stream(&events, "stderr"), b"fixture-stderr\n");
    let expected_cwd = fs::canonicalize(&run.cwd).expect("canonical cwd");
    assert!(paths_equal_text(
        observation["cwd"].as_str().expect("observed cwd"),
        &expected_cwd.to_string_lossy()
    ));

    let exited = &terminal(&events)["payload"];
    assert_eq!(exited["processExitCode"], 17);
    assert_eq!(exited["termination"], "natural");
    assert_eq!(exited["cleanupVerified"], true);
    assert_eq!(exited["activeProcessesAfterCleanup"], 0);
}

#[test]
fn cancellation_terminates_the_whole_job_and_forbids_breakaway() {
    let (tree, events, stderr) = run_tree(TreeStop::Cancel);
    assert!(stderr.is_empty());
    assert_eq!(tree["breakawaySpawned"], false);
    let exited = &terminal(&events)["payload"];
    assert_eq!(exited["termination"], "cancelled");
    assert_eq!(exited["gracefulControlAttempted"], true);
    assert_eq!(exited["hardKillUsed"], true);
    assert_eq!(exited["activeProcessesAfterCleanup"], 0);
    assert_eq!(exited["cleanupVerified"], true);
}

#[test]
fn malformed_control_fails_closed_and_cleans_the_whole_job() {
    let (tree, events, stderr) = run_tree(TreeStop::Malformed);
    assert!(stderr.is_empty());
    assert_eq!(tree["breakawaySpawned"], false);
    let exited = &terminal(&events)["payload"];
    assert_eq!(exited["termination"], "protocol-failure");
    assert_eq!(exited["hardKillUsed"], true);
    assert_eq!(exited["activeProcessesAfterCleanup"], 0);
    assert_eq!(exited["cleanupVerified"], true);
}

#[test]
fn parent_disconnect_fails_closed_and_cleans_the_whole_job() {
    let (tree, events, stderr) = run_tree(TreeStop::Disconnect);
    assert!(stderr.is_empty());
    assert_eq!(tree["breakawaySpawned"], false);
    let exited = &terminal(&events)["payload"];
    assert_eq!(exited["termination"], "parent-disconnected");
    assert_eq!(exited["hardKillUsed"], true);
    assert_eq!(exited["activeProcessesAfterCleanup"], 0);
    assert_eq!(exited["cleanupVerified"], true);
}

#[test]
fn output_limit_fails_closed_and_cleans_the_job() {
    let mut run = request("flood");
    run.argv.push("1048576".into());
    run.limits.max_stdout_bytes = 1024;
    let output = run_natural(&run);
    assert_eq!(output.status.code(), Some(0), "{:?}", output.stderr);
    let events = parse_events(&output.stdout);
    let exited = &terminal(&events)["payload"];
    assert_eq!(exited["termination"], "output-limit");
    assert_eq!(exited["cleanupVerified"], true);
    assert_eq!(exited["activeProcessesAfterCleanup"], 0);
}

#[test]
fn canonical_file_identity_rejects_hardlink_wrapper_recursion_before_spawn() {
    let fixture = fixture_executable();
    let alias = unique_path("fixture-alias.exe");
    fs::hard_link(&fixture, &alias).expect("fixture hard link");
    let _remove_alias = RemoveFile(alias.clone());
    let mut run = request("echo");
    run.executable = alias.to_string_lossy().into_owned();
    run.wrapper_executable = fixture.to_string_lossy().into_owned();
    let output = run_natural(&run);
    assert_eq!(output.status.code(), Some(70));
    assert!(output.stdout.is_empty(), "recursive child must not start");
    let error: Value = serde_json::from_slice(&output.stderr).expect("bootstrap error");
    assert_eq!(error["code"], "RECURSIVE_INVOCATION");
}

#[test]
fn ambient_wrapper_recursion_is_rejected_without_a_request() {
    let child = Command::new(host_executable())
        .env_clear()
        .env("OPENPROSE_RECURSION_TOKEN", "active-wrapper")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("run host");
    let mut host = HostGuard { child: Some(child) };
    let input = host.child().stdin.take().expect("host stdin");
    let output = capture_bounded(host, input);
    assert_eq!(output.status.code(), Some(20));
    assert!(output.stdout.is_empty());
    let error: Value = serde_json::from_slice(&output.stderr).expect("bootstrap error");
    assert_eq!(error["code"], "RECURSIVE_INVOCATION");
}

fn unique_path(name: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock")
        .as_nanos();
    std::env::temp_dir().join(format!(
        "openprose-windows-host-{}-{nonce}-{name}",
        std::process::id(),
    ))
}

fn paths_equal_text(left: &str, right: &str) -> bool {
    normalize_path(left) == normalize_path(right)
}

fn normalize_path(value: &str) -> String {
    value
        .strip_prefix(r"\\?\")
        .unwrap_or(value)
        .replace('/', r"\")
        .to_ascii_lowercase()
}
