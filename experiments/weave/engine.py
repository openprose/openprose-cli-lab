"""Experimental capability-injected reconcile; never parses contracts."""
from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class Checkpoint:
    binding: str = ''
    evidence: str = ''
    disposition: str = 'unknown'
    valid_until: float = 0
    pending: Optional[str] = None
    attempts: int = 0


def reconcile(binding, checkpoint, observe, assess, act, save, clock, new_id, max_attempts=1):
    """Host serializes calls and supplies durable save. At most one action per call."""
    cp=Checkpoint(**asdict(checkpoint))
    if cp.pending:
        return cp, 'recovery-needed'
    first=observe()
    now=clock()
    if first.gap or not first.observed_at <= now < first.valid_until:
        cp.disposition='unknown';save(cp);return cp,'evidence-gap'
    if (cp.binding==binding and cp.evidence==first.identity and
            cp.disposition=='satisfied' and now<cp.valid_until):
        return cp,'reused'
    def judge(evidence):
        result=assess(evidence)
        if result not in ('satisfied','work-needed','unknown'):
            raise ValueError('invalid assessment')
        fresh=observe()
        if (fresh.gap or fresh.identity!=evidence.identity or
                not evidence.observed_at<=clock()<evidence.valid_until or
                not fresh.observed_at<=clock()<fresh.valid_until):
            return 'unknown',fresh,True
        return result,fresh,False
    result, fresh, stale=judge(first)
    cp.binding=binding;cp.evidence=fresh.identity
    cp.disposition=result;cp.valid_until=min(first.valid_until,fresh.valid_until)
    save(cp)
    if stale:return cp,'stale-assessment'
    if result!='work-needed':return cp,result
    if cp.attempts>=max_attempts:return cp,'attempt-limit'
    cp.pending=new_id();cp.attempts+=1;save(cp)
    try:act(first,cp.pending)
    except Exception:
        # The caller must reconcile uncertain external effects before replay.
        return cp,'action-outcome-unknown'
    cp.pending=None;cp.disposition='unknown';save(cp)
    after=observe()
    if after.gap or not after.observed_at<=clock()<after.valid_until:
        return cp,'evidence-gap'
    result,fresh,stale=judge(after)
    cp.evidence=fresh.identity;cp.disposition=result
    cp.valid_until=min(after.valid_until,fresh.valid_until);save(cp)
    return cp,'stale-assessment' if stale else result
