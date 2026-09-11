use crate::{Clock, IdSource, OutputMode, RunnerError};
use serde::Serialize;
use serde_json::{Value, json};
use std::io::{self, Write};

#[derive(Debug, Clone)]
pub enum Payload {
    Human { stdout: String, stderr: String },
    Json(Value),
    JsonWithDiagnostic { value: Value, stderr: String },
    Jsonl(Vec<Value>),
    JsonlWithDiagnostic { values: Vec<Value>, stderr: String },
}

#[derive(Debug, Clone)]
pub struct CommandOutcome {
    pub exit_code: u8,
    pub payload: Payload,
}

impl CommandOutcome {
    #[must_use]
    pub fn human(stdout: impl Into<String>, stderr: impl Into<String>, exit_code: u8) -> Self {
        Self {
            exit_code,
            payload: Payload::Human {
                stdout: stdout.into(),
                stderr: stderr.into(),
            },
        }
    }

    #[must_use]
    /// Serializes an internally constructed stable result.
    ///
    /// # Panics
    ///
    /// Panics only if a runner-owned `Serialize` implementation fails.
    pub fn json(value: impl Serialize, exit_code: u8) -> Self {
        Self {
            exit_code,
            payload: Payload::Json(
                serde_json::to_value(value).expect("serializable runner output"),
            ),
        }
    }

    #[must_use]
    /// Serializes a stable JSON result while retaining a separately sanitized
    /// harness diagnostic on stderr.
    ///
    /// # Panics
    ///
    /// Panics only if a runner-owned `Serialize` implementation fails.
    pub fn json_with_diagnostic(
        value: impl Serialize,
        stderr: impl Into<String>,
        exit_code: u8,
    ) -> Self {
        Self {
            exit_code,
            payload: Payload::JsonWithDiagnostic {
                value: serde_json::to_value(value).expect("serializable runner output"),
                stderr: stderr.into(),
            },
        }
    }

    #[must_use]
    pub fn jsonl(values: Vec<Value>, exit_code: u8) -> Self {
        Self {
            exit_code,
            payload: Payload::Jsonl(values),
        }
    }

    #[must_use]
    pub fn jsonl_with_diagnostic(
        values: Vec<Value>,
        stderr: impl Into<String>,
        exit_code: u8,
    ) -> Self {
        Self {
            exit_code,
            payload: Payload::JsonlWithDiagnostic {
                values,
                stderr: stderr.into(),
            },
        }
    }

    /// Writes the payload while preserving the stdout/stderr contract.
    ///
    /// # Errors
    ///
    /// Returns the first stream write or JSON serialization error.
    pub fn render(&self, stdout: &mut dyn Write, stderr: &mut dyn Write) -> io::Result<()> {
        match &self.payload {
            Payload::Human {
                stdout: out,
                stderr: err,
            } => {
                stdout.write_all(out.as_bytes())?;
                stderr.write_all(err.as_bytes())?;
            }
            Payload::Json(value) => {
                serde_json::to_writer(&mut *stdout, value)?;
                stdout.write_all(b"\n")?;
            }
            Payload::JsonWithDiagnostic { value, stderr: err } => {
                serde_json::to_writer(&mut *stdout, value)?;
                stdout.write_all(b"\n")?;
                stderr.write_all(err.as_bytes())?;
            }
            Payload::Jsonl(values) => {
                for value in values {
                    serde_json::to_writer(&mut *stdout, value)?;
                    stdout.write_all(b"\n")?;
                }
            }
            Payload::JsonlWithDiagnostic {
                values,
                stderr: err,
            } => {
                for value in values {
                    serde_json::to_writer(&mut *stdout, value)?;
                    stdout.write_all(b"\n")?;
                }
                stderr.write_all(err.as_bytes())?;
            }
        }
        Ok(())
    }
}

#[must_use]
pub fn error_outcome(
    error: RunnerError,
    mode: OutputMode,
    clock: &dyn Clock,
    ids: &dyn IdSource,
) -> CommandOutcome {
    let exit_code = error.exit_code;
    match mode {
        OutputMode::Human => CommandOutcome::human("", format!("{error}\n"), exit_code),
        OutputMode::Json => CommandOutcome::json(error, exit_code),
        OutputMode::Jsonl => {
            let event = json!({
                "schema": "openprose.normalized-event/1",
                "sequence": 0,
                "timestamp": clock.now_rfc3339(),
                "invocationId": ids.next_invocation_id(),
                "type": "runner.failed",
                "payload": {
                    "kind": "runner.failed",
                    "error": error
                }
            });
            CommandOutcome::jsonl(vec![event], exit_code)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ErrorCode;

    struct FixedClock;
    impl Clock for FixedClock {
        fn now_rfc3339(&self) -> String {
            "2026-01-02T03:04:05.000Z".into()
        }
    }
    struct FixedId;
    impl IdSource for FixedId {
        fn next_invocation_id(&self) -> String {
            "test-invocation".into()
        }
    }

    #[test]
    fn json_is_one_line_on_stdout_and_never_stderr() {
        let outcome = error_outcome(
            RunnerError::new(
                ErrorCode::HostedUnavailable,
                "hosted-service",
                "down",
                "retry",
            ),
            OutputMode::Json,
            &FixedClock,
            &FixedId,
        );
        let mut stdout = Vec::new();
        let mut stderr = Vec::new();
        outcome.render(&mut stdout, &mut stderr).unwrap();
        assert!(stderr.is_empty());
        assert_eq!(
            String::from_utf8(stdout.clone()).unwrap().lines().count(),
            1
        );
        let parsed: Value = serde_json::from_slice(&stdout).unwrap();
        assert_eq!(parsed["code"], "HOSTED_UNAVAILABLE");
    }

    #[test]
    fn human_error_uses_only_stderr() {
        let outcome = error_outcome(
            RunnerError::config("bad value"),
            OutputMode::Human,
            &FixedClock,
            &FixedId,
        );
        let mut stdout = Vec::new();
        let mut stderr = Vec::new();
        outcome.render(&mut stdout, &mut stderr).unwrap();
        assert!(stdout.is_empty());
        assert!(
            String::from_utf8(stderr)
                .unwrap()
                .contains("CONFIG_INVALID")
        );
    }
}
