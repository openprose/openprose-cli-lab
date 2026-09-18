import unittest
from dataclasses import asdict
from evidence import pack
from engine import Checkpoint,reconcile

class LifecycleTests(unittest.TestCase):
    def run_case(self, state='bad', fix=True, crash=False, cp=None, binding='v1', mutate=False, gap=False):
        world=[state];self.actions=[];self.saved=[];self.assessments=[]
        def observe():return pack('source','v1',world[0],0,60,'missing' if gap else None)
        def assess(e):
            self.assessments.append(e.identity)
            result='satisfied' if e.payload=='"good"' else 'work-needed'
            if mutate:world[0]='changed'
            return result
        def act(e,identity):
            self.actions.append(identity)
            if crash:raise RuntimeError('uncertain effect')
            if fix:world[0]='good'
        result=reconcile(binding,cp or Checkpoint(),observe,assess,act,
                         lambda c:self.saved.append(asdict(c)),lambda:1,lambda:'attempt-1')
        return result
    def test_satisfied_rests(self):
        cp,status=self.run_case('good');self.assertEqual(status,'satisfied');self.assertEqual(self.actions,[])
    def test_cached_satisfaction_skips_assessment(self):
        cp,_=self.run_case('good');_,status=self.run_case('good',cp=cp)
        self.assertEqual(status,'reused');self.assertEqual(self.assessments,[])
    def test_changed_input_invalidates(self):
        cp,_=self.run_case('good');_,status=self.run_case('bad',cp=cp)
        self.assertEqual(status,'satisfied');self.assertEqual(len(self.actions),1)
    def test_repair_reassessed_and_pending_saved(self):
        cp,status=self.run_case();self.assertEqual(status,'satisfied')
        self.assertEqual(len(self.assessments),2);self.assertIsNone(cp.pending)
        self.assertTrue(any(c['pending']=='attempt-1' for c in self.saved))
    def test_failed_repair_does_not_loop(self):
        cp,status=self.run_case(fix=False);self.assertEqual(status,'work-needed');self.assertEqual(len(self.actions),1)
        _,status=self.run_case(fix=False,cp=cp);self.assertEqual(status,'attempt-limit');self.assertEqual(self.actions,[])
    def test_changed_binding_invalidates(self):
        cp,_=self.run_case('good');_,status=self.run_case('good',cp=cp,binding='v2')
        self.assertEqual(status,'satisfied');self.assertEqual(len(self.assessments),1)
    def test_assessment_race_is_rejected(self):
        _,status=self.run_case('good',mutate=True);self.assertEqual(status,'stale-assessment');self.assertEqual(self.actions,[])
    def test_pending_attempt_never_replayed(self):
        _,status=self.run_case(cp=Checkpoint(pending='prior'));self.assertEqual(status,'recovery-needed');self.assertEqual(self.actions,[])
    def test_exception_preserves_pending(self):
        cp,status=self.run_case(crash=True);self.assertEqual(status,'action-outcome-unknown');self.assertEqual(cp.pending,'attempt-1')
    def test_missing_evidence_blocks_action(self):
        _,status=self.run_case(gap=True);self.assertEqual(status,'evidence-gap');self.assertEqual(self.actions,[])

if __name__=='__main__':unittest.main()
