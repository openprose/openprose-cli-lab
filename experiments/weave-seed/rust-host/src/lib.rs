//! Synchronous local persistence for the unpublished bounded weave experiment.
pub use openprose_weave_experimental as core;

use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

pub type Result<T> = std::result::Result<T, String>;
const MAX_SAFE: u64 = 9_007_199_254_740_991;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct WireCheckpoint {
    schema: u64,
    binding: String,
    evidence: String,
    disposition: String,
    valid_until: u64,
    pending: Option<String>,
    attempts: u64,
    settlement: Option<WireSettlement>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireSettlement {
    binding: String,
    attempt: String,
    outcome: String,
    receipt: String,
}

fn nonblank(value: &str) -> bool {
    value.chars().any(|c| !c.is_whitespace() && c != '\u{feff}')
}

fn validate(w: &WireCheckpoint) -> Result<()> {
    if w.schema != 1 || w.valid_until > MAX_SAFE || w.attempts > MAX_SAFE {
        return Err("invalid checkpoint schema or integer".into());
    }
    if !["unknown", "satisfied", "work-needed"].contains(&w.disposition.as_str()) {
        return Err("invalid checkpoint disposition".into());
    }
    if w.pending.as_ref().is_some_and(|v| !nonblank(v)) {
        return Err("invalid pending attempt".into());
    }
    if let Some(r) = &w.settlement {
        if [&r.binding, &r.attempt, &r.outcome, &r.receipt].iter().any(|v| !nonblank(v))
            || !["completed", "not-applied"].contains(&r.outcome.as_str()) {
            return Err("invalid settlement record".into());
        }
    }
    Ok(())
}

/// Decode the exact shared checkpoint schema. Corrupt state is never reset.
pub fn decode(text: &str) -> Result<core::Checkpoint> {
    // Direct typed deserialization rejects duplicate fields as well as wrong types.
    let w: WireCheckpoint = serde_json::from_str(text).map_err(|e| format!("invalid checkpoint JSON: {e}"))?;
    // Option fields must be present explicitly rather than defaulting to null.
    let value: serde_json::Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let object = value.as_object().ok_or("checkpoint must be an object")?;
    if object.len() != 8 || !object.contains_key("pending") || !object.contains_key("settlement") {
        return Err("checkpoint requires every schema field".into());
    }
    validate(&w)?;
    Ok(core::Checkpoint {
        binding: w.binding, evidence: w.evidence,
        disposition: match w.disposition.as_str() {
            "satisfied" => core::Judgment::Satisfied,
            "work-needed" => core::Judgment::WorkNeeded,
            _ => core::Judgment::Unknown,
        },
        valid_until: w.valid_until, pending: w.pending, attempts: w.attempts,
        settlement: w.settlement.map(|r| core::Settlement { binding: r.binding, attempt: r.attempt, outcome: r.outcome, receipt: r.receipt }),
    })
}

/// Validate before allocating a temporary file or changing existing state.
pub fn encode(cp: &core::Checkpoint) -> Result<String> {
    let w = WireCheckpoint {
        schema: 1, binding: cp.binding.clone(), evidence: cp.evidence.clone(),
        disposition: cp.disposition.as_str().into(), valid_until: cp.valid_until,
        pending: cp.pending.clone(), attempts: cp.attempts,
        settlement: cp.settlement.as_ref().map(|r| WireSettlement { binding: r.binding.clone(), attempt: r.attempt.clone(), outcome: r.outcome.clone(), receipt: r.receipt.clone() }),
    };
    validate(&w)?;
    serde_json::to_string(&w).map_err(|e| format!("cannot encode checkpoint: {e}"))
}

#[derive(Clone, Debug)]
pub struct LocalHost { root: Result<PathBuf> }
impl LocalHost {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: std::path::absolute(root.into()).map_err(|e| format!("resolve host directory: {e}")) }
    }

    /// Hold the directory lock for the complete synchronous operation.
    pub fn with_lock<T>(&self, operation: impl FnOnce(&mut LockedStore) -> Result<T>) -> Result<T> {
        #[cfg(not(unix))]
        return Err("durable checkpoint host currently requires POSIX".into());
        let root = self.root.as_ref().map_err(Clone::clone)?;
        fs::create_dir_all(root).map_err(|e| format!("create host directory: {e}"))?;
        let lock = root.join("lock");
        fs::create_dir(&lock).map_err(|e| if e.kind() == io::ErrorKind::AlreadyExists { "busy: host lock exists".into() } else { format!("create host lock: {e}") })?;
        let identity = lock_identity(&lock)?;
        let mut store = LockedStore { root: root.clone(), lock, identity, owned: true, poisoned: false };
        let result = operation(&mut store);
        let release = store.release();
        match (result, release) {
            (Ok(value), Ok(())) => Ok(value),
            (Err(error), Ok(())) | (Ok(_), Err(error)) => Err(error),
            (Err(error), Err(release)) => Err(format!("{error}; {release}")),
        }
    }

    pub fn step(&self, binding: &str, capabilities: &mut impl Capabilities, max_attempts: u64) -> Result<(core::Checkpoint, &'static str)> {
        self.with_lock(|store| {
            let checkpoint = store.load()?;
            core::reconcile(binding, &checkpoint, &mut Bridge { store, capabilities }, max_attempts)
        })
    }

    pub fn settle(&self, binding: &str, attempt: &str, outcome: &str, receipt: &str) -> Result<core::Checkpoint> {
        self.with_lock(|store| {
            let checkpoint = store.load()?;
            let settled = core::settle_pending(&checkpoint, binding, attempt, outcome, receipt)?;
            store.save(&settled)?;
            Ok(settled)
        })
    }
}

