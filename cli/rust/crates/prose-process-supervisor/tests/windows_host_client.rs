use prose_process_supervisor::{
    FailureKind, WindowsGracefulControl, WindowsHostCancellation, WindowsHostChunk,
    WindowsHostClient, WindowsHostClientFailureKind, WindowsHostEnvironmentEntry,
    WindowsHostLimits, WindowsHostRequest, WindowsHostTermination, encode_windows_cancel_control,
    encode_windows_host_request, parse_windows_host_bootstrap_error,
    validate_windows_version_probe,
};
use serde_json::{Value, json};

const REQUEST_ID: &str = "request-0001";

fn line(value: &Value) -> Vec<u8> {
    let mut encoded = serde_json::to_vec(value).unwrap();
    encoded.push(b'\n');
    encoded
}

fn event(sequence: u64, event_type: &str, payload: &Value) -> Vec<u8> {
    line(&json!({
        "schema": "openprose.windows-process-host.event/1",
        "requestId": REQUEST_ID,
        "sequence": sequence,
        "type": event_type,
        "payload": payload,
    }))
}

fn started(sequence: u64) -> Vec<u8> {
    event(
        sequence,
        "host.started",
        &json!({
            "pid": 412,
            "suspendedCreate": true,
            "strictHandleList": true,
            "inheritedHandleCount": 3,
            "jobAssignedBeforeResume": true,
            "killOnJobClose": true,
            "newProcessGroup": true,
            "outerPty": false,
            "shell": false,
        }),
    )
}

fn stream(sequence: u64, name: &str, data: &str) -> Vec<u8> {
    event(
        sequence,
        &format!("child.{name}"),
        &json!({"encoding":"base64", "data":data, "stream":name}),
    )
}

fn terminal(
    sequence: u64,
    termination: &str,
    exit: u32,
    stdout_bytes: u64,
    stderr_bytes: u64,
) -> Vec<u8> {
    event(
        sequence,
        "host.exited",
        &json!({
            "pid": 412,
            "processExitCode": exit,
            "termination": termination,
            "gracefulControlAttempted": termination != "natural",
            "gracefulControlDelivered": false,
            "hardKillUsed": termination != "natural",
            "activeProcessesBeforeCleanup": 0,
            "activeProcessesAfterCleanup": 0,
            "cleanupVerified": true,
            "stdoutBytes": stdout_bytes,
            "stderrBytes": stderr_bytes,
        }),
    )
}

fn client() -> WindowsHostClient {
    WindowsHostClient::new(
        REQUEST_ID,
        WindowsHostLimits {
            max_stdout_bytes: 1024,
            max_stderr_bytes: 512,
            max_queued_chunks: 8,
        },
    )
    .unwrap()
}

fn accept_started(client: &mut WindowsHostClient) {
    assert!(matches!(
        client.accept_line(&started(0)).unwrap(),
        Some(WindowsHostChunk::Started { pid: 412 })
    ));
}

