//! Deterministic file fixture, not a model, kernel invocation, or general executor.
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use weave_local_host_experiment::{core::{Evidence, Judgment}, Capabilities, LocalHost};

struct FileContract {
    root: PathBuf,
    actions: usize,
}
impl FileContract {
    fn read_day(&self, name: &str) -> Result<String, String> {
        let value = fs::read_to_string(self.root.join(name)).map_err(|e| e.to_string())?;
        if !["Monday", "Tuesday", "Wednesday"].contains(&value.as_str()) {
            return Err(format!("{name} does not contain one supported fixture day"));
        }
        Ok(value)
    }
}
impl Capabilities for FileContract {
    fn observe(&mut self) -> Result<Evidence, String> {
        let now = self.clock();
        let values = self.read_day("desired.txt").and_then(|desired| {
            self.read_day("actual.txt").map(|actual| (desired, actual))
        });
        Ok(match values {
            Ok((desired, actual)) => {
                // The full tiny payload is a collision-free identity for this fixture.
                let payload = serde_json::to_string(&(desired, actual)).map_err(|e| e.to_string())?;
                Evidence { identity: payload.clone(), payload, observed_at: now, valid_until: now + 60, gap: false }
            }
            Err(_) => Evidence { identity: "unavailable-fixture-source".into(), payload: String::new(), observed_at: now, valid_until: now + 60, gap: true },
        })
    }
    fn assess(&mut self, evidence: &Evidence) -> Result<Judgment, String> {
        let (desired, actual): (String, String) = serde_json::from_str(&evidence.payload).map_err(|e| e.to_string())?;
        Ok(if actual == desired { Judgment::Satisfied } else { Judgment::WorkNeeded })
    }
    fn act(&mut self, evidence: &Evidence, _: &str) -> Result<(), String> {
        let (desired, _): (String, String) = serde_json::from_str(&evidence.payload).map_err(|e| e.to_string())?;
        self.actions += 1;
        // This explicitly bound sample effect only edits the fixture's actual file.
        fs::write(self.root.join("actual.txt"), desired).map_err(|e| e.to_string())
    }
    fn clock(&mut self) -> u64 {
        SystemTime::now().duration_since(UNIX_EPOCH).expect("clock predates Unix epoch").as_secs()
    }
    fn new_id(&mut self) -> String { format!("fixture-action-{}", self.actions + 1) }
}
fn event(root: &Path, world: &mut FileContract, label: &str, expected: &str) -> Result<(), String> {
    // Reconstruct the host each time; the checkpoint, not the object, retains state.
    let host = LocalHost::new(root.join("checkpoint"));
    let binding = format!("weekday-file-fixture-v1:{}:actual-equals-desired:write-actual-only", root.display());
    let (checkpoint, status) = host.step(&binding, world, 2)?;
    println!("{}", serde_json::json!({
        "event": label, "status": status, "attempts": checkpoint.attempts,
        "actions": world.actions, "actual": world.read_day("actual.txt").ok(),
    }));
    if status != expected { return Err(format!("fixture event {label}: expected {expected}, observed {status}")); }
    Ok(())
}
fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() != 2 { return Err("usage: cargo run --example persistent -- NEW_DIRECTORY".into()); }
    let root = std::path::absolute(PathBuf::from(&args[1])).map_err(|e| e.to_string())?;
    // Refuse existing paths before writing any fixture input or checkpoint.
    fs::create_dir(&root).map_err(|e| format!("create a NEW fixture directory {}: {e}", root.display()))?;
    fs::write(root.join("desired.txt"), "Tuesday").map_err(|e| e.to_string())?;
    fs::write(root.join("actual.txt"), "Monday").map_err(|e| e.to_string())?;
    eprintln!("Deterministic local-file fixture at {}. No model or kernel is invoked. Files remain for inspection.", root.display());
    let mut world = FileContract { root: root.clone(), actions: 0 };
    event(&root, &mut world, "initial-repair", "satisfied")?;
    event(&root, &mut world, "reopened-reuse", "reused")?;
    fs::write(root.join("desired.txt"), "Wednesday").map_err(|e| e.to_string())?;
    event(&root, &mut world, "desired-changed", "satisfied")?;
    fs::write(root.join("desired.txt"), [0xff]).map_err(|e| e.to_string())?;
    event(&root, &mut world, "source-corrupt", "evidence-gap")?;
    fs::remove_file(root.join("desired.txt")).map_err(|e| e.to_string())?;
    event(&root, &mut world, "source-unavailable", "evidence-gap")?;
    if world.actions != 2 { return Err("fixture must perform exactly two actions".into()); }
    Ok(())
}
fn main() {
    if let Err(error) = run() { eprintln!("{error}"); std::process::exit(1); }
}
