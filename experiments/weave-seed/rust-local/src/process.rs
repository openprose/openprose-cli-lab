//! Bounded Unix pipes; no shell, ambient environment, worker threads or PTY.
use crate::Result;
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

fn nonblocking(pipe: &impl AsRawFd) -> Result<()> {
    let fd = pipe.as_raw_fd();
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(format!("configure capability pipe: {}", io::Error::last_os_error()));
    }
    Ok(())
}

fn drain(pipe: &mut impl Read, bytes: &mut Vec<u8>, count: &mut u64, limit: u64, retain: bool) -> Result<bool> {
    loop {
        let mut buffer = [0u8; 8192];
        let size = (limit.saturating_sub(*count) + 1).min(buffer.len() as u64) as usize;
        match pipe.read(&mut buffer[..size]) {
            Ok(0) => return Ok(true),
            Ok(n) => {
                *count += n as u64;
                if *count > limit { return Err("capability output exceeds bound".into()); }
                if retain { bytes.extend_from_slice(&buffer[..n]); }
                return Ok(false);
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => return Ok(false),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(format!("read capability output: {e}")),
        }
    }
}

pub fn run_process(command: &[String], cwd: &Path, environment: &[(String, String)], input: &[u8], timeout_ms: u64, limit: u64, cancelled: &AtomicBool) -> Result<Vec<u8>> {
    if cancelled.load(Ordering::Relaxed) { return Err("capability cancelled before launch".into()); }
    let mut child = Command::new(&command[0]).args(&command[1..]).current_dir(cwd)
        .env_clear().envs(environment.iter().map(|(k, v)| (k, v)))
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .process_group(0).spawn().map_err(|e| format!("start capability: {e}"))?;
    let pid = child.id() as i32;
    let started = Instant::now();
    let result = (|| {
        let mut stdin = child.stdin.take();
        let mut stdout = child.stdout.take().ok_or("missing stdout")?;
        let mut stderr = child.stderr.take().ok_or("missing stderr")?;
        nonblocking(stdin.as_ref().ok_or("missing stdin")?)?;
        nonblocking(&stdout)?;
        nonblocking(&stderr)?;
        let mut sent = 0;
        let mut bytes = Vec::new();
        let mut discarded = Vec::new();
        let (mut out_count, mut err_count) = (0, 0);
        let (mut out_eof, mut err_eof) = (false, false);
        let mut status = None;
        loop {
            if cancelled.load(Ordering::Relaxed) { return Err("capability cancelled; effects may have occurred".into()); }
            if started.elapsed() >= Duration::from_millis(timeout_ms) { return Err("capability process timed out".into()); }
            if let Some(pipe) = stdin.as_mut() {
                match pipe.write(&input[sent..]) {
                    Ok(n) => sent += n,
                    Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted) => {},
                    Err(e) => return Err(format!("write capability input: {e}")),
                }
                if sent == input.len() { stdin = None; }
            }
            if !out_eof { out_eof = drain(&mut stdout, &mut bytes, &mut out_count, limit, true)?; }
            if !err_eof { err_eof = drain(&mut stderr, &mut discarded, &mut err_count, limit, false)?; }
            if status.is_none() { status = child.try_wait().map_err(|e| format!("wait capability: {e}"))?; }
            if let Some(exit) = status {
                if !exit.success() { return Err("capability process failed or was signalled".into()); }
                if out_eof && err_eof && stdin.is_none() { return Ok(bytes); }
            }
            std::thread::sleep(Duration::from_millis(2));
        }
    })();
    if result.is_err() {
        // Cooperative process-group cleanup, not confinement of adversarial descendants.
        unsafe { libc::kill(-pid, libc::SIGKILL); }
        let _ = child.kill();
        let _ = child.wait();
    }
    result
}
