//! The only module allowed to call Win32 directly.
//!
//! Safety invariant: a successfully created child remains suspended until it
//! has been assigned to the configured Job Object. Every raw handle is moved
//! into one `OwnedHandle`, and the strict inherited-handle list contains only
//! the child's three redirected standard handles.

use openprose_windows_process_host::{
    ControlAction, EventQueueError, EventSequencer, GracefulControl, MAX_CONTROL_BYTES,
    MAX_REQUEST_BYTES, ProtocolLine, ValidatedRequest, build_command_line, classify_control_line,
    encode_base64, enqueue_event, parse_request_line, read_protocol_line,
};
use serde_json::{Value, json};
use std::ffi::c_void;
use std::fs::File;
use std::io::{self, BufReader, Read, Write};
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::OsStrExt as _;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::mpsc::{self, Receiver, SyncSender, TryRecvError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{
    GetLastError, HANDLE, HANDLE_FLAG_INHERIT, SetHandleInformation, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Storage::FileSystem::{
    BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
};
use windows_sys::Win32::System::Console::{CTRL_BREAK_EVENT, GenerateConsoleCtrlEvent};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
    QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CREATE_NEW_PROCESS_GROUP, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessW,
    DeleteProcThreadAttributeList, EXTENDED_STARTUPINFO_PRESENT, GetExitCodeProcess,
    InitializeProcThreadAttributeList, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROCESS_INFORMATION,
    ResumeThread, STARTF_USESTDHANDLES, STARTUPINFOEXW, TerminateProcess,
    UpdateProcThreadAttribute, WaitForSingleObject,
};

const HOST_TERMINATION_EXIT: u32 = 0x4f50_524f;
const POLL_INTERVAL: Duration = Duration::from_millis(5);
const IO_CHUNK_BYTES: usize = 8192;

#[derive(Debug)]
struct HostError {
    code: &'static str,
    operation: &'static str,
    win32: Option<u32>,
}

impl HostError {
    const fn protocol(code: &'static str, operation: &'static str) -> Self {
        Self {
            code,
            operation,
            win32: None,
        }
    }

    fn last(code: &'static str, operation: &'static str) -> Self {
        // SAFETY: `GetLastError` has no pointer preconditions and is called
        // immediately after the failing Win32 operation.
        let win32 = unsafe { GetLastError() };
        Self {
            code,
            operation,
            win32: Some(win32),
        }
    }
}

#[derive(Debug)]
struct Pipes {
    child_stdin: OwnedHandle,
    parent_stdin: OwnedHandle,
    parent_stdout: OwnedHandle,
    child_stdout: OwnedHandle,
    parent_stderr: OwnedHandle,
    child_stderr: OwnedHandle,
}

#[derive(Debug)]
struct AttributeList {
    // Pointer-sized elements give the opaque Win32 list at least pointer
    // alignment; a byte vector would not provide that Rust-level guarantee.
    storage: Vec<usize>,
    initialized: bool,
}

impl AttributeList {
    fn new(handles: &mut [HANDLE; 3]) -> Result<Self, HostError> {
        let mut bytes = 0usize;
        // SAFETY: the documented sizing call accepts a null list and writes
        // only the required byte count to `bytes`.
        unsafe {
            InitializeProcThreadAttributeList(null_mut(), 1, 0, &raw mut bytes);
        }
        if bytes == 0 {
            return Err(HostError::last(
                "HANDLE_LIST_INITIALIZATION_FAILED",
                "InitializeProcThreadAttributeList(size)",
            ));
        }
        let storage = vec![0usize; bytes.div_ceil(size_of::<usize>())];
        let mut list = Self {
            storage,
            initialized: false,
        };
        // SAFETY: `storage` has the size returned by the sizing call and stays
        // alive and immovable until after `CreateProcessW` returns.
        if unsafe { InitializeProcThreadAttributeList(list.pointer(), 1, 0, &raw mut bytes) } == 0 {
            return Err(HostError::last(
                "HANDLE_LIST_INITIALIZATION_FAILED",
                "InitializeProcThreadAttributeList",
            ));
        }
        list.initialized = true;
        // SAFETY: `handles` is a live contiguous array of exactly the three
        // inheritable standard handles and remains alive through process creation.
        if unsafe {
            UpdateProcThreadAttribute(
                list.pointer(),
                0,
                usize::try_from(PROC_THREAD_ATTRIBUTE_HANDLE_LIST)
                    .expect("u32 process attribute fits Windows usize"),
                handles.as_ptr().cast(),
                size_of::<[HANDLE; 3]>(),
                null_mut(),
                null(),
            )
        } == 0
        {
            return Err(HostError::last(
                "HANDLE_LIST_INITIALIZATION_FAILED",
                "UpdateProcThreadAttribute(handle-list)",
            ));
        }
        Ok(list)
    }

    fn pointer(&mut self) -> *mut c_void {
        self.storage.as_mut_ptr().cast()
    }
}

impl Drop for AttributeList {
    fn drop(&mut self) {
        if self.initialized {
            // SAFETY: a successful initializer owns exactly one initialized list
            // in `storage`; Windows requires the paired delete before freeing it.
            unsafe { DeleteProcThreadAttributeList(self.pointer()) };
        }
    }
}

#[derive(Debug)]
struct Spawned {
    process: OwnedHandle,
    thread: OwnedHandle,
    job: OwnedHandle,
    pid: u32,
    parent_stdin: OwnedHandle,
    parent_stdout: OwnedHandle,
    parent_stderr: OwnedHandle,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stream {
    Stdout,
    Stderr,
}

#[derive(Debug)]
enum IoMessage {
    Chunk(Stream, Vec<u8>),
    Eof(Stream),
    ReadFailed,
    StdinFailed,
}

#[derive(Debug)]
enum ControlMessage {
    Cancel,
    Disconnected,
    Malformed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Termination {
    Natural,
    Caller,
    ParentDisconnected,
    Timeout,
    OutputLimit,
    IoFailure,
    ProtocolFailure,
    Backpressure,
}

impl Termination {
    const fn label(self) -> &'static str {
        match self {
            Self::Natural => "natural",
            Self::Caller => "cancelled",
            Self::ParentDisconnected => "parent-disconnected",
            Self::Timeout => "timeout",
            Self::OutputLimit => "output-limit",
            Self::IoFailure => "io-failure",
            Self::ProtocolFailure => "protocol-failure",
            Self::Backpressure => "backpressure",
        }
    }
}

pub fn run() -> u8 {
    if std::env::var_os("OPENPROSE_RECURSION_TOKEN").is_some() {
        emit_bootstrap_failure(&HostError::protocol(
            "RECURSIVE_INVOCATION",
            "host inherited an active wrapper recursion marker",
        ));
        return 20;
    }
    match run_inner() {
        Ok(code) => code,
        Err(error) => {
            emit_bootstrap_failure(&error);
            70
        }
    }
}

#[allow(clippy::too_many_lines)]
fn run_inner() -> Result<u8, HostError> {
    let mut input = BufReader::new(io::stdin());
    let request_line = match read_protocol_line(&mut input, MAX_REQUEST_BYTES)
        .map_err(|_| HostError::protocol("PROTOCOL_MALFORMED", "read request"))?
    {
        ProtocolLine::Complete(line) => line,
        ProtocolLine::EndOfStream => {
            return Err(HostError::protocol(
                "PROTOCOL_MALFORMED",
                "request line is missing",
            ));
        }
        ProtocolLine::Unterminated => {
            return Err(HostError::protocol(
                "PROTOCOL_MALFORMED",
                "request line is not newline terminated",
            ));
        }
        ProtocolLine::LimitExceeded => {
            return Err(HostError::protocol(
                "REQUEST_LIMIT_EXCEEDED",
                "request line exceeds the host limit",
            ));
        }
    };
    let mut validated = parse_request_line(&request_line).map_err(|error| HostError {
        code: error.code,
        operation: error.field,
        win32: None,
    })?;
    canonicalize_request_paths(&mut validated)?;
    let request_id = validated.request.request_id.clone();
    let max_queue = validated.request.limits.max_queued_chunks;

    let spawned = spawn_contained(&validated)?;
    let pid = spawned.pid;
    let (event_tx, event_rx) = mpsc::sync_channel::<Value>(max_queue);
    let (writer_status_tx, writer_status_rx) = mpsc::sync_channel::<bool>(1);
    let writer = spawn_event_writer(event_rx, writer_status_tx);
    let mut events = EventSequencer::new(&request_id)
        .map_err(|error| HostError::protocol(error.code, error.field))?;
    send_event(
        &event_tx,
        events
            .next(
                "host.started",
                &json!({
                    "pid": pid,
                    "suspendedCreate": true,
                    "strictHandleList": true,
                    "inheritedHandleCount": 3,
                    "jobAssignedBeforeResume": true,
                    "killOnJobClose": true,
                    "newProcessGroup": true,
                    "outerPty": false,
                    "shell": false
                }),
            )
            .map_err(|error| HostError::protocol(error.code, error.field))?,
    )?;

    let (io_tx, io_rx) = mpsc::sync_channel(max_queue);
    let stdout_reader = spawn_reader(spawned.parent_stdout, Stream::Stdout, io_tx.clone());
    let stderr_reader = spawn_reader(spawned.parent_stderr, Stream::Stderr, io_tx.clone());
    let stdin_writer = spawn_stdin_writer(spawned.parent_stdin, validated.child_stdin, io_tx);
    let (control_tx, control_rx) = mpsc::sync_channel(max_queue);
    let _control_reader = spawn_control_reader(input, control_tx);

    // SAFETY: the primary thread handle belongs to the still-suspended child,
    // which has already been assigned to `spawned.job`.
    if unsafe { ResumeThread(raw_handle(&spawned.thread)) } == u32::MAX {
        let error = HostError::last("RESUME_FAILED", "ResumeThread");
        terminate_job(&spawned.job)?;
        return Err(error);
    }

    let started = Instant::now();
    let run_deadline = started + Duration::from_millis(validated.request.run_timeout_ms);
    let mut stdout_bytes = 0usize;
    let mut stderr_bytes = 0usize;
    let mut stdout_eof = false;
    let mut stderr_eof = false;
    let mut termination = None;
    let mut graceful_attempted = false;
    let mut graceful_delivered = false;
    let mut hard_kill_used = false;
    let mut graceful_deadline = None;
    let native_exit = loop {
        match writer_status_rx.try_recv() {
            Ok(false) | Err(TryRecvError::Disconnected) => {
                termination.get_or_insert(Termination::Backpressure);
            }
            Ok(true) | Err(TryRecvError::Empty) => {}
        }
        match control_rx.try_recv() {
            Ok(ControlMessage::Cancel) => {
                termination.get_or_insert(Termination::Caller);
            }
            Ok(ControlMessage::Disconnected) | Err(TryRecvError::Disconnected) => {
                termination.get_or_insert(Termination::ParentDisconnected);
            }
            Ok(ControlMessage::Malformed) => {
                termination.get_or_insert(Termination::ProtocolFailure);
            }
            Err(TryRecvError::Empty) => {}
        }
        if Instant::now() >= run_deadline {
            termination.get_or_insert(Termination::Timeout);
        }
        drain_one_io(
            &io_rx,
            &event_tx,
            &mut events,
            &mut stdout_bytes,
            &mut stderr_bytes,
            validated.request.limits.max_stdout_bytes,
            validated.request.limits.max_stderr_bytes,
            &mut stdout_eof,
            &mut stderr_eof,
            &mut termination,
        )?;

        if termination.is_some() && !graceful_attempted {
            graceful_attempted = true;
            if validated.request.cancellation.graceful == GracefulControl::CtrlBreak {
                // SAFETY: the PID is the process-group identifier created by
                // `CREATE_NEW_PROCESS_GROUP`; this is best-effort grace only.
                graceful_delivered =
                    unsafe { GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) } != 0;
            }
            graceful_deadline = Some(
                Instant::now() + Duration::from_millis(validated.request.cancellation.grace_ms),
            );
        }

        // SAFETY: `spawned.process` is a live process handle with synchronize access.
        let wait = unsafe { WaitForSingleObject(raw_handle(&spawned.process), 0) };
        if wait == WAIT_OBJECT_0 {
            break process_exit_code(&spawned.process)?;
        }
        if wait != WAIT_TIMEOUT {
            termination.get_or_insert(Termination::IoFailure);
        }
        if graceful_deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            terminate_job(&spawned.job)?;
            hard_kill_used = true;
            wait_for_process(
                &spawned.process,
                validated.request.cancellation.hard_kill_after_ms,
            )?;
            break process_exit_code(&spawned.process)?;
        }
        thread::sleep(POLL_INTERVAL);
    };

    // A direct process can exit while retaining descendants. The Job handle,
    // not a PID scan, is authoritative: terminate any remaining job members.
    let active_before_cleanup = active_job_processes(&spawned.job)?;
    if active_before_cleanup > 0 {
        terminate_job(&spawned.job)?;
        hard_kill_used = true;
    }
    let active_after_cleanup = wait_for_empty_job(
        &spawned.job,
        validated.request.cancellation.hard_kill_after_ms,
    )?;

    join_io(stdin_writer);
    let io_deadline =
        Instant::now() + Duration::from_millis(validated.request.cancellation.hard_kill_after_ms);
    while !(stdout_eof && stderr_eof) && Instant::now() < io_deadline {
        drain_one_io_wait(
            &io_rx,
            &event_tx,
            &mut events,
            &mut stdout_bytes,
            &mut stderr_bytes,
            validated.request.limits.max_stdout_bytes,
            validated.request.limits.max_stderr_bytes,
            &mut stdout_eof,
            &mut stderr_eof,
            &mut termination,
            POLL_INTERVAL,
        )?;
    }
    // Once the Job is empty both inherited writers must close. Joining after
    // draining avoids deadlocking a reader on the bounded channel.
    join_io(stdout_reader);
    join_io(stderr_reader);

    let final_termination = termination.unwrap_or(Termination::Natural);
    send_event(
        &event_tx,
        events
            .next(
                "host.exited",
                &json!({
                    "pid": pid,
                    "processExitCode": native_exit,
                    "termination": final_termination.label(),
                    "gracefulControlAttempted": graceful_attempted,
                    "gracefulControlDelivered": graceful_delivered,
                    "hardKillUsed": hard_kill_used,
                    "activeProcessesBeforeCleanup": active_before_cleanup,
                    "activeProcessesAfterCleanup": active_after_cleanup,
                    "cleanupVerified": active_after_cleanup == 0,
                    "stdoutBytes": stdout_bytes,
                    "stderrBytes": stderr_bytes
                }),
            )
            .map_err(|error| HostError::protocol(error.code, error.field))?,
    )?;
    drop(event_tx);
    let _ = writer.join();
    Ok(if active_after_cleanup == 0 { 0 } else { 25 })
}

