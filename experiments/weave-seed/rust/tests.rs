use super::*;
struct Fake { observations: Vec<char>, judgments: Vec<char>, outcome: String, observes: usize, assesses: usize, acts: usize, saves: Vec<Checkpoint>, fail_save: usize }
impl Fake {
    fn new(obs: &str, judges: &str, outcome: &str) -> Self { Self { observations: obs.chars().collect(), judgments: judges.chars().collect(), outcome: outcome.into(), observes: 0, assesses: 0, acts: 0, saves: vec![], fail_save: 0 } }
}
impl Host for Fake {
    fn observe(&mut self) -> Result<Evidence, String> {
        let c = self.observations[self.observes]; self.observes += 1; assert_ne!(c, '-');
        Ok(Evidence { identity: c.to_string(), payload: c.to_string(), observed_at: if c == 'f' {11} else {0}, valid_until: if c == 'x' {10} else {100}, gap: c == 'g' })
    }
    fn assess(&mut self, _: &Evidence) -> Result<Judgment, String> {
        let c = self.judgments[self.assesses]; self.assesses += 1;
        Ok(match c { 's' => Judgment::Satisfied, 'w' => Judgment::WorkNeeded, 'u' => Judgment::Unknown, _ => panic!("bad fixture judgment") })
    }
    fn act(&mut self, _: &Evidence, attempt: &str) -> Result<(), String> {
        let saved = self.saves.last().expect("pending save required");
        assert_eq!(saved.pending.as_deref(), Some(attempt)); assert!(saved.attempts > 0); self.acts += 1;
        if self.outcome == "fail" { Err("uncertain".into()) } else { Ok(()) }
    }
    fn save(&mut self, cp: &Checkpoint) -> Result<(), String> {
        if self.saves.len() + 1 == self.fail_save { return Err("save failed".into()); }
        self.saves.push(cp.clone()); Ok(())
    }
    fn clock(&mut self) -> u64 {10}
    fn new_id(&mut self) -> String {"attempt-1".into()}
}
fn initial(kind: &str) -> Checkpoint {
    let mut cp = Checkpoint::default();
    if ["cached", "expired", "rebound"].contains(&kind) {
        cp.binding = if kind == "rebound" {"v0"} else {"v1"}.into(); cp.evidence = "a".into(); cp.disposition = Judgment::Satisfied; cp.valid_until = if kind == "expired" {9} else {100};
    }
    if ["pending", "exhausted"].contains(&kind) {cp.attempts = 1; cp.binding = "v1".into();}
    if kind == "pending" {cp.pending = Some("prior".into());}
    cp
}
#[test]
fn shared_lifecycle_corpus() {
    let mut count = 0;
    for line in include_str!("../fixtures/lifecycle.tsv").lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
        let f: Vec<_> = line.split('\t').collect(); assert_eq!(f.len(), 14);
        let cp = initial(f[1]); let original = cp.clone(); let mut host = Fake::new(f[2], f[3], f[4]);
        let (out, status) = reconcile("v1", &cp, &mut host, f[5].parse().unwrap()).unwrap();
        assert_eq!(status, f[6], "{}", f[0]); assert_eq!(out.disposition.as_str(), f[7], "{}", f[0]);
        assert_eq!(out.attempts, f[8].parse::<u64>().unwrap(), "{}", f[0]);
        assert_eq!(out.pending.as_deref().unwrap_or("-"), f[9], "{}", f[0]);
        assert_eq!([host.observes, host.assesses, host.acts, host.saves.len()], [f[10], f[11], f[12], f[13]].map(|n| n.parse::<usize>().unwrap()), "{}", f[0]);
        assert_eq!(cp, original); count += 1;
    }
    assert_eq!(count, 25);
}
#[test]
fn save_failure_never_launches_an_unrecorded_effect() {
    for at in 1..=4 {
        let mut host = Fake::new("aabb", "ws", "ok"); host.fail_save = at;
        assert_eq!(reconcile("v1", &Checkpoint::default(), &mut host, 1).unwrap_err(), "save failed");
        assert_eq!(host.acts, if at <= 2 {0} else {1});
    }
}
#[test]
fn settlement_is_explicit_and_does_not_replenish_budget() {
    let pending = initial("pending");
    for outcome in ["completed", "not-applied"] {
        let cp = settle_pending(&pending, "v1", "prior", outcome, "receipt-1").unwrap();
        assert_eq!(cp.pending, None); assert_eq!(cp.attempts, 1); assert_eq!(cp.disposition, Judgment::Unknown); assert_eq!(cp.valid_until, 0); assert_eq!(cp.settlement.as_ref().unwrap().receipt, "receipt-1");
        assert!(settle_pending(&cp, "v1", "prior", outcome, "receipt-1").is_err());
    }
    for (binding, attempt, outcome, receipt) in [("v0", "prior", "completed", "receipt"), ("v1", "other", "completed", "receipt"), ("v1", "prior", "unknown", "receipt"), ("v1", "prior", "completed", " ")] {
        assert!(settle_pending(&pending, binding, attempt, outcome, receipt).is_err());
    }
    assert_eq!(pending.pending.as_deref(), Some("prior"));
}
#[test]
fn ten_thousand_sequential_world_transitions() {
    struct World { state: String, now: u64, actions: u64, assessments: u64, saved: Option<Checkpoint> }
    impl Host for World {
        fn observe(&mut self) -> Result<Evidence, String> { Ok(Evidence { identity: self.state.clone(), payload: self.state.clone(), observed_at: self.now, valid_until: self.now + 2, gap: false }) }
        fn assess(&mut self, e: &Evidence) -> Result<Judgment, String> {
            self.assessments += 1;
            Ok(match e.payload.as_str() { "good" => Judgment::Satisfied, "bad" => Judgment::WorkNeeded, _ => Judgment::Unknown })
        }
        fn act(&mut self, e: &Evidence, attempt: &str) -> Result<(), String> {
            assert_eq!(self.saved.as_ref().unwrap().pending.as_deref(), Some(attempt)); assert_eq!(e.payload, "bad");
            self.actions += 1; self.state = "good".into(); Ok(())
        }
        fn save(&mut self, cp: &Checkpoint) -> Result<(), String> { self.saved = Some(cp.clone()); Ok(()) }
        fn clock(&mut self) -> u64 { self.now }
        fn new_id(&mut self) -> String { format!("stress-{}", self.actions + 1) }
    }
    let mut host = World { state: "good".into(), now: 10, actions: 0, assessments: 0, saved: None };
    let mut cp = Checkpoint::default();
    for i in 0..10000 {
        let mode = i % 7;
        host.state = match mode { 0 | 4 => "bad", 2 | 5 => "unknown", _ => "good" }.into();
        if mode == 6 {host.now += 3;}
        let binding = if i % 11 == 0 {"v2"} else {"v1"};
        let before = cp.clone(); let input = host.state.clone(); let actions = host.actions; let assessments = host.assessments;
        let can_reuse = before.binding == binding && before.evidence == input && before.disposition == Judgment::Satisfied && host.now < before.valid_until;
        let (next, status) = reconcile(binding, &cp, &mut host, 2000).unwrap(); cp = next;
        let expected_actions = u64::from(input == "bad" && before.attempts < 2000);
        assert_eq!(host.actions - actions, expected_actions); assert_eq!(cp.attempts, host.actions); assert_eq!(cp.pending, None);
        assert_eq!(status == "reused", can_reuse);
        assert_eq!(host.assessments - assessments, if can_reuse {0} else if expected_actions == 1 {2} else {1});
        let expected = if input == "unknown" {Judgment::Unknown} else if input == "bad" && expected_actions == 0 {Judgment::WorkNeeded} else {Judgment::Satisfied};
        assert_eq!(cp.disposition, expected);
        assert_eq!(status, if can_reuse {"reused"} else if input == "unknown" {"unknown"} else if input == "bad" && expected_actions == 0 {"attempt-limit"} else {"satisfied"});
    }
    assert_eq!(host.actions, 2000);
}
