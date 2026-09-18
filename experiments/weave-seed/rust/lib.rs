//! Provider-neutral synchronous experiment; no storage or language interpretation.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub enum Judgment { #[default] Unknown, Satisfied, WorkNeeded }
impl Judgment {
    pub fn as_str(&self) -> &'static str { match self { Self::Unknown => "unknown", Self::Satisfied => "satisfied", Self::WorkNeeded => "work-needed" } }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settlement { pub binding: String, pub attempt: String, pub outcome: String, pub receipt: String }
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct Checkpoint {
    pub binding: String, pub evidence: String, pub disposition: Judgment,
    pub valid_until: u64, pub pending: Option<String>, pub attempts: u64,
    pub settlement: Option<Settlement>,
}
#[derive(Clone, Debug)]
pub struct Evidence { pub identity: String, pub payload: String, pub observed_at: u64, pub valid_until: u64, pub gap: bool }
pub trait Host {
    fn observe(&mut self) -> Result<Evidence, String>;
    fn assess(&mut self, evidence: &Evidence) -> Result<Judgment, String>;
    fn act(&mut self, evidence: &Evidence, attempt: &str) -> Result<(), String>;
    fn save(&mut self, checkpoint: &Checkpoint) -> Result<(), String>;
    fn clock(&mut self) -> u64;
    fn new_id(&mut self) -> String;
}
const MAX_SAFE: u64 = 9_007_199_254_740_991;
fn nonblank(value: &str) -> bool { value.chars().any(|c| !c.is_whitespace() && c != '\u{feff}') }
fn validate(cp: &Checkpoint) -> Result<(), String> {
    if cp.attempts > MAX_SAFE || cp.valid_until > MAX_SAFE || cp.pending.as_ref().is_some_and(|p| !nonblank(p)) { return Err("invalid checkpoint".into()); }
    if let Some(r) = &cp.settlement {
        if [&r.binding, &r.attempt, &r.outcome, &r.receipt].iter().any(|v| !nonblank(v)) || !["completed", "not-applied"].contains(&r.outcome.as_str()) { return Err("invalid settlement record".into()); }
    }
    Ok(())
}
fn fresh(e: &Evidence, now: u64) -> Result<bool, String> {
    if e.observed_at > MAX_SAFE || e.valid_until > MAX_SAFE || now > MAX_SAFE { return Err("invalid observation or clock".into()); }
    Ok(!e.gap && e.observed_at <= now && now < e.valid_until)
}
fn judge(host: &mut impl Host, evidence: &Evidence) -> Result<(Judgment, Evidence, bool), String> {
    let result = host.assess(evidence)?;
    let next = host.observe()?;
    let stale = !fresh(evidence, host.clock())? || !fresh(&next, host.clock())? || next.identity != evidence.identity;
    Ok((if stale { Judgment::Unknown } else { result }, next, stale))
}
pub fn reconcile(binding: &str, checkpoint: &Checkpoint, host: &mut impl Host, max_attempts: u64) -> Result<(Checkpoint, &'static str), String> {
    validate(checkpoint)?;
    if !nonblank(binding) || max_attempts > MAX_SAFE { return Err("invalid binding or limit".into()); }
    let mut cp = checkpoint.clone();
    if cp.pending.is_some() { return Ok((cp, "recovery-needed")); }
    let first = host.observe()?;
    let now = host.clock();
    if !fresh(&first, now)? { cp.disposition = Judgment::Unknown; host.save(&cp)?; return Ok((cp, "evidence-gap")); }
    if cp.binding == binding && cp.evidence == first.identity && cp.disposition == Judgment::Satisfied && now < cp.valid_until { return Ok((cp, "reused")); }
    let (result, next, stale) = judge(host, &first)?;
    cp.binding = binding.into(); cp.evidence = next.identity; cp.disposition = result;
    cp.valid_until = first.valid_until.min(next.valid_until); host.save(&cp)?;
    if stale { return Ok((cp, "stale-assessment")); }
    if cp.disposition != Judgment::WorkNeeded { let status = cp.disposition.as_str(); return Ok((cp, status)); }
    if cp.attempts >= max_attempts { return Ok((cp, "attempt-limit")); }
    let attempt = host.new_id();
    if !nonblank(&attempt) { return Err("invalid attempt identity".into()); }
    cp.pending = Some(attempt.clone()); cp.attempts += 1; host.save(&cp)?;
    if host.act(&first, &attempt).is_err() { return Ok((cp, "action-outcome-unknown")); }
    cp.pending = None; cp.disposition = Judgment::Unknown; host.save(&cp)?;
    let after = host.observe()?;
    if !fresh(&after, host.clock())? { return Ok((cp, "evidence-gap")); }
    let (result, next, stale) = judge(host, &after)?;
    cp.evidence = next.identity; cp.disposition = result; cp.valid_until = after.valid_until.min(next.valid_until); host.save(&cp)?;
    let status = if stale { "stale-assessment" } else { cp.disposition.as_str() };
    Ok((cp, status))
}
pub fn settle_pending(checkpoint: &Checkpoint, binding: &str, attempt: &str, outcome: &str, receipt: &str) -> Result<Checkpoint, String> {
    validate(checkpoint)?;
    if attempt.is_empty() || checkpoint.pending.as_deref() != Some(attempt) || checkpoint.binding != binding || !["completed", "not-applied"].contains(&outcome) || !nonblank(receipt) { return Err("invalid settlement".into()); }
    let mut cp = checkpoint.clone();
    cp.pending = None; cp.disposition = Judgment::Unknown; cp.valid_until = 0;
    cp.settlement = Some(Settlement { binding: binding.into(), attempt: attempt.into(), outcome: outcome.into(), receipt: receipt.into() });
    Ok(cp)
}
#[cfg(test)]
mod tests;

#[cfg(test)]
mod identity_tests;