fn canonicalize_request_paths(validated: &mut ValidatedRequest) -> Result<(), HostError> {
    let executable = canonical_file(&validated.request.executable, "canonicalize executable")?;
    let wrapper = canonical_file(
        &validated.request.wrapper_executable,
        "canonicalize wrapper executable",
    )?;
    let current = std::env::current_exe()
        .and_then(std::fs::canonicalize)
        .map_err(|_| HostError::protocol("PATH_INVALID", "canonicalize host executable"))?;
    if paths_equal(&executable, &wrapper) || paths_equal(&executable, &current) {
        return Err(HostError::protocol(
            "RECURSIVE_INVOCATION",
            "canonical executable aliases the wrapper or process host",
        ));
    }
    let cwd = std::fs::canonicalize(&validated.request.cwd)
        .map_err(|_| HostError::protocol("PATH_INVALID", "canonicalize cwd"))?;
    if !cwd.is_dir() {
        return Err(HostError::protocol(
            "PATH_INVALID",
            "cwd is not a directory",
        ));
    }
    let executable_text = executable.to_string_lossy().into_owned();
    validated.command_line = build_command_line(&executable_text, &validated.request.argv)
        .map_err(|error| HostError::protocol(error.code, error.field))?;
    validated.request.executable = executable_text;
    validated.request.wrapper_executable = wrapper.to_string_lossy().into_owned();
    validated.request.cwd = cwd.to_string_lossy().into_owned();
    Ok(())
}

