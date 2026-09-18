use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};
use weave_local_host_experiment::{core::{Checkpoint, Evidence, Judgment}, decode, encode, Capabilities, LocalHost};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!("weave-rust-host-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
        fs::create_dir(&p).unwrap(); Self(p)
    }
}
impl Drop for Temp { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
fn fixture() -> Checkpoint { decode(include_str!("../../fixtures/checkpoint-v1.json")).unwrap() }
struct World { good: bool, actions: usize, assessments: usize, fail_actor: bool, sabotage: Option<PathBuf> }
impl World { fn bad() -> Self { Self { good: false, actions: 0, assessments: 0, fail_actor: false, sabotage: None } } }
impl Capabilities for World {
    fn observe(&mut self) -> Result<Evidence, String> { Ok(Evidence { identity: self.good.to_string(), payload: self.good.to_string(), observed_at: 0, valid_until: 100, gap: false }) }
    fn assess(&mut self, _: &Evidence) -> Result<Judgment, String> { self.assessments += 1; Ok(if self.good { Judgment::Satisfied } else { Judgment::WorkNeeded }) }
    fn act(&mut self, _: &Evidence, _: &str) -> Result<(), String> {
        self.actions += 1;
        if self.fail_actor { return Err("unknown effect".into()); }
        self.good = true; Ok(())
    }
    fn clock(&mut self) -> u64 {10}
    fn new_id(&mut self) -> String {
        // Real filesystem failure only after initial assessment has been saved.
        if let Some(root) = &self.sabotage {
            fs::rename(root.join("checkpoint.json"), root.join("previous.json")).unwrap();
            fs::create_dir(root.join("checkpoint.json")).unwrap();
        }
        "attempt-1".into()
    }
}

#[test]
fn restart_reuses_durable_satisfaction() {
    let temp = Temp::new(); let mut world = World::bad();
    let (cp, status) = LocalHost::new(&temp.0).step("v1", &mut world, 1).unwrap();
    assert_eq!(status, "satisfied"); assert_eq!(world.actions, 1); assert_eq!(cp.attempts, 1);
    let reopened = LocalHost::new(&temp.0);
    let (_, status) = reopened.step("v1", &mut world, 1).unwrap();
    assert_eq!(status, "reused"); assert_eq!(world.actions, 1); assert_eq!(world.assessments, 2);
    assert!(!temp.0.join("lock").exists());
    assert_eq!(fs::read_dir(&temp.0).unwrap().count(), 1);
}

#[test]
fn actor_error_survives_restart_then_settlement_reassesses_without_new_budget() {
    let temp = Temp::new(); let mut world = World::bad(); world.fail_actor = true;
    let (_, status) = LocalHost::new(&temp.0).step("v1", &mut world, 1).unwrap();
    assert_eq!(status, "action-outcome-unknown"); assert_eq!(world.actions, 1);
    let host = LocalHost::new(&temp.0);
    let (_, status) = host.step("v1", &mut world, 1).unwrap();
    assert_eq!(status, "recovery-needed"); assert_eq!(world.actions, 1);
    assert!(host.settle("v1", "wrong", "not-applied", "receipt").is_err());
    let cp = host.settle("v1", "attempt-1", "not-applied", "receipt").unwrap();
    assert_eq!(cp.attempts, 1); assert_eq!(cp.pending, None);
    let (_, status) = LocalHost::new(&temp.0).step("v1", &mut world, 1).unwrap();
    assert_eq!(status, "attempt-limit"); assert_eq!(world.actions, 1);
    world.good = true;
    assert_eq!(host.step("v1", &mut world, 1).unwrap().1, "satisfied");
}

#[test]
fn invalid_encoding_preserves_previous_checkpoint_and_unlocks() {
    let temp = Temp::new(); let host = LocalHost::new(&temp.0); let mut cp = fixture();
    host.with_lock(|s| s.save(&cp)).unwrap();
    let before = fs::read(temp.0.join("checkpoint.json")).unwrap();
    cp.pending = Some("  ".into());
    assert!(host.with_lock(|s| s.save(&cp)).is_err());
    assert_eq!(fs::read(temp.0.join("checkpoint.json")).unwrap(), before);
    assert!(!temp.0.join("lock").exists());
    assert_eq!(fs::read_dir(&temp.0).unwrap().count(), 1);
}

#[test]
fn malformed_state_is_never_reset() {
    let temp = Temp::new(); let host = LocalHost::new(&temp.0);
    let base = include_str!("../../fixtures/checkpoint-v1.json");
    let cases = [
        "{".into(), base.replace("\"schema\":1", "\"schema\":2"),
        base.replace("\"schema\":1", "\"schema\":1.0"),
        base.replace("\"attempts\":1", "\"attempts\":-1"),
        base.replace("\"attempts\":1", "\"attempts\":9007199254740992"),
        base.replace("\"pending\":\"attempt-1\",", ""),
        base.replace(",\"settlement\":null", ""),
        base.replace("\"settlement\":null", "\"settlement\":null,\"extra\":1"),
        base.replace("\"pending\":\"attempt-1\"", "\"pending\":\"attempt-1\",\"pending\":null"),
        base.replace("\"pending\":\"attempt-1\"", "\"pending\":false"),
        base.replace("\"settlement\":null", "\"settlement\":{\"binding\":\"v1\",\"attempt\":\"a\",\"outcome\":\"unknown\",\"receipt\":\"r\"}"),
    ];
    for text in cases {
        fs::write(temp.0.join("checkpoint.json"), &text).unwrap();
        let mut world = World::bad();
        assert!(host.step("v1", &mut world, 1).is_err(), "accepted {text}");
        assert_eq!(world.actions, 0); assert_eq!(world.assessments, 0);
        assert_eq!(fs::read_to_string(temp.0.join("checkpoint.json")).unwrap(), text);
        assert!(!temp.0.join("lock").exists());
    }
    fs::write(temp.0.join("checkpoint.json"), [0xff]).unwrap();
    assert!(host.with_lock(|s| s.load()).is_err());
}

#[test]
fn pending_save_failure_prevents_effect_and_cleans_temporary_file() {
    let temp = Temp::new(); let mut world = World::bad(); world.sabotage = Some(temp.0.clone());
    assert!(LocalHost::new(&temp.0).step("v1", &mut world, 1).is_err());
    assert_eq!(world.actions, 0); assert!(temp.0.join("lock").exists());
    assert!(!fs::read_dir(&temp.0).unwrap().any(|e| e.unwrap().file_name().to_string_lossy().contains(".tmp-")));
    let previous = decode(&fs::read_to_string(temp.0.join("previous.json")).unwrap()).unwrap();
    assert_eq!(previous.attempts, 0); assert_eq!(previous.disposition, Judgment::WorkNeeded);
}

#[test]
fn preexisting_lock_is_never_removed_and_errors_release_only_owned_lock() {
    let temp = Temp::new(); let host = LocalHost::new(&temp.0);
    fs::create_dir(temp.0.join("lock")).unwrap();
    assert!(host.with_lock(|_| Ok(())).unwrap_err().starts_with("busy"));
    assert!(temp.0.join("lock").is_dir());
    fs::remove_dir(temp.0.join("lock")).unwrap();
    assert!(host.with_lock::<()>(|_| Err("caller failed".into())).is_err());
    assert!(!temp.0.join("lock").exists());
    let _ = std::panic::catch_unwind(|| host.with_lock::<()>(|_| panic!("caller panic")));
    assert!(!temp.0.join("lock").exists());
}

fn child(root: &Path, mode: &str) -> std::process::Child {
    Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "child_process_helper", "--ignored", "--nocapture"])
        .current_dir(root).env_clear().env("WEAVE_TEST_ROOT", root).env("WEAVE_TEST_MODE", mode)
        .stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap()
}
fn wait_for(path: &Path) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !path.exists() {
        assert!(Instant::now() < deadline, "child readiness timeout");
        std::thread::sleep(Duration::from_millis(5));
    }
}
#[test]
fn separate_process_contention_and_abrupt_pending_restart() {
    let temp = Temp::new(); let mut process = child(&temp.0, "hold");
    wait_for(&temp.0.join("ready"));
    assert!(LocalHost::new(&temp.0).with_lock(|_| Ok(())).unwrap_err().starts_with("busy"));
    assert!(temp.0.join("lock").exists());
    fs::write(temp.0.join("release"), "").unwrap();
    assert!(process.wait().unwrap().success()); assert!(!temp.0.join("lock").exists());
    fs::remove_file(temp.0.join("ready")).unwrap();
    let mut process = child(&temp.0, "crash-pending");
    assert_eq!(process.wait().unwrap().code(), Some(73));
    assert!(temp.0.join("lock").exists());
    let host = LocalHost::new(&temp.0); let mut world = World::bad();
    assert!(host.step("sample-v1", &mut world, 1).unwrap_err().starts_with("busy"));
    // Test acts as trusted operator after confirming the child has exited.
    fs::remove_dir(temp.0.join("lock")).unwrap();
    let (cp, status) = host.step("sample-v1", &mut world, 1).unwrap();
    assert_eq!(status, "recovery-needed"); assert_eq!(cp.pending.as_deref(), Some("attempt-1")); assert_eq!(world.actions, 0);
}