#[test]
fn request_and_cancel_lines_are_closed_canonical_and_binary_safe() {
    let request = WindowsHostRequest {
        request_id: REQUEST_ID.to_owned(),
        executable: r"C:\Tools\codex.exe".to_owned(),
        wrapper_executable: r"C:\OpenProse\prose.exe".to_owned(),
        argv: vec!["exec".to_owned(), "two words".to_owned(), "雪".to_owned()],
        cwd: r"C:\workspace with spaces".to_owned(),
        environment: vec![
            WindowsHostEnvironmentEntry {
                name: "OPENPROSE_INVOCATION_ID".to_owned(),
                value: "invocation-1".to_owned(),
            },
            WindowsHostEnvironmentEntry {
                name: "OPENPROSE_RECURSION_TOKEN".to_owned(),
                value: "recursion-1".to_owned(),
            },
            WindowsHostEnvironmentEntry {
                name: "OPENPROSE_RUN_NONCE".to_owned(),
                value: "nonce-1".to_owned(),
            },
        ],
        child_stdin: vec![0, 1, 2, 0xff],
        limits: WindowsHostLimits {
            max_stdout_bytes: 4096,
            max_stderr_bytes: 2048,
            max_queued_chunks: 16,
        },
        cancellation: WindowsHostCancellation {
            graceful: WindowsGracefulControl::CtrlBreak,
            grace_ms: 250,
            hard_kill_after_ms: 5000,
        },
        run_timeout_ms: 60_000,
    };
    let encoded = encode_windows_host_request(&request).unwrap();
    assert_eq!(encoded.last(), Some(&b'\n'));
    let value: Value = serde_json::from_slice(&encoded).unwrap();
    assert_eq!(value["schema"], "openprose.windows-process-host.request/1");
    assert_eq!(value["stdinBase64"], "AAEC/w==");
    assert_eq!(value["cancellation"]["graceful"], "ctrl-break");
    assert_eq!(value.as_object().unwrap().len(), 11);

    let cancel = encode_windows_cancel_control("caller:SIGINT").unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&cancel).unwrap(),
        json!({
            "type":"cancel",
            "schema":"openprose.windows-process-host.control/1",
            "reason":"caller:SIGINT"
        })
    );
    assert_eq!(cancel.last(), Some(&b'\n'));
}

#[test]
fn request_encoder_rejects_shell_targets_aliases_case_collisions_and_nul() {
    let base = WindowsHostRequest {
        request_id: REQUEST_ID.to_owned(),
        executable: r"C:\Tools\codex.exe".to_owned(),
        wrapper_executable: r"C:\OpenProse\prose.exe".to_owned(),
        argv: vec![],
        cwd: r"C:\workspace".to_owned(),
        environment: vec![],
        child_stdin: vec![],
        limits: WindowsHostLimits {
            max_stdout_bytes: 1,
            max_stderr_bytes: 1,
            max_queued_chunks: 1,
        },
        cancellation: WindowsHostCancellation {
            graceful: WindowsGracefulControl::None,
            grace_ms: 0,
            hard_kill_after_ms: 1,
        },
        run_timeout_ms: 1,
    };
    let mut command = base.clone();
    command.executable = r"C:\Tools\codex.cmd".to_owned();
    assert_eq!(
        encode_windows_host_request(&command).unwrap_err().kind,
        WindowsHostClientFailureKind::RequestInvalid
    );
    let mut alias = base.clone();
    alias.wrapper_executable = alias.executable.to_ascii_lowercase();
    assert_eq!(
        encode_windows_host_request(&alias).unwrap_err().kind,
        WindowsHostClientFailureKind::RequestInvalid
    );
    let mut collision = base.clone();
    collision.environment = vec![
        WindowsHostEnvironmentEntry {
            name: "Path".to_owned(),
            value: "one".to_owned(),
        },
        WindowsHostEnvironmentEntry {
            name: "PATH".to_owned(),
            value: "two".to_owned(),
        },
    ];
    assert_eq!(
        encode_windows_host_request(&collision).unwrap_err().kind,
        WindowsHostClientFailureKind::RequestInvalid
    );
    let mut nul = base;
    nul.argv.push("bad\0argument".to_owned());
    assert_eq!(
        encode_windows_host_request(&nul).unwrap_err().kind,
        WindowsHostClientFailureKind::RequestInvalid
    );
    assert!(encode_windows_cancel_control("contains space").is_err());
}