fn canonical_file(path: &str, operation: &'static str) -> Result<PathBuf, HostError> {
    let canonical =
        std::fs::canonicalize(path).map_err(|_| HostError::protocol("PATH_INVALID", operation))?;
    if !canonical.is_file()
        || !canonical
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("exe"))
    {
        return Err(HostError::protocol("SHELL_TARGET_REJECTED", operation));
    }
    Ok(canonical)
}

fn paths_equal(left: &Path, right: &Path) -> bool {
    if left
        .as_os_str()
        .to_string_lossy()
        .eq_ignore_ascii_case(&right.as_os_str().to_string_lossy())
    {
        return true;
    }
    matches!(
        (file_identity(left), file_identity(right)),
        (Some(left_identity), Some(right_identity)) if left_identity == right_identity
    )
}

fn file_identity(path: &Path) -> Option<(u32, u64)> {
    let file = File::open(path).ok()?;
    let mut information = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: `file` owns a live file handle and `information` is a writable
    // structure of the exact type required by this API.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), &raw mut information) } == 0
    {
        return None;
    }
    let index = u64::from(information.nFileIndexHigh) << 32 | u64::from(information.nFileIndexLow);
    Some((information.dwVolumeSerialNumber, index))
}

fn spawn_contained(validated: &ValidatedRequest) -> Result<Spawned, HostError> {
    let pipes = create_pipes()?;
    let mut inherited = [
        raw_handle(&pipes.child_stdin),
        raw_handle(&pipes.child_stdout),
        raw_handle(&pipes.child_stderr),
    ];
    let mut attributes = AttributeList::new(&mut inherited)?;
    let job = create_kill_on_close_job()?;
    let mut startup = STARTUPINFOEXW::default();
    startup.StartupInfo.cb = u32::try_from(size_of::<STARTUPINFOEXW>())
        .map_err(|_| HostError::protocol("INTERNAL_ERROR", "STARTUPINFOEXW size"))?;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = inherited[0];
    startup.StartupInfo.hStdOutput = inherited[1];
    startup.StartupInfo.hStdError = inherited[2];
    startup.lpAttributeList = attributes.pointer();

    let application = nul_wide(&validated.request.executable);
    let cwd = nul_wide(&validated.request.cwd);
    let mut command_line = validated.command_line.clone();
    let mut information: PROCESS_INFORMATION = unsafe { zeroed() };
    let flags = CREATE_SUSPENDED
        | CREATE_NEW_PROCESS_GROUP
        | CREATE_UNICODE_ENVIRONMENT
        | EXTENDED_STARTUPINFO_PRESENT;
    // SAFETY: all pointers refer to live, NUL-terminated buffers; the startup
    // attribute list and handle array remain alive through this call. Only the
    // explicitly listed three inheritable pipe handles may cross the boundary.
    let created = unsafe {
        CreateProcessW(
            application.as_ptr(),
            command_line.as_mut_ptr(),
            null(),
            null(),
            1,
            flags,
            validated.environment_block.as_ptr().cast(),
            cwd.as_ptr(),
            (&raw const startup.StartupInfo),
            &raw mut information,
        )
    };
    if created == 0 {
        return Err(HostError::last("CREATE_PROCESS_FAILED", "CreateProcessW"));
    }
    // SAFETY: successful `CreateProcessW` returns two unique owned handles.
    let process = unsafe { owned_handle(information.hProcess) };
    // SAFETY: same ownership guarantee as the process handle above.
    let primary_thread = unsafe { owned_handle(information.hThread) };
    // SAFETY: the process is suspended and has not executed user code; this is
    // the race-free assignment point required by the containment contract.
    if unsafe { AssignProcessToJobObject(raw_handle(&job), raw_handle(&process)) } == 0 {
        let error = HostError::last("JOB_ASSIGNMENT_FAILED", "AssignProcessToJobObject");
        // SAFETY: direct termination is used only before Job assignment, while
        // the sole primary thread is still suspended.
        unsafe {
            TerminateProcess(raw_handle(&process), HOST_TERMINATION_EXIT);
            WaitForSingleObject(raw_handle(&process), 5000);
        }
        return Err(error);
    }

    drop(attributes);
    drop(pipes.child_stdin);
    drop(pipes.child_stdout);
    drop(pipes.child_stderr);
    Ok(Spawned {
        process,
        thread: primary_thread,
        job,
        pid: information.dwProcessId,
        parent_stdin: pipes.parent_stdin,
        parent_stdout: pipes.parent_stdout,
        parent_stderr: pipes.parent_stderr,
    })
}

