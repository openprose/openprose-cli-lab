"""Experimental capability-injected reconcile; never parses contracts."""
from dataclasses import dataclass, asdict
from typing import Optional
import math

@dataclass
class Checkpoint:
    binding: str = ''
    evidence: str = ''
    disposition: str = 'unknown'
    valid_until: float = 0
    pending: Optional[str] = None
    attempts: int = 0
    settlement: Optional[dict] = None

    def __post_init__(self):
        if not isinstance(self.binding,str) or not isinstance(self.evidence,str):
            raise ValueError('invalid checkpoint identity')
        if self.disposition not in ('unknown','satisfied','work-needed'):
            raise ValueError('invalid checkpoint disposition')
        if type(self.attempts) is not int or self.attempts < 0:
            raise ValueError('invalid checkpoint attempts')
        if type(self.valid_until) not in (int,float) or not math.isfinite(self.valid_until):
            raise ValueError('invalid checkpoint expiry')
        if self.pending is not None and (not isinstance(self.pending,str) or not self.pending.strip()):
            raise ValueError('invalid pending attempt')
        if self.settlement is not None:
            r=self.settlement
            if (not isinstance(r,dict) or set(r)!= {'attempt','binding','outcome','receipt'}
                or any(not isinstance(r[k],str) or not r[k].strip() for k in r)
                or r['outcome'] not in ('completed','not-applied')):
                raise ValueError('invalid settlement record')


def reconcile(binding, checkpoint, observe, assess, act, save, clock, new_id, max_attempts=1):
    """Host serializes calls and supplies durable save. At most one action per call."""
    if type(max_attempts) is not int or max_attempts < 0:
        raise ValueError('invalid attempt limit')
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
    attempt=new_id()
    if not isinstance(attempt,str) or not attempt.strip():
        raise ValueError('invalid new attempt identity')
    cp.pending=attempt;cp.attempts+=1;save(cp)
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


def settle_pending(checkpoint, binding, attempt, outcome, receipt):
    """Trusted host settlement only; receipt authenticity is caller responsibility.

    Does not assert contract satisfaction, replenish attempts, or perform work.
    Caller serializes and atomically saves the returned checkpoint.
    """
    if not attempt or checkpoint.pending != attempt:
        raise ValueError('pending attempt mismatch')
    if checkpoint.binding != binding:
        raise ValueError('binding mismatch')
    if outcome not in ('completed', 'not-applied'):
        raise ValueError('unresolved action outcome')
    if not isinstance(receipt, str) or not receipt.strip():
        raise ValueError('receipt reference required')
    cp=Checkpoint(**asdict(checkpoint))
    cp.settlement={'attempt':attempt,'binding':binding,'outcome':outcome,'receipt':receipt}
    cp.pending=None;cp.disposition='unknown';cp.valid_until=0
    return cp