#[test]
#[ignore = "subprocess helper, invoked explicitly by parent test"]
fn child_process_helper() {
    let root = PathBuf::from(std::env::var("WEAVE_TEST_ROOT").unwrap());
    let mode = std::env::var("WEAVE_TEST_MODE").unwrap();
    if mode == "relative-root" {
        let host = LocalHost::new(".");
        fs::create_dir(root.join("other")).unwrap();
        host.with_lock(|store| {
            std::env::set_current_dir(root.join("other")).unwrap();
            store.save(&fixture())?;
            assert_eq!(store.load()?, fixture());
            Ok(())
        }).unwrap();
        assert!(root.join("checkpoint.json").exists());
        assert!(!root.join("lock").exists());
        assert!(!root.join("other/checkpoint.json").exists());
        return;
    }
    LocalHost::new(&root).with_lock(|store| {
        if mode == "crash-pending" {
            store.save(&fixture())?;
            std::process::exit(73);
        }
        fs::write(root.join("ready"), "").unwrap();
        wait_for(&root.join("release"));
        Ok(())
    }).unwrap();
}

#[test]
fn shared_fixture_roundtrips_exact_state() {
    let expected = fixture();
    assert_eq!(decode(&encode(&expected).unwrap()).unwrap(), expected);
}

#[test]
fn replacement_lock_is_preserved_on_return_and_panic() {
    for panic in [false, true] {
        let temp = Temp::new();
        let host = LocalHost::new(&temp.0);
        let result = std::panic::catch_unwind(|| host.with_lock::<()>(|_| {
            fs::rename(temp.0.join("lock"), temp.0.join("old-lock")).unwrap();
            fs::create_dir(temp.0.join("lock")).unwrap();
            if panic { panic!("operator replaced live lock"); }
            Ok(())
        }));
        assert!(panic || result.unwrap().unwrap_err().contains("ownership changed"));
        assert!(temp.0.join("lock").is_dir());
    }
}

