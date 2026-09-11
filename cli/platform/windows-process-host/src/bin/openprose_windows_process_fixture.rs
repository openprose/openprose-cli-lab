//! Provider-free Windows child used only by the native-host test suite.

use openprose_windows_process_host::encode_base64;
use serde_json::json;
use std::io::{self, Read, Write};
use std::process::ExitCode;
use std::thread;
use std::time::Duration;

#[cfg(windows)]
use std::os::windows::process::CommandExt as _;
#[cfg(windows)]
use std::process::Command;
#[cfg(windows)]
use windows_sys::Win32::System::Threading::CREATE_BREAKAWAY_FROM_JOB;

fn main() -> ExitCode {
    let mut arguments = std::env::args().skip(1);
    match arguments.next().as_deref().unwrap_or("echo") {
        "echo" => echo(&arguments.collect::<Vec<_>>()),
        "hold" => hold(),
        "tree" => tree(),
        "grandchild" => grandchild(),
        "flood" => flood(arguments.next().as_deref()),
        _ => ExitCode::from(64),
    }
}

fn echo(arguments: &[String]) -> ExitCode {
    let mut input = Vec::new();
    if io::stdin().read_to_end(&mut input).is_err() {
        return ExitCode::from(74);
    }
    let metadata = [
        "OPENPROSE_INVOCATION_ID",
        "OPENPROSE_RECURSION_TOKEN",
        "OPENPROSE_RUN_NONCE",
        "OPENPROSE_FIXTURE_SENTINEL",
    ]
    .into_iter()
    .map(|name| {
        (
            name.to_owned(),
            serde_json::to_value(std::env::var(name).ok()).expect("Option<String> is JSON"),
        )
    })
    .collect::<serde_json::Map<String, serde_json::Value>>();
    let cwd = match std::env::current_dir() {
        Ok(path) => path.to_string_lossy().into_owned(),
        Err(_) => return ExitCode::from(74),
    };
    let mut environment_names = std::env::vars_os()
        .map(|(name, _)| name.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    environment_names.sort_by_key(|name| name.to_ascii_uppercase());
    let observation = json!({
        "schema": "openprose.windows-process-fixture.observation/1",
        "argv": arguments,
        "cwd": cwd,
        "environment": metadata,
        "environmentNames": environment_names,
        "stdinBase64": encode_base64(&input)
    });
    if writeln!(io::stdout().lock(), "{observation}").is_err()
        || writeln!(io::stderr().lock(), "fixture-stderr").is_err()
    {
        return ExitCode::from(74);
    }
    ExitCode::from(17)
}

fn hold() -> ExitCode {
    println!("{{\"schema\":\"openprose.windows-process-fixture.ready/1\"}}");
    let _ = io::stdout().flush();
    thread::sleep(Duration::from_secs(120));
    ExitCode::SUCCESS
}

#[cfg(windows)]
fn tree() -> ExitCode {
    let Ok(executable) = std::env::current_exe() else {
        return ExitCode::from(74);
    };

    // A Job without BREAKAWAY_OK must reject this. If the platform accepts it,
    // clean up the escaped child immediately and leave a failing observation.
    let breakaway_spawned = match Command::new(&executable)
        .arg("grandchild")
        .creation_flags(CREATE_BREAKAWAY_FROM_JOB)
        .spawn()
    {
        Ok(mut child) => {
            let _ = child.kill();
            let _ = child.wait();
            true
        }
        Err(_) => false,
    };
    let Ok(descendant) = Command::new(&executable).arg("grandchild").spawn() else {
        return ExitCode::from(74);
    };
    println!(
        "{}",
        json!({
            "schema": "openprose.windows-process-fixture.tree/1",
            "descendantPid": descendant.id(),
            "breakawaySpawned": breakaway_spawned
        })
    );
    let _ = io::stdout().flush();
    drop(descendant);
    thread::sleep(Duration::from_secs(120));
    ExitCode::SUCCESS
}

#[cfg(not(windows))]
fn tree() -> ExitCode {
    ExitCode::from(20)
}

fn grandchild() -> ExitCode {
    thread::sleep(Duration::from_secs(120));
    ExitCode::SUCCESS
}

fn flood(bytes: Option<&str>) -> ExitCode {
    let byte_count = bytes
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(1_048_576);
    let chunk = [b'X'; 8192];
    let mut remaining = byte_count;
    let mut output = io::stdout().lock();
    while remaining > 0 {
        let written = remaining.min(chunk.len());
        if output.write_all(&chunk[..written]).is_err() {
            return ExitCode::from(74);
        }
        remaining -= written;
    }
    ExitCode::SUCCESS
}
