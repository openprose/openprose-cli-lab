#![cfg_attr(not(windows), allow(dead_code))]

#[cfg(windows)]
mod windows;

use openprose_windows_process_host::{
    COMPONENT_NAME, COMPONENT_VERSION, ERROR_SCHEMA, host_identity,
};
use serde_json::json;
use std::ffi::OsStr;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut arguments = std::env::args_os().skip(1);
    match (arguments.next(), arguments.next()) {
        (Some(argument), None) if argument == OsStr::new("--version") => {
            println!("{COMPONENT_NAME} {COMPONENT_VERSION}");
            return ExitCode::SUCCESS;
        }
        (Some(argument), None) if argument == OsStr::new("--identity-json") => {
            println!("{}", host_identity());
            return ExitCode::SUCCESS;
        }
        (Some(_), _) => {
            eprintln!(
                "{}",
                json!({
                    "schema": ERROR_SCHEMA,
                    "code": "CLI_ARGUMENT_INVALID",
                    "operation": "expected no arguments, --version, or --identity-json",
                    "win32": null
                })
            );
            return ExitCode::from(64);
        }
        (None, _) => {}
    }
    #[cfg(windows)]
    {
        ExitCode::from(windows::run())
    }
    #[cfg(not(windows))]
    {
        eprintln!(
            "{}",
            json!({
                "schema": ERROR_SCHEMA,
                "code": "PLATFORM_UNSUPPORTED",
                "operation": "target is not Windows",
                "win32": null
            })
        );
        ExitCode::from(20)
    }
}