#[test]
fn request_encoder_enforces_required_metadata_and_frozen_collection_limits() {
    let base = WindowsHostRequest {
        request_id: REQUEST_ID.to_owned(),
        executable: r"C:\Tools\codex.exe".to_owned(),
        wrapper_executable: r"C:\OpenProse\prose.exe".to_owned(),
        argv: vec![],
        cwd: r"C:\workspace".to_owned(),
        environment: vec![],
        child_stdin: vec![],
        limits: WindowsHostLimits {
            max_stdout_bytes: 1,
            max_stderr_bytes: 1,
            max_queued_chunks: 1,
        },
        cancellation: WindowsHostCancellation {
            graceful: WindowsGracefulControl::None,
            grace_ms: 0,
            hard_kill_after_ms: 1,
        },
        run_timeout_ms: 1,
    };
    assert_eq!(
        encode_windows_host_request(&base).unwrap_err().kind,
        WindowsHostClientFailureKind::RequestInvalid
    );

    let mut too_many_environment = base.clone();
    too_many_environment.environment = (0..65)
        .map(|index| WindowsHostEnvironmentEntry {
            name: format!("NAME_{index}"),
            value: "value".to_owned(),
        })
        .collect();
    assert_eq!(
        encode_windows_host_request(&too_many_environment)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::RequestLimit
    );

    let mut oversized_command_line = base;
    oversized_command_line.environment = [
        ("OPENPROSE_INVOCATION_ID", "invocation"),
        ("OPENPROSE_RECURSION_TOKEN", "recursion"),
        ("OPENPROSE_RUN_NONCE", "nonce"),
    ]
    .into_iter()
    .map(|(name, value)| WindowsHostEnvironmentEntry {
        name: name.to_owned(),
        value: value.to_owned(),
    })
    .collect();
    let mut oversized_stdin = oversized_command_line.clone();
    oversized_stdin.child_stdin = vec![0; 16_777_216];
    assert_eq!(
        encode_windows_host_request(&oversized_stdin)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::RequestLimit
    );

    oversized_command_line.argv = vec!["x".repeat(32_766)];
    assert_eq!(
        encode_windows_host_request(&oversized_command_line)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::RequestLimit
    );
}

#[test]
fn valid_stream_preserves_binary_channels_and_requires_authoritative_terminal() {
    let mut decoder = client();
    accept_started(&mut decoder);
    assert_eq!(
        decoder
            .accept_line(&stream(1, "stdout", "AAH/eg=="))
            .unwrap(),
        Some(WindowsHostChunk::Stdout(vec![0, 1, 0xff, b'z']))
    );
    assert_eq!(
        decoder
            .accept_line(&stream(2, "stderr", "ZGlhZ25vc3RpYwo="))
            .unwrap(),
        Some(WindowsHostChunk::Stderr(b"diagnostic\n".to_vec()))
    );
    assert_eq!(
        decoder
            .accept_line(&terminal(3, "natural", 17, 4, 11))
            .unwrap(),
        None
    );
    let completed = decoder.finish(Some(0)).unwrap();
    assert_eq!(completed.pid, 412);
    assert_eq!(completed.process_exit_code, 17);
    assert_eq!(completed.termination, WindowsHostTermination::Natural);
    assert!(completed.cleanup_verified);
    assert_eq!(completed.stdout_bytes, 4);
    assert_eq!(completed.stderr_bytes, 11);
}

#[test]
fn event_envelope_and_ordering_are_closed_and_fail_once() {
    let invalid_first = [
        event(
            0,
            "child.stdout",
            &json!({"encoding":"base64","data":"","stream":"stdout"}),
        ),
        event(1, "host.started", &json!({})),
        line(&json!({
            "schema":"wrong", "requestId":REQUEST_ID, "sequence":0,
            "type":"host.started", "payload":{}
        })),
        line(&json!({
            "schema":"openprose.windows-process-host.event/1", "requestId":"other",
            "sequence":0, "type":"host.started", "payload":{},
        })),
        line(&json!({
            "schema":"openprose.windows-process-host.event/1", "requestId":REQUEST_ID,
            "sequence":0, "type":"host.started", "payload":{}, "extra":true,
        })),
    ];
    for bytes in invalid_first {
        let mut decoder = client();
        assert_eq!(
            decoder.accept_line(&bytes).unwrap_err().kind,
            WindowsHostClientFailureKind::ProtocolMalformed
        );
        assert_eq!(
            decoder.accept_line(&started(0)).unwrap_err().kind,
            WindowsHostClientFailureKind::ProtocolMalformed,
            "a failed decoder must remain poisoned"
        );
    }

    let mut wrong_sequence = client();
    assert_eq!(
        wrong_sequence.accept_line(&started(1)).unwrap_err().kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );
    let mut duplicate = client();
    accept_started(&mut duplicate);
    assert_eq!(
        duplicate.accept_line(&started(1)).unwrap_err().kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );
}

