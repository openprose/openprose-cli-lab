//! Native local coordinator. Configuration is trusted executable policy.
#[cfg(not(unix))]
compile_error!("weave-rust-local currently requires Unix");
mod process;
use serde::{Serialize, Serializer, ser::SerializeMap};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, fs, io::Read, path::{Component, Path, PathBuf}, sync::atomic::{AtomicBool, AtomicU64, Ordering}, time::{Duration, SystemTime, UNIX_EPOCH}};
use std::os::unix::fs::{MetadataExt, DirBuilderExt, OpenOptionsExt};
use weave_file_binding_experiment::{Config as BindingConfig, FileBinding, Evidence as WireEvidence, MAX_SAFE_INTEGER};
use weave_local_host_experiment::{Capabilities, LocalHost, core, encode, decode};
pub type Result<T> = std::result::Result<T, String>;
static NEXT_ID: AtomicU64 = AtomicU64::new(0);
static NOT_CANCELLED: AtomicBool = AtomicBool::new(false);
fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn nonblank(s: &str) -> bool { s.chars().any(|c| !matches!(c, '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}')) }
fn resolve(base: &Path, path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in base.join(path).components() {
        match component { Component::ParentDir => { out.pop(); }, Component::CurDir => {}, other => out.push(other.as_os_str()) }
    }
    out
}
fn read_config(path: &Path) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    let file = fs::OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK).open(path).map_err(|e| format!("read configuration: {e}"))?;
    let metadata = file.metadata().map_err(|e| format!("inspect configuration: {e}"))?;
    if !metadata.is_file() || metadata.len() > 1_048_576 { return Err("configuration must be a regular file at most 1 MiB".into()); }
    file.take(1_048_577).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() > 1_048_576 { return Err("configuration exceeds 1 MiB".into()); }
    Ok(bytes)
}
fn safe(value: Option<&Value>, default: Option<u64>, positive: bool) -> Result<u64> {
    let n = match value { Some(v) => v.as_f64().ok_or("invalid numeric bound")?, None => default.ok_or("missing numeric bound")? as f64 };
    if !n.is_finite() || n < 0.0 || (positive && n == 0.0) || n.fract() != 0.0 || n > MAX_SAFE_INTEGER as f64 { return Err("invalid numeric bound".into()); }
    Ok(n as u64)
}
struct Selection { path: PathBuf, bytes: Vec<u8>, directory: PathBuf }
fn select(path: &Path) -> Result<Selection> {
    let path = resolve(&std::env::current_dir().map_err(|e| e.to_string())?, path);
    let bytes = read_config(&path)?;
    let text = std::str::from_utf8(&bytes).map_err(|_| "configuration is not UTF-8")?;
    let value: Value = serde_json::from_str(text.strip_prefix('\u{feff}').unwrap_or(text)).map_err(|e| format!("invalid configuration: {e}"))?;
    if safe(value.get("schema"), None, false)? != 1 { return Err("invalid schema".into()); }
    let directory = value.get("checkpointDirectory").and_then(Value::as_str).filter(|s| nonblank(s)).ok_or("explicit checkpointDirectory required")?;
    let directory = resolve(path.parent().ok_or("configuration parent unavailable")?, Path::new(directory));
    Ok(Selection { path, bytes, directory })
}
fn present(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) { Ok(_) => Ok(true), Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false), Err(e) => Err(e.to_string()) }
}
/// Diagnostic only: does not create files or establish an atomic authority snapshot.
pub fn status_config(path: impl AsRef<Path>) -> Result<Value> {
    let selected = select(path.as_ref())?;
    let checkpoint = match fs::read_to_string(selected.directory.join("checkpoint.json")) {
        Ok(text) => {
            let mut value: Value = serde_json::from_str(&encode(&decode(&text)?)?).map_err(|e| e.to_string())?;
            value.as_object_mut().ok_or("invalid checkpoint projection")?.remove("schema");
            value
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Value::Null,
        Err(e) => return Err(format!("read checkpoint: {e}")),
    };
    Ok(json!({"serviceOwned":present(&selected.directory.join("service.lock"))?,"checkpointLocked":present(&selected.directory.join("lock"))?,"checkpoint":checkpoint}))
}

struct OrderedEnvironment(Vec<(String, String)>);
impl Serialize for OrderedEnvironment {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(self.0.len()))?;
        for (key, value) in &self.0 { map.serialize_entry(key, value)?; }
        map.end()
    }
}
#[derive(Serialize)]
struct Policy<'a> { version: &'a str, assessor: &'a [String], actor: &'a [String], environment: &'a OrderedEnvironment, #[serde(rename="timeoutMs")] timeout_ms: u64, #[serde(rename="maxOutputBytes")] max_output_bytes: u64 }
fn argv(value: Option<&Value>) -> Result<Vec<String>> {
    let list = value.and_then(Value::as_array).ok_or("explicit command argument array required")?;
    let command: Vec<String> = list.iter().map(|v| v.as_str().map(str::to_owned).ok_or_else(|| "command arguments must be strings".to_string())).collect::<Result<_>>()?;
    if command.is_empty() || !Path::new(&command[0]).is_absolute() || command.iter().any(|s| s.contains('\0')) { return Err("absolute executable and NUL-free arguments required".into()); }
    Ok(command)
}
struct Prepared { bound: FileBinding, root: PathBuf, environment: OrderedEnvironment, assessor: Vec<String>, actor: Vec<String>, max_attempts: u64, timeout_ms: u64, max_output_bytes: u64, environment_keys: Vec<String>, missing_environment: Vec<String>, source_count: usize }
fn prepare(selected: &Selection, allow_missing_environment: bool) -> Result<Prepared> {
    // The integration runner rejects a BOM even though status selection strips it.
    let config: Value = serde_json::from_slice(&selected.bytes).map_err(|e| format!("invalid configuration: {e}"))?;
    let object = config.as_object().ok_or("configuration must be an object")?;
    if object.contains_key("environment") { return Err("use environmentKeys, not environment values, in configuration files".into()); }
    let version = config.get("capabilityVersion").and_then(Value::as_str).filter(|s| nonblank(s)).ok_or("explicit capabilityVersion required")?;
    let max_attempts = safe(config.get("maxAttempts"), None, false)?;
    let timeout_ms = safe(config.get("timeoutMs"), Some(30000), true)?;
    let max_output_bytes = safe(config.get("maxOutputBytes"), Some(1048576), true)?;
    let assessor = argv(config.get("assessor"))?;
    let actor = argv(config.get("actor"))?;
    let mut environment = OrderedEnvironment(Vec::new());
    let mut seen = HashSet::new();
    let mut environment_keys = Vec::new();
    let mut missing_environment = Vec::new();
    if let Some(keys) = config.get("environmentKeys").filter(|v| !v.is_null()) {
        for key in keys.as_array().ok_or("environmentKeys must be an array")? {
            let key = key.as_str().ok_or("environment key must be a string")?;
            let mut chars = key.chars();
            if !chars.next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_') || !chars.all(|c| c.is_ascii_alphanumeric() || c == '_') || !seen.insert(key) { return Err("invalid environment key selection".into()); }
            environment_keys.push(key.to_owned());
            match std::env::var(key) {
                Ok(value) => environment.0.push((key.into(), value)),
                Err(_) => {
                    missing_environment.push(key.to_owned());
                    if !allow_missing_environment { return Err("selected environment variable unavailable".into()); }
                }
            }
        }
    }
    let root_value = match config.get("root") { None | Some(Value::Null) => ".", Some(v) => v.as_str().ok_or("root must be a string")? };
    let root = resolve(selected.path.parent().ok_or("configuration parent unavailable")?, Path::new(root_value));
    let policy = hash(&serde_json::to_vec(&Policy { version, assessor:&assessor, actor:&actor, environment:&environment, timeout_ms, max_output_bytes }).map_err(|e| e.to_string())?);
    let mut binding_config = config.clone();
    binding_config["root"] = root.to_str().ok_or("root is not UTF-8")?.into();
    binding_config["policy"] = policy.into();
    let binding_config = serde_json::from_value::<BindingConfig>(binding_config).map_err(|e| format!("invalid source binding: {e}"))?;
    let source_count = 1 + binding_config.contracts.len() + binding_config.evidence.len();
    let bound = FileBinding::new(binding_config)?;
    Ok(Prepared { bound, root, environment, assessor, actor, max_attempts, timeout_ms, max_output_bytes, environment_keys, missing_environment, source_count })
}
/// Offline diagnostic. Never launches capabilities, creates state or takes ownership.
/// "configured" means local inputs are available, not provider or semantic verification.
pub fn check_config(path: impl AsRef<Path>) -> Value {
    let mut result = json!({
        "schema":"openprose.weave-check/1", "status":"blocked",
        "providerVerified":false, "semanticAssessment":false,
        "runtime":{"name":"rust", "version":env!("CARGO_PKG_VERSION")},
        "configuration":"unchecked", "sources":{"status":"unchecked", "selectedCount":null},
        "executables":{"assessor":"unchecked", "actor":"unchecked"},
        "environment":{"selected":[], "missing":[]},
        "checkpoint":{"status":"unchecked", "serviceOwned":null, "locked":null}, "errors":[]
    });
    let mut errors: Vec<&str> = Vec::new();
    let prepared = match select(path.as_ref()).and_then(|selected| prepare(&selected, true)) {
        Ok(prepared) => prepared,
        Err(_) => {
            result["configuration"] = "invalid".into();
            result["errors"] = json!(["CONFIGURATION_INVALID_OR_UNAVAILABLE"]);
            return result;
        }
    };
    result["configuration"] = "valid".into();
    result["environment"] = json!({"selected":prepared.environment_keys, "missing":prepared.missing_environment});
    if !prepared.missing_environment.is_empty() { errors.push("ENVIRONMENT_MISSING"); }
    for (role, command, code) in [
        ("assessor", &prepared.assessor, "ASSESSOR_EXECUTABLE_UNAVAILABLE"),
        ("actor", &prepared.actor, "ACTOR_EXECUTABLE_UNAVAILABLE")
    ] {
        let available = fs::metadata(&command[0]).is_ok_and(|m| m.is_file()) &&
            std::ffi::CString::new(command[0].as_bytes()).is_ok_and(|p| unsafe { libc::access(p.as_ptr(), libc::X_OK) } == 0);
        result["executables"][role] = if available { "available" } else { "unavailable" }.into();
        if !available { errors.push(code); }
    }
    let gap = prepared.bound.observe(now_ms()).map(|e| e.gap).unwrap_or(true);
    result["sources"] = json!({"status":if gap {"gap"} else {"available"}, "selectedCount":prepared.source_count});
    if gap { errors.push("SELECTED_SOURCE_GAP"); }
    match status_config(path) {
        Ok(status) => {
            let checkpoint = &status["checkpoint"];
            let pending = !checkpoint.is_null() && !checkpoint["pending"].is_null();
            result["checkpoint"] = json!({
                "status":if checkpoint.is_null() {"absent"} else if pending {"pending"} else {"present"},
                "serviceOwned":status["serviceOwned"], "locked":status["checkpointLocked"]
            });
            if pending { errors.push("RECOVERY_REQUIRED"); }
            if status["serviceOwned"] == true || status["checkpointLocked"] == true { errors.push("EXISTING_OWNER_OR_HOST_LOCK"); }
        }
        Err(_) => {
            result["checkpoint"]["status"] = "invalid-or-unreadable".into();
            errors.push("CHECKPOINT_INVALID_OR_UNREADABLE");
        }
    }
    result["status"] = if errors.is_empty() { "configured" } else { "blocked" }.into();
    result["errors"] = json!(errors);
    result
}

