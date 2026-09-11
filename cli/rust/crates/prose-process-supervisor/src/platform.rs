use std::io;
use std::process::{Child, Command};
#[cfg(unix)]
use std::thread;
use std::time::Duration;
#[cfg(unix)]
use std::time::Instant;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContainmentClaim {
    /// The original child process group was managed, but a descendant may
    /// escape it with `setsid`; this is not strict descendant containment.
    UnixProcessGroupBestEffort,
    /// The native host owns a Job Object that accounts for descendants.
    WindowsNativeProcessHost,
    Unsupported,
}

#[must_use]
/// Direct supervision has no strict descendant authority. Native Windows Job
/// admission is reported separately by `windows_host_execution_admitted`.
pub const fn strict_containment_available() -> bool {
    false
}

#[cfg(unix)]
#[allow(clippy::unnecessary_wraps)] // Same fallible signature on every platform.
pub(crate) fn prepare(command: &mut Command) -> io::Result<ContainmentClaim> {
    use std::os::unix::process::CommandExt as _;
    command.process_group(0);
    Ok(ContainmentClaim::UnixProcessGroupBestEffort)
}

#[cfg(windows)]
pub(crate) fn prepare(_command: &mut Command) -> io::Result<ContainmentClaim> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "strict Windows containment requires suspended creation and race-free Job assignment; this build does not implement it",
    ))
}

#[cfg(not(any(unix, windows)))]
pub(crate) fn prepare(_command: &mut Command) -> io::Result<ContainmentClaim> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "strict process containment is unavailable on this platform",
    ))
}

#[cfg(unix)]
pub(crate) fn terminate_original_process_group(
    child: &mut Child,
    grace: Duration,
) -> io::Result<()> {
    use rustix::process::{Pid, Signal, kill_process_group, test_kill_process_group};

    let group = Pid::from_child(child);
    let _ = kill_process_group(group, Signal::TERM);
    let graceful_deadline = Instant::now() + grace;
    while Instant::now() < graceful_deadline {
        if child.try_wait()?.is_some() {
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }

    // SIGKILL is sent to the group even if the direct child exited during the
    // grace period: descendants may still retain the owned group.
    let _ = kill_process_group(group, Signal::KILL);
    let hard_deadline = Instant::now() + grace.max(Duration::from_millis(250));
    while Instant::now() < hard_deadline {
        let child_done = child.try_wait()?.is_some();
        let group_gone = test_kill_process_group(group).is_err();
        if child_done && group_gone {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(5));
    }
    let _ = child.kill();
    let _ = child.wait();
    if test_kill_process_group(group).is_ok() {
        return Err(io::Error::other(
            "original Unix process group still exists after hard termination",
        ));
    }
    Ok(())
}

#[cfg(unix)]
pub(crate) fn original_process_group_is_empty(child: &Child) -> bool {
    use rustix::process::{Pid, test_kill_process_group};
    test_kill_process_group(Pid::from_child(child)).is_err()
}

#[cfg(not(unix))]
pub(crate) fn terminate_original_process_group(
    _child: &mut Child,
    _grace: Duration,
) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "strict tree termination is unavailable on this platform",
    ))
}

#[cfg(not(unix))]
pub(crate) fn original_process_group_is_empty(_child: &Child) -> bool {
    false
}