#[test]
fn started_stream_and_terminal_payloads_are_exact() {
    let mut bad_started: Value = serde_json::from_slice(&started(0)).unwrap();
    bad_started["payload"]["shell"] = json!(true);
    let mut decoder = client();
    assert_eq!(
        decoder.accept_line(&line(&bad_started)).unwrap_err().kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );

    for data in ["A===", "AA=A", "AB==", "AAB=", "雪", " AA=="] {
        let mut decoder = client();
        accept_started(&mut decoder);
        assert_eq!(
            decoder
                .accept_line(&stream(1, "stdout", data))
                .unwrap_err()
                .kind,
            WindowsHostClientFailureKind::ProtocolMalformed,
            "accepted non-canonical base64 {data:?}"
        );
    }

    let mut mismatch = client();
    accept_started(&mut mismatch);
    let mismatched = event(
        1,
        "child.stdout",
        &json!({"encoding":"base64", "data":"", "stream":"stderr"}),
    );
    assert_eq!(
        mismatch.accept_line(&mismatched).unwrap_err().kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );

    let mut terminal_extra: Value =
        serde_json::from_slice(&terminal(1, "natural", 0, 0, 0)).unwrap();
    terminal_extra["payload"]["extra"] = json!(true);
    let mut decoder = client();
    accept_started(&mut decoder);
    assert_eq!(
        decoder
            .accept_line(&line(&terminal_extra))
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );

    let mut wrong_pid = client();
    accept_started(&mut wrong_pid);
    let mut terminal_value: Value =
        serde_json::from_slice(&terminal(1, "natural", 0, 0, 0)).unwrap();
    terminal_value["payload"]["pid"] = json!(413);
    assert_eq!(
        wrong_pid
            .accept_line(&line(&terminal_value))
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );

    let mut after_terminal = client();
    accept_started(&mut after_terminal);
    after_terminal
        .accept_line(&terminal(1, "natural", 0, 0, 0))
        .unwrap();
    assert_eq!(
        after_terminal
            .accept_line(&stream(2, "stdout", ""))
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );
}

#[test]
fn aggregate_limits_and_terminal_byte_evidence_are_enforced() {
    let mut stdout_limit = WindowsHostClient::new(
        REQUEST_ID,
        WindowsHostLimits {
            max_stdout_bytes: 3,
            max_stderr_bytes: 3,
            max_queued_chunks: 1,
        },
    )
    .unwrap();
    accept_started(&mut stdout_limit);
    assert_eq!(
        stdout_limit
            .accept_line(&stream(1, "stdout", "AAAAAA=="))
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::StdoutLimit
    );

    let mut count_mismatch = client();
    accept_started(&mut count_mismatch);
    count_mismatch
        .accept_line(&stream(1, "stdout", "YQ=="))
        .unwrap();
    assert_eq!(
        count_mismatch
            .accept_line(&terminal(2, "natural", 0, 2, 0))
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );

    let mut stderr_limit = WindowsHostClient::new(
        REQUEST_ID,
        WindowsHostLimits {
            max_stdout_bytes: 3,
            max_stderr_bytes: 3,
            max_queued_chunks: 1,
        },
    )
    .unwrap();
    accept_started(&mut stderr_limit);
    stderr_limit
        .accept_line(&terminal(1, "output-limit", 1, 0, 4))
        .unwrap();
    let failure = stderr_limit.finish(Some(0)).unwrap_err();
    assert_eq!(failure.kind, WindowsHostClientFailureKind::StderrLimit);
    assert_eq!(failure.supervisor_kind(), FailureKind::HarnessFailed);

    let oversized_event = vec![b'x'; 65 * 1024];
    let mut bounded = client();
    assert_eq!(
        bounded.accept_line(&oversized_event).unwrap_err().kind,
        WindowsHostClientFailureKind::ProtocolMalformed
    );
}