fn now_ms() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis().min(u64::MAX as u128) as u64).unwrap_or(u64::MAX) }
fn evidence(e: &core::Evidence) -> WireEvidence { WireEvidence { identity:e.identity.clone(), payload:e.payload.clone(), observed_at:e.observed_at, valid_until:e.valid_until, gap:e.gap } }
#[derive(Serialize)]
struct Input<'a> { schema: &'static str, evidence: WireEvidence, attempt: Option<&'a str> }
struct Bridge<'a> { prepared: &'a Prepared, cancelled: &'a AtomicBool }
impl Bridge<'_> {
    fn invoke(&self, command: &[String], value: &core::Evidence, attempt: Option<&str>) -> Result<Vec<u8>> {
        let mut input = serde_json::to_vec(&Input { schema:"openprose.weave-input/1", evidence:evidence(value), attempt }).map_err(|e| e.to_string())?;
        input.push(b'\n');
        process::run_process(command, &self.prepared.root, &self.prepared.environment.0, &input, self.prepared.timeout_ms, self.prepared.max_output_bytes, self.cancelled)
    }
}
impl Capabilities for Bridge<'_> {
    fn observe(&mut self) -> Result<core::Evidence> {
        let e = self.prepared.bound.observe(now_ms())?;
        Ok(core::Evidence { identity:e.identity,payload:e.payload,observed_at:e.observed_at,valid_until:e.valid_until,gap:e.gap })
    }
    fn assess(&mut self, e: &core::Evidence) -> Result<core::Judgment> { parse_judgment(&self.invoke(&self.prepared.assessor,e,None)?) }
    fn act(&mut self, e: &core::Evidence, attempt: &str) -> Result<()> { self.invoke(&self.prepared.actor,e,Some(attempt))?; Ok(()) }
    fn clock(&mut self) -> u64 { now_ms() }
    fn new_id(&mut self) -> String {
        format!("rust-local-{}-{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).map(|d|d.as_nanos()).unwrap_or(0), NEXT_ID.fetch_add(1,Ordering::Relaxed))
    }
}
pub fn parse_judgment(bytes: &[u8]) -> Result<core::Judgment> {
    let text = std::str::from_utf8(bytes).map_err(|_| "invalid assessor UTF-8")?;
    // Consume only literal grammar, matching Bun's no-extra-fields/no-escape protocol.
    let compact: String = text.chars().filter(|c| !matches!(c, ' ' | '\t' | '\r' | '\n')).collect();
    let (expected, result) = match compact.as_str() {
        "{\"judgment\":\"satisfied\"}" => ("satisfied",core::Judgment::Satisfied),
        "{\"judgment\":\"work-needed\"}" => ("work-needed",core::Judgment::WorkNeeded),
        "{\"judgment\":\"unknown\"}" => ("unknown",core::Judgment::Unknown),
        _ => return Err("invalid assessor result".into()),
    };
    let parsed: Value = serde_json::from_str(text).map_err(|_| "invalid assessor JSON")?;
    if parsed.get("judgment").and_then(Value::as_str) != Some(expected) { return Err("invalid assessor result".into()); }
    Ok(result)
}
#[derive(Debug)]
pub struct StepResult { pub checkpoint: core::Checkpoint, pub status: &'static str }
impl StepResult {
    pub fn projection(&self) -> Value { json!({"status":self.status,"attempts":self.checkpoint.attempts,"pending":self.checkpoint.pending}) }
}
fn identity(path: &Path) -> Result<(u64,u64)> {
    let info = fs::symlink_metadata(path).map_err(|e| format!("inspect service lock: {e}"))?;
    if !info.is_dir() { return Err("service lock ownership changed".into()); }
    Ok((info.dev(),info.ino()))
}
pub struct Owner { selected: Selection, lock: PathBuf, identity: (u64,u64), active: bool }
pub fn acquire_owner(path: impl AsRef<Path>) -> Result<Owner> {
    let selected = select(path.as_ref())?;
    fs::DirBuilder::new().recursive(true).mode(0o700).create(&selected.directory).map_err(|e| e.to_string())?;
    let lock = selected.directory.join("service.lock");
    fs::DirBuilder::new().mode(0o700).create(&lock).map_err(|e| if e.kind()==std::io::ErrorKind::AlreadyExists { "local service busy; existing owner lock requires completion or trusted reconciliation".into() } else { format!("create service lock: {e}") })?;
    let identity = identity(&lock)?;
    Ok(Owner { selected, lock, identity, active:true })
}
impl Owner {
    fn check(&self) -> Result<()> {
        if !self.active { return Err("service owner released".into()); }
        if identity(&self.lock)? != self.identity { return Err("service lock ownership changed".into()); }
        Ok(())
    }
    pub fn step(&self) -> Result<StepResult> { self.step_with_cancel(&NOT_CANCELLED) }
    pub fn step_with_cancel(&self, cancelled: &AtomicBool) -> Result<StepResult> {
        self.check()?;
        let current = select(&self.selected.path)?;
        if current.bytes != self.selected.bytes { return Err("configuration changed during service; stop and review before restart".into()); }
        if cancelled.load(Ordering::Relaxed) { return Err("service cancelled".into()); }
        let prepared = prepare(&current, false)?;
        let host = LocalHost::new(&self.selected.directory);
        let (checkpoint,status) = host.step(prepared.bound.binding(), &mut Bridge { prepared:&prepared,cancelled }, prepared.max_attempts)?;
        Ok(StepResult { checkpoint,status })
    }
    pub fn release(&mut self) -> Result<()> { self.check()?; fs::remove_dir(&self.lock).map_err(|e| e.to_string())?; self.active=false; Ok(()) }
}
impl Drop for Owner { fn drop(&mut self) { if self.active && identity(&self.lock).ok()==Some(self.identity) { let _=fs::remove_dir(&self.lock); } } }
pub fn step_config(path: impl AsRef<Path>) -> Result<StepResult> {
    let mut owner = acquire_owner(path)?;
    let result = owner.step();
    let release = owner.release();
    match (result,release) { (Ok(value),Ok(()))=>Ok(value), (Err(e),Ok(())) | (Ok(_),Err(e))=>Err(e), (Err(e),Err(r))=>Err(format!("{e}; {r}")) }
}
#[derive(Debug)]
pub struct ServeResult { pub steps:u64, pub stopped:&'static str, pub last:Option<StepResult> }
pub fn serve_config(path: impl AsRef<Path>, poll_ms:u64, max_steps:u64, cancelled:&AtomicBool, mut on_step:impl FnMut(&StepResult,u64)->Result<()>) -> Result<ServeResult> {
    if !(1..=3_600_000).contains(&poll_ms) || !(1..=1_000_000).contains(&max_steps) { return Err("bounded pollMs (1..3600000) and maxSteps (1..1000000) required".into()); }
    let mut owner = acquire_owner(path)?;
    let operation = (|| {
        let mut steps=0; let mut last=None;
        while steps<max_steps && !cancelled.load(Ordering::Relaxed) {
            let result = owner.step_with_cancel(cancelled)?; steps+=1; on_step(&result,steps)?;
            let pending=result.checkpoint.pending.is_some();last=Some(result);
            if pending { break; }
            if steps<max_steps {
                let start=std::time::Instant::now();
                while start.elapsed()<Duration::from_millis(poll_ms) && !cancelled.load(Ordering::Relaxed) { std::thread::sleep(Duration::from_millis(10).min(Duration::from_millis(poll_ms).saturating_sub(start.elapsed()))); }
            }
        }
        let stopped=if cancelled.load(Ordering::Relaxed) {"cancelled"} else if last.as_ref().is_some_and(|r|r.checkpoint.pending.is_some()) {"pending"} else {"step-limit"};
        Ok(ServeResult {steps,stopped,last})
    })();
    let released=owner.release();
    match (operation,released) { (Ok(value),Ok(()))=>Ok(value), (Err(e),Ok(())) | (Ok(_),Err(e))=>Err(e), (Err(e),Err(r))=>Err(format!("{e}; {r}")) }
}