fn create_pipes() -> Result<Pipes, HostError> {
    let (child_stdin, parent_stdin) = create_pipe(true)?;
    let (parent_stdout, child_stdout) = create_pipe(false)?;
    let (parent_stderr, child_stderr) = create_pipe(false)?;
    Ok(Pipes {
        child_stdin,
        parent_stdin,
        parent_stdout,
        child_stdout,
        parent_stderr,
        child_stderr,
    })
}

fn create_pipe(child_reads: bool) -> Result<(OwnedHandle, OwnedHandle), HostError> {
    let mut read = null_mut();
    let mut write = null_mut();
    let attributes = SECURITY_ATTRIBUTES {
        nLength: u32::try_from(size_of::<SECURITY_ATTRIBUTES>())
            .map_err(|_| HostError::protocol("INTERNAL_ERROR", "SECURITY_ATTRIBUTES size"))?,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: 1,
    };
    // SAFETY: output pointers are valid and `attributes` requests inheritable
    // anonymous-pipe handles with a default security descriptor.
    if unsafe { CreatePipe(&raw mut read, &raw mut write, &raw const attributes, 0) } == 0 {
        return Err(HostError::last("PIPE_CREATION_FAILED", "CreatePipe"));
    }
    // SAFETY: successful `CreatePipe` returns unique handles.
    let read = unsafe { owned_handle(read) };
    // SAFETY: same ownership guarantee as the read handle above.
    let write = unsafe { owned_handle(write) };
    let parent = if child_reads { &write } else { &read };
    // SAFETY: the parent endpoint is a live handle; clearing the inheritance
    // flag cannot affect the distinct child endpoint.
    if unsafe { SetHandleInformation(raw_handle(parent), HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err(HostError::last(
            "PIPE_CREATION_FAILED",
            "SetHandleInformation(parent non-inheritable)",
        ));
    }
    Ok((read, write))
}

fn create_kill_on_close_job() -> Result<OwnedHandle, HostError> {
    // SAFETY: null security/name pointers request an unnamed Job with defaults.
    let raw = unsafe { CreateJobObjectW(null(), null()) };
    if raw.is_null() {
        return Err(HostError::last("JOB_CREATION_FAILED", "CreateJobObjectW"));
    }
    // SAFETY: a non-null CreateJobObjectW result is uniquely owned here.
    let job = unsafe { owned_handle(raw) };
    let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: the information pointer/length exactly describe the initialized
    // extended-limit structure and `job` is a live Job handle.
    if unsafe {
        SetInformationJobObject(
            raw_handle(&job),
            JobObjectExtendedLimitInformation,
            (&raw const information).cast(),
            u32::try_from(size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>())
                .map_err(|_| HostError::protocol("INTERNAL_ERROR", "job limit size"))?,
        )
    } == 0
    {
        return Err(HostError::last(
            "JOB_CONFIGURATION_FAILED",
            "SetInformationJobObject(KILL_ON_JOB_CLOSE)",
        ));
    }
    Ok(job)
}

fn spawn_reader(
    handle: OwnedHandle,
    stream: Stream,
    sender: SyncSender<IoMessage>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut file = File::from(handle);
        loop {
            let mut buffer = vec![0u8; IO_CHUNK_BYTES];
            match file.read(&mut buffer) {
                Ok(0) => {
                    let _ = sender.send(IoMessage::Eof(stream));
                    break;
                }
                Ok(read) => {
                    buffer.truncate(read);
                    if sender.send(IoMessage::Chunk(stream, buffer)).is_err() {
                        break;
                    }
                }
                Err(_) => {
                    let _ = sender.send(IoMessage::ReadFailed);
                    break;
                }
            }
        }
    })
}