#[test]
fn finish_maps_disconnect_host_failure_cleanup_and_host_termination() {
    let mut truncated = client();
    accept_started(&mut truncated);
    assert_eq!(
        truncated.finish(Some(0)).unwrap_err().kind,
        WindowsHostClientFailureKind::ProtocolTruncated
    );
    let mut disconnected = client();
    accept_started(&mut disconnected);
    assert_eq!(
        disconnected.finish(None).unwrap_err().kind,
        WindowsHostClientFailureKind::HostDisconnected
    );

    let mut bad_host_exit = client();
    accept_started(&mut bad_host_exit);
    bad_host_exit
        .accept_line(&terminal(1, "natural", 0, 0, 0))
        .unwrap();
    assert_eq!(
        bad_host_exit.finish(Some(70)).unwrap_err().kind,
        WindowsHostClientFailureKind::HostFailed
    );

    let mut unclean = client();
    accept_started(&mut unclean);
    let mut event_value: Value = serde_json::from_slice(&terminal(1, "natural", 0, 0, 0)).unwrap();
    event_value["payload"]["cleanupVerified"] = json!(false);
    event_value["payload"]["activeProcessesAfterCleanup"] = json!(1);
    unclean.accept_line(&line(&event_value)).unwrap();
    let failure = unclean.finish(Some(0)).unwrap_err();
    assert_eq!(
        failure.kind,
        WindowsHostClientFailureKind::CleanupUnverified
    );
    assert_eq!(failure.supervisor_kind(), FailureKind::CleanupFailed);

    let mut unclean_nonzero_host = client();
    accept_started(&mut unclean_nonzero_host);
    let mut event_value: Value = serde_json::from_slice(&terminal(1, "natural", 0, 0, 0)).unwrap();
    event_value["payload"]["cleanupVerified"] = json!(false);
    event_value["payload"]["activeProcessesAfterCleanup"] = json!(1);
    unclean_nonzero_host
        .accept_line(&line(&event_value))
        .unwrap();
    assert_eq!(
        unclean_nonzero_host.finish(Some(25)).unwrap_err().kind,
        WindowsHostClientFailureKind::CleanupUnverified
    );

    for (termination, expected, supervisor) in [
        (
            "cancelled",
            WindowsHostClientFailureKind::Cancelled,
            FailureKind::Cancelled,
        ),
        (
            "timeout",
            WindowsHostClientFailureKind::TimedOut,
            FailureKind::RunTimeout,
        ),
        (
            "output-limit",
            WindowsHostClientFailureKind::StdoutLimit,
            FailureKind::ProtocolMalformed,
        ),
        (
            "protocol-failure",
            WindowsHostClientFailureKind::ProtocolMalformed,
            FailureKind::ProtocolMalformed,
        ),
        (
            "io-failure",
            WindowsHostClientFailureKind::HostFailed,
            FailureKind::HarnessFailed,
        ),
        (
            "backpressure",
            WindowsHostClientFailureKind::HostFailed,
            FailureKind::HarnessFailed,
        ),
        (
            "parent-disconnected",
            WindowsHostClientFailureKind::HostDisconnected,
            FailureKind::Internal,
        ),
    ] {
        let mut decoder = client();
        accept_started(&mut decoder);
        let stdout_bytes = if termination == "output-limit" {
            1025
        } else {
            0
        };
        decoder
            .accept_line(&terminal(1, termination, 1, stdout_bytes, 0))
            .unwrap();
        let failure = decoder.finish(Some(0)).unwrap_err();
        assert_eq!(failure.kind, expected, "{termination}");
        assert_eq!(failure.supervisor_kind(), supervisor, "{termination}");
    }
}

