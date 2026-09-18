//! Byte-compatible v1 file observation for trusted local Unix callers.
//! Reads are sequential, not an atomic filesystem snapshot or a sandbox.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs::{self, OpenOptions}, io::Read, path::{Component, Path, PathBuf}};
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
#[cfg(not(unix))]
compile_error!("The experimental observer currently supports Unix only");
pub const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;
/// JSON numeric spellings follow JavaScript safe-integer value semantics.
pub fn deserialize_safe_integer<'de, D: serde::Deserializer<'de>>(d: D) -> Result<u64, D::Error> {
    let n = f64::deserialize(d)?;
    if !n.is_finite() || n < 0.0 || n > MAX_SAFE_INTEGER as f64 || n.fract() != 0.0 {
        return Err(serde::de::Error::custom("invalid safe integer"));
    }
    Ok(n as u64)
}
fn ttl() -> u64 { 60_000 }
fn limit() -> u64 { 262_144 }
#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Config {
    pub root: String,
    pub kernel: String,
    pub contracts: Vec<String>,
    pub evidence: Vec<String>,
    pub policy: String,
    #[serde(default = "ttl", deserialize_with = "deserialize_safe_integer")]
    pub ttl_ms: u64,
    #[serde(default = "limit", deserialize_with = "deserialize_safe_integer")]
    pub limit: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Evidence {
    pub identity: String,
    pub payload: String,
    pub observed_at: u64,
    pub valid_until: u64,
    pub gap: bool,
}
#[derive(Clone, Debug, Serialize)]
struct Source { role: &'static str, path: String }
#[derive(Serialize)]
struct Declaration<'a> { version: u8, sources: &'a [Source], policy: &'a str, #[serde(rename="ttlMs")] ttl_ms: u64, limit: u64 }
#[derive(Serialize)]
struct File { role: &'static str, path: String, sha256: String, content: String }
#[derive(Serialize)]
struct Payload<'a> { version: u8, policy: &'a str, files: Vec<File> }
#[derive(Serialize)]
struct Gap<'a> { version: u8, error: &'static str, policy: &'a str }
fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn json<T: Serialize>(value: &T) -> Result<String, String> { serde_json::to_string(value).map_err(|e| e.to_string()) }
// ECMAScript String.trim whitespace (NEL is deliberately not whitespace).
fn js_blank(s: &str) -> bool {
    s.chars().all(|c| matches!(c, '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}'))
}
fn resolve(base: &Path, value: &str) -> Result<String, String> {
    let path = base.join(value);
    let mut out = PathBuf::new();
    for part in path.components() {
        match part { Component::ParentDir => { out.pop(); }, Component::CurDir => {}, other => out.push(other.as_os_str()) }
    }
    out.to_str().map(str::to_owned).ok_or_else(|| "non-UTF-8 source path".into())
}
pub struct FileBinding { base: PathBuf, config: Config, sources: Vec<Source>, binding: String }
impl FileBinding {
    pub fn new(config: Config) -> Result<Self, String> {
        if config.contracts.is_empty() || config.evidence.is_empty() || js_blank(&config.policy) {
            return Err("explicit kernel, contracts, evidence, and policy required".into());
        }
        if config.ttl_ms == 0 || config.limit == 0 || config.ttl_ms > MAX_SAFE_INTEGER || config.limit > MAX_SAFE_INTEGER {
            return Err("invalid bounds".into());
        }
        let base = fs::canonicalize(&config.root).map_err(|e| e.to_string())?;
        let mut sources = Vec::new();
        for (role, paths) in [("kernel", std::slice::from_ref(&config.kernel)), ("contract", config.contracts.as_slice()), ("evidence", config.evidence.as_slice())] {
            for path in paths {
                if path.is_empty() { return Err("invalid source path".into()); }
                sources.push(Source { role, path: resolve(&base, path)? });
            }
        }
        let binding = hash(json(&Declaration { version: 1, sources: &sources, policy: &config.policy, ttl_ms: config.ttl_ms, limit: config.limit })?.as_bytes());
        Ok(Self { base, config, sources, binding })
    }
    pub fn binding(&self) -> &str { &self.binding }
    /// Invalid time is an error. Required-source read/decode failures are evidence gaps.
    pub fn observe(&self, now_ms: u64) -> Result<Evidence, String> {
        let valid_until = now_ms.checked_add(self.config.ttl_ms).filter(|n| *n <= MAX_SAFE_INTEGER).ok_or("invalid time")?;
        let (payload, gap) = match self.read_files() {
            Ok(files) => (json(&Payload { version: 1, policy: &self.config.policy, files })?, false),
            Err(_) => (json(&Gap { version: 1, error: "required-source-unavailable", policy: &self.config.policy })?, true),
        };
        Ok(Evidence { identity: hash(payload.as_bytes()), payload, observed_at: now_ms, valid_until, gap })
    }
    fn read_files(&self) -> Result<Vec<File>, String> {
        let mut total = 0u64;
        let mut files = Vec::new();
        for source in &self.sources {
            let canonical = fs::canonicalize(&source.path).map_err(|e| e.to_string())?;
            if !canonical.starts_with(&self.base) { return Err("source outside root".into()); }
            let mut file = OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK).open(canonical).map_err(|e| e.to_string())?;
            let info = file.metadata().map_err(|e| e.to_string())?;
            let remaining = self.config.limit - total;
            if !info.is_file() || info.len() > remaining { return Err("source limit or non-file".into()); }
            let mut bytes = Vec::new();
            // One extra byte detects growth beyond the aggregate limit, without allocating the configured limit upfront.
            let mut limited = (&mut file).take(remaining + 1);
            limited.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
            total += bytes.len() as u64;
            if total > self.config.limit { return Err("aggregate source limit".into()); }
            let decoded = std::str::from_utf8(&bytes).map_err(|e| e.to_string())?;
            let content = decoded.strip_prefix('\u{feff}').unwrap_or(decoded).to_owned();
            files.push(File { role: source.role, path: source.path.clone(), sha256: hash(&bytes), content });
        }
        Ok(files)
    }
}