fn spawn_stdin_writer(
    handle: OwnedHandle,
    bytes: Vec<u8>,
    sender: SyncSender<IoMessage>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut file = File::from(handle);
        if !bytes.is_empty() && file.write_all(&bytes).is_err() {
            let _ = sender.send(IoMessage::StdinFailed);
        }
        // Dropping `file` closes child stdin exactly once after the request bytes.
    })
}

fn spawn_control_reader(
    mut input: BufReader<io::Stdin>,
    sender: SyncSender<ControlMessage>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let action = read_protocol_line(&mut input, MAX_CONTROL_BYTES)
            .map(classify_control_line)
            .unwrap_or(ControlAction::ProtocolFailure);
        let message = match action {
            ControlAction::Cancel(_) => ControlMessage::Cancel,
            ControlAction::ParentDisconnected => ControlMessage::Disconnected,
            ControlAction::ProtocolFailure => ControlMessage::Malformed,
        };
        let _ = sender.send(message);
    })
}

fn spawn_event_writer(receiver: Receiver<Value>, status: SyncSender<bool>) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut output = io::BufWriter::new(io::stdout().lock());
        for value in receiver {
            let written = serde_json::to_writer(&mut output, &value)
                .and_then(|()| output.write_all(b"\n").map_err(serde_json::Error::io))
                .and_then(|()| output.flush().map_err(serde_json::Error::io));
            if written.is_err() {
                let _ = status.send(false);
                return;
            }
        }
        let _ = status.send(true);
    })
}