#[test]
fn bootstrap_diagnostic_is_bounded_single_line_and_closed() {
    let encoded = line(&json!({
        "schema":"openprose.windows-process-host.error/1",
        "code":"JOB_ASSIGNMENT_FAILED",
        "operation":"AssignProcessToJobObject",
        "win32":5
    }));
    let parsed = parse_windows_host_bootstrap_error(&encoded, 1024).unwrap();
    assert_eq!(parsed.code, "JOB_ASSIGNMENT_FAILED");
    assert_eq!(parsed.operation, "AssignProcessToJobObject");
    assert_eq!(parsed.win32, Some(5));
    assert_eq!(
        parsed.supervisor_kind(),
        FailureKind::ContainmentUnsupported
    );

    for invalid in [
        b"{}\n".as_slice(),
        b"{\"schema\":\"openprose.windows-process-host.error/1\",\"code\":\"X\",\"operation\":\"Y\",\"win32\":null,\"extra\":true}\n",
        b"{\"schema\":\"openprose.windows-process-host.error/1\",\"code\":\"X\",\"operation\":\"Y\",\"win32\":null}\n{}\n",
    ] {
        assert_eq!(
            parse_windows_host_bootstrap_error(invalid, 1024).unwrap_err().kind,
            WindowsHostClientFailureKind::HostDiagnosticMalformed
        );
    }
    assert_eq!(
        parse_windows_host_bootstrap_error(&encoded, 8)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::HostDiagnosticLimit
    );
}

#[test]
fn version_probe_uses_the_same_host_completion_without_protocol_interpretation() {
    let mut decoder = client();
    accept_started(&mut decoder);
    let bytes = b"codex-cli 0.149.0-alpha.4\n";
    let data = "Y29kZXgtY2xpIDAuMTQ5LjAtYWxwaGEuNAo=";
    let chunk = decoder
        .accept_line(&stream(1, "stdout", data))
        .unwrap()
        .unwrap();
    let WindowsHostChunk::Stdout(stdout) = chunk else {
        panic!("stdout chunk")
    };
    assert_eq!(stdout, bytes);
    decoder
        .accept_line(&terminal(2, "natural", 0, bytes.len() as u64, 0))
        .unwrap();
    let completion = decoder.finish(Some(0)).unwrap();
    assert_eq!(
        validate_windows_version_probe(&completion, &stdout, 64).unwrap(),
        "codex-cli 0.149.0-alpha.4"
    );
    assert_eq!(
        validate_windows_version_probe(&completion, &stdout, 4)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::VersionProbeInvalid
    );
    assert_eq!(
        validate_windows_version_probe(&completion, b"\xff", 64)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::VersionProbeInvalid
    );

    let mut nonzero = completion;
    nonzero.process_exit_code = 17;
    assert_eq!(
        validate_windows_version_probe(&nonzero, b"valid-version\n", 64)
            .unwrap_err()
            .kind,
        WindowsHostClientFailureKind::VersionProbeInvalid
    );
}

#[test]
fn canonical_base64_accepts_empty_and_each_valid_padding_width() {
    let mut decoder = client();
    accept_started(&mut decoder);
    for (sequence, encoded, expected) in [
        (1, "", Vec::new()),
        (2, "AA==", vec![0]),
        (3, "AAE=", vec![0, 1]),
        (4, "AAEC", vec![0, 1, 2]),
    ] {
        assert_eq!(
            decoder
                .accept_line(&stream(sequence, "stdout", encoded))
                .unwrap(),
            Some(WindowsHostChunk::Stdout(expected))
        );
    }
    decoder
        .accept_line(&terminal(5, "natural", 0, 6, 0))
        .unwrap();
    assert!(decoder.finish(Some(0)).is_ok());
}
