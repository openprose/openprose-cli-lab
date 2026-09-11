#!/usr/bin/env python3
"""Provider-free structural audit for the Windows native process host."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "src" / "windows.rs"


class AuditFailure(RuntimeError):
    pass


checks: list[str] = []


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AuditFailure(label)
    checks.append(label)


def ordered(text: str, labels: list[str], context: str) -> None:
    cursor = -1
    for label in labels:
        position = text.find(label, cursor + 1)
        require(position >= 0, f"{context} contains {label}")
        require(position > cursor, f"{context} orders {label}")
        cursor = position


def main() -> int:
    manifest = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")
    source = WINDOWS.read_text(encoding="utf-8")
    main_source = (ROOT / "src" / "main.rs").read_text(encoding="utf-8")
    library_source = (ROOT / "src" / "lib.rs").read_text(encoding="utf-8")
    fixture_source = (
        ROOT / "src" / "bin" / "openprose_windows_process_fixture.rs"
    ).read_text(encoding="utf-8")
    runtime_tests = (ROOT / "tests" / "windows_runtime.rs").read_text(encoding="utf-8")

    require('rust-version = "1.87"' in manifest, "manifest pins Rust floor")
    require('channel = "1.87.0"' in toolchain, "workspace pins Rust toolchain")
    require('serde = { version = "=1.0.228"' in manifest, "serde is exact")
    require('serde_json = "=1.0.145"' in manifest, "serde_json is exact")
    require('windows-sys = { version = "=0.61.2"' in manifest, "windows-sys is exact")

    spawn = source[source.index("fn spawn_contained"):source.index("fn create_pipes")]
    ordered(
        spawn,
        [
            "create_pipes()",
            "AttributeList::new",
            "create_kill_on_close_job()",
            "CreateProcessW(",
            "AssignProcessToJobObject(",
            "Ok(Spawned",
        ],
        "suspended spawn",
    )
    run = source[source.index("fn run_inner"):source.index("fn canonicalize_request_paths")]
    ordered(run, ["spawn_contained(", "ResumeThread("], "run lifecycle")
    require("CREATE_SUSPENDED" in spawn, "spawn requests suspended creation")
    require("CREATE_NEW_PROCESS_GROUP" in spawn, "spawn requests a new process group")
    require("CREATE_UNICODE_ENVIRONMENT" in spawn, "spawn passes an explicit Unicode environment")
    require("EXTENDED_STARTUPINFO_PRESENT" in spawn, "spawn enables the extended handle list")
    require("PROC_THREAD_ATTRIBUTE_HANDLE_LIST" in source, "strict inherited-handle list is configured")
    require("[HANDLE; 3]" in source, "handle list is closed to three standard handles")
    require("JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in source, "Job kill-on-close is configured")
    require("TerminateJobObject(" in source, "hard cleanup uses Job authority")
    require("QueryInformationJobObject(" in source, "cleanup evidence uses Job accounting")
    require("TerminateProcess(" in spawn, "pre-assignment failure kills the suspended process")
    require("GenerateConsoleCtrlEvent(" in run, "grace path uses explicit control delivery")
    require("read_protocol_line" in source, "shared bounded-line parser is used")
    require("EventSequencer" in source, "shared event lifecycle sequencer is used")
    require("classify_control_line" in source, "shared disconnect/control classifier is used")
    require("enqueue_event" in source, "shared nonblocking event enqueue is used")
    require("mpsc::sync_channel" in source, "diagnostic and IO queues are bounded")
    require("file_identity(" in source and "GetFileInformationByHandle(" in source,
            "recursion rejection uses Windows file identity")

    production = "\n".join((library_source, main_source, source))
    forbidden = [
        "Command::new",
        "cmd.exe",
        "powershell",
        "ShellExecute",
        "CREATE_BREAKAWAY_FROM_JOB",
        "CreatePseudoConsole",
        "TcpStream",
        "UdpSocket",
        "reqwest",
    ]
    for token in forbidden:
        require(token not in production, f"production excludes {token}")
    require("unsafe" not in library_source and "unsafe" not in main_source,
            "Win32 unsafe code is isolated to windows.rs")
    require("unsafe" not in fixture_source, "provider-free fixture contains no unsafe code")
    require('"environment":' not in source and '"argv":' not in source,
            "host diagnostics do not serialize argv or environment")

    schema_names = ["request", "control", "event", "error", "identity"]
    schemas = {
        name: json.loads((ROOT / "protocol" / f"{name}.schema.json").read_text(encoding="utf-8"))
        for name in schema_names
    }
    require(all(schema["$schema"].endswith("2020-12/schema") for schema in schemas.values()),
            "all protocol schemas use JSON Schema 2020-12")
    require(all(schemas[name].get("additionalProperties") is False for name in ("request", "control", "error", "identity")),
            "request, control, error, and identity envelopes are closed")
    event_defs = schemas["event"]["$defs"]
    require(all(event_defs[name]["allOf"][1]["additionalProperties"] is False for name in ("started", "stream", "exited")),
            "all event envelopes are closed")
    request_properties = schemas["request"]["properties"]
    require(request_properties["argv"]["maxItems"] == 64,
            "request argv count matches the host cap")
    require(request_properties["argv"]["items"]["maxLength"] == 32766,
            "request argument length matches the native cap")
    require(request_properties["environment"]["maxItems"] == 64,
            "request environment count matches the host cap")
    environment_properties = request_properties["environment"]["items"]["properties"]
    require(environment_properties["name"]["maxLength"] == 128,
            "environment-name length is closed")
    require(environment_properties["value"]["maxLength"] == 8192,
            "environment-value length is closed")
    require(request_properties["stdinBase64"]["maxLength"] == 22369620,
            "base64 request length is closed")
    require(22369620 // 4 * 3 == 16777215,
            "base64 and decoded child-stdin caps are exact")
    require(schemas["identity"]["properties"]["protocol"]["properties"]["maxRequestBytes"]["const"] == 67108864,
            "machine identity exposes the exact line cap")
    identity_limits = schemas["identity"]["properties"]["protocol"]["properties"]["limits"]
    require(identity_limits["additionalProperties"] is False,
            "machine identity limit inventory is closed")
    require(identity_limits["properties"]["maxDecodedStdinBytes"]["const"] == 16777215,
            "machine identity exposes the decoded stdin cap")
    require(identity_limits["properties"]["maxEnvironmentBlockBytes"]["const"] == 4194304,
            "machine identity exposes the environment-block cap")
    schema_wire_upper_bound = (
        22369620
        + 64 * (32766 * 12 + 3)
        + 64 * (128 + 8192 * 12 + 64)
        + 3 * 32766 * 12
        + 128
        + 64 * 1024
    )
    require(schema_wire_upper_bound == 55083084,
            "schema request wire upper bound is stable")
    require(schema_wire_upper_bound <= 67108864,
            "every schema-admitted request fits the bounded line")

    capability = json.loads(
        (ROOT / "capabilities" / "windows-x64.v1.json").read_text(encoding="utf-8")
    )
    claims = capability["claims"]
    require(capability["providerCallsMade"] is False, "capability report records no provider calls")
    require(capability["evidence"]["windowsRuntimeTestsRun"] is False,
            "capability report does not claim a native Windows run")
    require(claims["raceFreeWindowsBehaviorVerified"] is False,
            "capability report does not claim race-free runtime evidence")
    require(claims["strictWindowsContainmentReady"] is False,
            "capability report does not claim strict Windows readiness")
    require(len(re.findall(r"#\[test\]", runtime_tests)) == 7,
            "seven provider-free Windows runtime tests are present")

    print(
        json.dumps(
            {
                "schema": "openprose.windows-process-host.static-audit/1",
                "status": "pass",
                "checks": len(checks),
                "providerCallsMade": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditFailure, KeyError, ValueError, OSError) as error:
        print(
            json.dumps(
                {
                    "schema": "openprose.windows-process-host.static-audit/1",
                    "status": "fail",
                    "reason": str(error),
                    "providerCallsMade": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