#[test]
fn strict_wire_numbers_bom_and_duplicate_settlement_fields_are_rejected() {
    let base = include_str!("../../fixtures/checkpoint-v1.json");
    for invalid in ["1.0", "1e0", "-0"] {
        assert!(decode(&base.replace("\"attempts\":1", &format!("\"attempts\":{invalid}"))).is_err());
    }
    assert!(decode(&format!("\u{feff}{base}")).is_err());
    let duplicate = base.replace("\"settlement\":null", "\"settlement\":{\"binding\":\"v1\",\"attempt\":\"a\",\"outcome\":\"completed\",\"receipt\":\"r\",\"receipt\":\"other\"}");
    assert!(decode(&duplicate).is_err());
}

#[test]
fn relative_root_remains_stable_when_callback_changes_working_directory() {
    let temp = Temp::new();
    assert!(child(&temp.0, "relative-root").wait().unwrap().success());
}

#[test]
fn blank_string_domain_matches_shared_unicode_whitespace_plus_bom() {
    let base = fixture();
    for blank in [" ", "\u{0085}", "\u{feff}", "\u{2028}\u{2029}\u{feff}"] {
        let mut cp = base.clone(); cp.pending = Some(blank.into());
        assert!(encode(&cp).is_err());
        let text = include_str!("../../fixtures/checkpoint-v1.json").replace("attempt-1", blank);
        assert!(decode(&text).is_err());
        cp.pending = None;
        cp.settlement = Some(weave_local_host_experiment::core::Settlement { binding: "v1".into(), attempt: "a".into(), outcome: "completed".into(), receipt: blank.into() });
        assert!(encode(&cp).is_err());
    }
    let mut cp = base;
    cp.pending = Some("\u{feff}visible\u{0085}".into());
    assert_eq!(decode(&encode(&cp).unwrap()).unwrap(), cp);
}