#[allow(clippy::too_many_arguments)]
fn drain_one_io(
    receiver: &Receiver<IoMessage>,
    events: &SyncSender<Value>,
    sequence: &mut EventSequencer,
    stdout_bytes: &mut usize,
    stderr_bytes: &mut usize,
    max_stdout: usize,
    max_stderr: usize,
    stdout_eof: &mut bool,
    stderr_eof: &mut bool,
    termination: &mut Option<Termination>,
) -> Result<bool, HostError> {
    let message = match receiver.try_recv() {
        Ok(message) => message,
        Err(TryRecvError::Empty | TryRecvError::Disconnected) => return Ok(false),
    };
    apply_io_message(
        message,
        events,
        sequence,
        stdout_bytes,
        stderr_bytes,
        max_stdout,
        max_stderr,
        stdout_eof,
        stderr_eof,
        termination,
    )?;
    Ok(true)
}

#[allow(clippy::too_many_arguments)]
fn drain_one_io_wait(
    receiver: &Receiver<IoMessage>,
    events: &SyncSender<Value>,
    sequence: &mut EventSequencer,
    stdout_bytes: &mut usize,
    stderr_bytes: &mut usize,
    max_stdout: usize,
    max_stderr: usize,
    stdout_eof: &mut bool,
    stderr_eof: &mut bool,
    termination: &mut Option<Termination>,
    timeout: Duration,
) -> Result<bool, HostError> {
    let message = match receiver.recv_timeout(timeout) {
        Ok(message) => message,
        Err(mpsc::RecvTimeoutError::Timeout | mpsc::RecvTimeoutError::Disconnected) => {
            return Ok(false);
        }
    };
    apply_io_message(
        message,
        events,
        sequence,
        stdout_bytes,
        stderr_bytes,
        max_stdout,
        max_stderr,
        stdout_eof,
        stderr_eof,
        termination,
    )?;
    Ok(true)
}

