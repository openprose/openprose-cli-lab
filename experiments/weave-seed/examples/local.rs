#[allow(dead_code)]
#[path = "../rust/lib.rs"]
mod weave;
use weave::{Checkpoint, Evidence, Host, Judgment};

// In-memory saves demonstrate callbacks. They do not provide crash recovery.
struct LocalHost { actual: String, saved: Checkpoint, actions: u64 }
impl Host for LocalHost {
    fn observe(&mut self) -> Result<Evidence, String> {
        Ok(Evidence { identity: format!("Tuesday/{}", self.actual), payload: self.actual.clone(), observed_at: 0, valid_until: 100, gap: false })
    }
    fn assess(&mut self, evidence: &Evidence) -> Result<Judgment, String> {
        Ok(if evidence.payload == "Tuesday" { Judgment::Satisfied } else { Judgment::WorkNeeded })
    }
    fn act(&mut self, _evidence: &Evidence, attempt: &str) -> Result<(), String> {
        assert_eq!(self.saved.pending.as_deref(), Some(attempt));
        self.actions += 1;
        self.actual = "Tuesday".into();
        Ok(())
    }
    fn save(&mut self, checkpoint: &Checkpoint) -> Result<(), String> { self.saved = checkpoint.clone(); Ok(()) }
    fn clock(&mut self) -> u64 { 10 }
    fn new_id(&mut self) -> String { "example-attempt-1".into() }
}
fn main() -> Result<(), String> {
    let mut host = LocalHost { actual: "Monday".into(), saved: Checkpoint::default(), actions: 0 };
    let (checkpoint, first) = weave::reconcile("synthetic-date-rule-v1", &Checkpoint::default(), &mut host, 1)?;
    let (_, replay) = weave::reconcile("synthetic-date-rule-v1", &checkpoint, &mut host, 1)?;
    assert_eq!((first, replay, host.actions), ("satisfied", "reused", 1));
    println!("first={first} replay={replay} actions={}", host.actions);
    Ok(())
}
