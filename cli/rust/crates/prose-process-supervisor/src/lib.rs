//! Direct, bounded supervision for installed and SDK-managed harness processes.
//!
//! This crate owns process mechanics only. It neither knows nor parses the
//! `OpenProse` language, and it never starts a shell or allocates a PTY.
#![allow(clippy::module_name_repetitions)]

mod environment;
mod framing;
mod platform;
mod prompt_files;
mod supervisor;
mod windows_host;
#[path = "../build_support.rs"]
mod windows_host_build;
#[cfg(windows)]
mod windows_supervision;

pub use environment::{EnvironmentPolicy, Sensitivity};
pub use framing::StreamLimits;
pub use platform::{ContainmentClaim, strict_containment_available};
pub use prompt_files::{
    OMP_CONTROL_OVERLAY_BYTES, PrivatePromptFiles, PrivatePromptFilesCreateError,
};
pub use supervisor::{
    CancellationToken, CommandProbe, CommandProbeOutcome, FailureKind, JsonlProtocol,
    ProcessOutcome, ProcessSpec, RecordObserver, SignalCancellationGuard, StdinLifecycle,
    SupervisorFailure, VersionProbe, VersionProbeOutput, probe_command, probe_version, supervise,
    supervise_observed,
};
pub use windows_host::{
    VerifiedWindowsHost, WindowsGracefulControl, WindowsHostBootstrapError,
    WindowsHostCancellation, WindowsHostChunk, WindowsHostClient, WindowsHostClientFailure,
    WindowsHostClientFailureKind, WindowsHostCompletion, WindowsHostEnvironmentEntry,
    WindowsHostIdentity, WindowsHostLimits, WindowsHostRequest, WindowsHostTermination,
    encode_windows_cancel_control, encode_windows_host_request, parse_windows_host_bootstrap_error,
    validate_windows_host_identity, validate_windows_version_probe, verify_windows_host_sibling,
};
pub use windows_host_build::WindowsHostBuildIdentity;

/// Fixed sidecar name used only beside the canonical prose wrapper executable.
pub const WINDOWS_PROCESS_HOST_FILENAME: &str = "openprose-windows-process-host.exe";

/// Returns the immutable process-host admission values compiled into this runner.
#[must_use]
pub fn compiled_windows_host_identity() -> WindowsHostBuildIdentity {
    let digest = env!("OPENPROSE_COMPILED_WINDOWS_HOST_SHA256");
    WindowsHostBuildIdentity {
        admitted: env!("OPENPROSE_COMPILED_WINDOWS_HOST_ADMISSION") == "1",
        sha256: (!digest.is_empty()).then(|| digest.to_owned()),
    }
}

/// Reports only compile/platform admission, never native-runtime evidence.
#[must_use]
pub fn windows_host_execution_admitted() -> bool {
    cfg!(windows) && compiled_windows_host_identity().admitted
}

/// Environment marker inherited by every supervised harness tree.
pub const RECURSION_MARKER: &str = "OPENPROSE_RECURSION_TOKEN";
/// Per-run audit nonce inherited by fixture descendants and owned helpers.
pub const RUN_NONCE: &str = "OPENPROSE_RUN_NONCE";
/// Invocation identity inherited as transport metadata, never model context.
pub const INVOCATION_ID: &str = "OPENPROSE_INVOCATION_ID";