/// Only constructed while this process owns the lock. Cannot outlive the operation.
pub struct LockedStore { root: PathBuf, lock: PathBuf, identity: (u64, u64), owned: bool, poisoned: bool }
impl LockedStore {
    pub fn load(&self) -> Result<core::Checkpoint> {
        if self.poisoned { return Err("uncertain checkpoint save; lock retained for reconciliation".into()); }
        match fs::read_to_string(self.root.join("checkpoint.json")) {
            Ok(text) => decode(&text),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(core::Checkpoint::default()),
            Err(e) => Err(format!("read checkpoint: {e}")),
        }
    }

    pub fn save(&mut self, checkpoint: &core::Checkpoint) -> Result<()> {
        self.save_with_sync(checkpoint, sync_directory)
    }

    fn save_with_sync(&mut self, checkpoint: &core::Checkpoint, sync: impl FnOnce(&Path) -> Result<()>) -> Result<()> {
        if self.poisoned { return Err("uncertain checkpoint save; lock retained for reconciliation".into()); }
        let text = encode(checkpoint)?;
        let (path, mut file) = create_temporary(&self.root)?;
        let result = (|| {
            file.write_all(text.as_bytes()).map_err(|e| format!("write checkpoint: {e}"))?;
            file.flush().map_err(|e| format!("flush checkpoint: {e}"))?;
            file.sync_all().map_err(|e| format!("sync checkpoint: {e}"))?;
            drop(file);
            // A rename error may have ambiguous publication on an I/O failure.
            self.poisoned = true;
            fs::rename(&path, self.root.join("checkpoint.json")).map_err(|e| format!("replace checkpoint: {e}"))?;
            if let Err(error) = sync(&self.root) {
                self.poisoned = true;
                return Err(format!("{error}; checkpoint publication uncertain; lock retained"));
            }
            self.poisoned = false;
            Ok(())
        })();
        if result.is_err() { let _ = fs::remove_file(&path); }
        result
    }