#[allow(clippy::too_many_arguments)]
fn apply_io_message(
    message: IoMessage,
    events: &SyncSender<Value>,
    sequence: &mut EventSequencer,
    stdout_bytes: &mut usize,
    stderr_bytes: &mut usize,
    max_stdout: usize,
    max_stderr: usize,
    stdout_eof: &mut bool,
    stderr_eof: &mut bool,
    termination: &mut Option<Termination>,
) -> Result<(), HostError> {
    match message {
        IoMessage::Chunk(stream, bytes) => {
            let (observed, maximum, label) = match stream {
                Stream::Stdout => (stdout_bytes, max_stdout, "stdout"),
                Stream::Stderr => (stderr_bytes, max_stderr, "stderr"),
            };
            *observed = observed.saturating_add(bytes.len());
            if *observed > maximum {
                termination.get_or_insert(Termination::OutputLimit);
            } else {
                send_event(
                    events,
                    sequence
                        .next(
                        if stream == Stream::Stdout {
                            "child.stdout"
                        } else {
                            "child.stderr"
                        },
                        &json!({"encoding":"base64","data":encode_base64(&bytes),"stream":label}),
                        )
                        .map_err(|error| HostError::protocol(error.code, error.field))?,
                )?;
            }
        }
        IoMessage::Eof(Stream::Stdout) => *stdout_eof = true,
        IoMessage::Eof(Stream::Stderr) => *stderr_eof = true,
        IoMessage::ReadFailed | IoMessage::StdinFailed => {
            termination.get_or_insert(Termination::IoFailure);
        }
    }
    Ok(())
}

