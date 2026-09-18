use super::*;
struct IdentityHost { id: String, actions: usize }
impl Host for IdentityHost {
    fn observe(&mut self) -> Result<Evidence, String> { Ok(Evidence { identity:"e".into(), payload:"p".into(), observed_at:0, valid_until:100, gap:false }) }
    fn assess(&mut self, _: &Evidence) -> Result<Judgment, String> { Ok(Judgment::WorkNeeded) }
    fn act(&mut self, _: &Evidence, _: &str) -> Result<(), String> { self.actions += 1; Ok(()) }
    fn save(&mut self, _: &Checkpoint) -> Result<(), String> { Ok(()) }
    fn clock(&mut self) -> u64 { 10 }
    fn new_id(&mut self) -> String { self.id.clone() }
}
#[test]
fn shared_unicode_identity_boundaries() {
    // Closed fixture rows contain only ASCII names, space-separated scalar hex and booleans.
    // This keeps the core dependency-free; no general JSON parser is implemented here.
    let mut count = 0;
    for line in include_str!("../fixtures/core-identity.json").lines().map(str::trim).filter(|s| s.starts_with("[\"")) {
        let row:Vec<_> = line.trim_end_matches(',').strip_prefix('[').unwrap().strip_suffix(']').unwrap().split(',').map(str::trim).collect();
        assert_eq!(row.len(), 3);
        let name = row[0].trim_matches('"');
        let value:String = row[1].trim_matches('"').split_whitespace().map(|s| char::from_u32(u32::from_str_radix(s,16).unwrap()).unwrap()).collect();
        let accepted = match row[2] { "true" => true, "false" => false, _ => panic!("bad fixture boolean") };
        for field in ["binding", "newId", "pending", "receipt", "storedReceipt"] {
            let mut host = IdentityHost { id: if field == "newId" {value.clone()} else {"attempt".into()}, actions:0 };
            let mut cp = Checkpoint::default();
            if field == "pending" { cp.pending = Some(value.clone()); }
            if field == "storedReceipt" { cp.settlement = Some(Settlement {binding:"v1".into(),attempt:"prior".into(),outcome:"completed".into(),receipt:value.clone()}); }
            let result = if field == "receipt" {
                cp.binding = "v1".into(); cp.pending = Some("prior".into()); cp.attempts = 1;
                settle_pending(&cp,"v1","prior","completed",&value).map(|_| ())
            } else { reconcile(if field == "binding" {&value} else {"v1"}, &cp, &mut host, 1).map(|_| ()) };
            assert_eq!(result.is_ok(), accepted, "{name}/{field}: {result:?}");
            if !accepted { assert_eq!(host.actions, 0, "{name}/{field}"); }
        }
        count += 1;
    }
    assert_eq!(count, 6);
}