    fn release(&mut self) -> Result<()> {
        if self.poisoned { return Err("uncertain checkpoint save; lock retained for reconciliation".into()); }
        if self.owned {
            if lock_identity(&self.lock)? != self.identity {
                self.owned = false;
                return Err("host lock ownership changed; replacement lock preserved".into());
            }
            fs::remove_dir(&self.lock).map_err(|e| format!("release host lock: {e}"))?;
            self.owned = false;
        }
        Ok(())
    }
}
impl Drop for LockedStore {
    fn drop(&mut self) { if self.owned && !self.poisoned && lock_identity(&self.lock).ok() == Some(self.identity) { let _ = fs::remove_dir(&self.lock); } }
}
#[cfg(unix)]
fn lock_identity(path: &Path) -> Result<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    let metadata = fs::symlink_metadata(path).map_err(|e| format!("inspect host lock: {e}"))?;
    if !metadata.is_dir() { return Err("host lock is no longer the owned directory".into()); }
    Ok((metadata.dev(), metadata.ino()))
}
#[cfg(not(unix))]
fn lock_identity(_: &Path) -> Result<(u64, u64)> { Err("POSIX lock identity required".into()) }
fn create_temporary(root: &Path) -> Result<(PathBuf, File)> {
    for _ in 0..128 {
        let id = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = root.join(format!("checkpoint.tmp-{}-{id}", std::process::id()));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(format!("create checkpoint temporary file: {e}")),
        }
    }
    Err("temporary checkpoint filename collisions".into())
}
#[cfg(unix)]
fn sync_directory(root: &Path) -> Result<()> {
    File::open(root).and_then(|f| f.sync_all()).map_err(|e| format!("sync checkpoint directory: {e}"))
}
#[cfg(not(unix))]
fn sync_directory(_: &Path) -> Result<()> {
    Err("durable checkpoint host currently requires POSIX directory sync".into())
}

pub trait Capabilities {
    fn observe(&mut self) -> Result<core::Evidence>;
    fn assess(&mut self, evidence: &core::Evidence) -> Result<core::Judgment>;
    fn act(&mut self, evidence: &core::Evidence, attempt: &str) -> Result<()>;
    fn clock(&mut self) -> u64;
    fn new_id(&mut self) -> String;
}
struct Bridge<'a, C> { store: &'a mut LockedStore, capabilities: &'a mut C }
impl<C: Capabilities> core::Host for Bridge<'_, C> {
    fn observe(&mut self) -> Result<core::Evidence> { self.capabilities.observe() }
    fn assess(&mut self, evidence: &core::Evidence) -> Result<core::Judgment> { self.capabilities.assess(evidence) }
    fn act(&mut self, evidence: &core::Evidence, attempt: &str) -> Result<()> { self.capabilities.act(evidence, attempt) }
    fn save(&mut self, checkpoint: &core::Checkpoint) -> Result<()> { self.store.save(checkpoint) }
    fn clock(&mut self) -> u64 { self.capabilities.clock() }
    fn new_id(&mut self) -> String { self.capabilities.new_id() }
}

#[cfg(test)]
mod persistence_failure_tests {
    use super::*;
    #[test]
    fn post_rename_sync_failure_retains_lock_and_blocks_restart() {
        let root = std::env::temp_dir().join(format!("weave-poison-{}", std::process::id()));
        fs::create_dir(&root).unwrap();
        let host = LocalHost::new(&root);
        let cp = core::Checkpoint { binding: "new-state".into(), ..Default::default() };
        let error = host.with_lock(|store| {
            let failed = store.save_with_sync(&cp, |_| Err("injected directory sync failure".into()));
            assert!(failed.is_err());
            assert!(store.load().is_err());
            assert!(store.save(&core::Checkpoint::default()).is_err());
            failed
        }).unwrap_err();
        assert!(error.contains("lock retained"));
        assert!(root.join("lock").is_dir());
        assert_eq!(decode(&fs::read_to_string(root.join("checkpoint.json")).unwrap()).unwrap(), cp);
        assert!(host.with_lock(|_| Ok(())).unwrap_err().starts_with("busy"));
        // A trusted operator removes this test-owned directory after inspection.
        fs::remove_dir_all(&root).unwrap();
    }
}