fn send_event(sender: &SyncSender<Value>, event: Value) -> Result<(), HostError> {
    match enqueue_event(sender, event) {
        Ok(()) => Ok(()),
        Err(EventQueueError::Backpressure) => Err(HostError::protocol(
            "OUTPUT_BACKPRESSURE",
            "bounded event queue is full",
        )),
        Err(EventQueueError::Disconnected) => Err(HostError::protocol(
            "OUTPUT_DISCONNECTED",
            "event consumer disconnected",
        )),
    }
}

fn terminate_job(job: &OwnedHandle) -> Result<(), HostError> {
    // SAFETY: `job` is the owned containment authority for this request.
    if unsafe { TerminateJobObject(raw_handle(job), HOST_TERMINATION_EXIT) } == 0 {
        return Err(HostError::last(
            "JOB_TERMINATION_FAILED",
            "TerminateJobObject",
        ));
    }
    Ok(())
}

fn wait_for_process(process: &OwnedHandle, milliseconds: u64) -> Result<(), HostError> {
    let timeout = u32::try_from(milliseconds).unwrap_or(u32::MAX - 1);
    // SAFETY: `process` is a live synchronizable process handle.
    let wait = unsafe { WaitForSingleObject(raw_handle(process), timeout) };
    if wait != WAIT_OBJECT_0 {
        return Err(HostError::last(
            "PROCESS_WAIT_FAILED",
            "WaitForSingleObject(process)",
        ));
    }
    Ok(())
}

fn process_exit_code(process: &OwnedHandle) -> Result<u32, HostError> {
    let mut code = 0u32;
    // SAFETY: `code` is writable and `process` is a live process handle.
    if unsafe { GetExitCodeProcess(raw_handle(process), &raw mut code) } == 0 {
        return Err(HostError::last(
            "PROCESS_STATUS_FAILED",
            "GetExitCodeProcess",
        ));
    }
    Ok(code)
}

fn active_job_processes(job: &OwnedHandle) -> Result<u32, HostError> {
    let mut information = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
    let mut returned = 0u32;
    // SAFETY: output pointers and byte sizes exactly describe `information`.
    if unsafe {
        QueryInformationJobObject(
            raw_handle(job),
            JobObjectBasicAccountingInformation,
            (&raw mut information).cast(),
            u32::try_from(size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>())
                .map_err(|_| HostError::protocol("INTERNAL_ERROR", "job accounting size"))?,
            &raw mut returned,
        )
    } == 0
    {
        return Err(HostError::last(
            "JOB_QUERY_FAILED",
            "QueryInformationJobObject",
        ));
    }
    Ok(information.ActiveProcesses)
}

fn wait_for_empty_job(job: &OwnedHandle, milliseconds: u64) -> Result<u32, HostError> {
    let deadline = Instant::now() + Duration::from_millis(milliseconds);
    loop {
        let active = active_job_processes(job)?;
        if active == 0 {
            return Ok(0);
        }
        if Instant::now() >= deadline {
            return Ok(active);
        }
        thread::sleep(POLL_INTERVAL);
    }
}

fn join_io(handle: JoinHandle<()>) {
    let _ = handle.join();
}

fn emit_bootstrap_failure(error: &HostError) {
    let value = json!({
        "schema":"openprose.windows-process-host.error/1",
        "code":error.code,
        "operation":error.operation,
        "win32":error.win32
    });
    if let Ok(mut encoded) = serde_json::to_vec(&value) {
        encoded.push(b'\n');
        let _ = io::stderr().write_all(&encoded);
    }
}

fn nul_wide(value: &str) -> Vec<u16> {
    std::ffi::OsStr::new(value)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn raw_handle(handle: &OwnedHandle) -> HANDLE {
    handle.as_raw_handle().cast()
}

unsafe fn owned_handle(handle: HANDLE) -> OwnedHandle {
    // SAFETY: callers document that `handle` is a fresh non-null owned Win32
    // handle and transfer its sole close responsibility into `OwnedHandle`.
    unsafe { OwnedHandle::from_raw_handle(handle.cast()) }
}
