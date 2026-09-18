import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from engine import Checkpoint,settle_pending
from evidence import file_evidence
from store import FileHost
class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.host=FileHost(self.root/'cp.json')
  self.host.save(Checkpoint(binding='v1',pending='attempt-1',attempts=1))
 def test_rejects_wrong_or_unknown_settlement(self):
  for args in [('v2','attempt-1','completed','r'),('v1','wrong','completed','r'),('v1','attempt-1','unknown','r'),('v1','attempt-1','completed','')]:
   with self.assertRaises(ValueError):self.host.settle(*args)
   self.assertEqual(self.host.read().pending,'attempt-1')
 def test_completed_repair_reassesses_without_replay(self):
  state=self.root/'state';state.write_text('good');actions=[];assessments=[]
  cp=self.host.settle('v1','attempt-1','completed','local-inspection:1')
  self.assertEqual(cp.attempts,1);self.assertEqual(cp.disposition,'unknown')
  def assess(e):assessments.append(e.identity);return 'satisfied'
  cp,status=FileHost(self.host.path).step('v1',lambda:file_evidence(state,0),assess,lambda *a:actions.append(1),lambda:1,lambda:'attempt-2')
  self.assertEqual(status,'satisfied');self.assertEqual(len(assessments),1);self.assertEqual(actions,[])
  self.assertEqual(cp.settlement['receipt'],'local-inspection:1')
 def test_not_applied_does_not_replenish_attempts(self):
  self.host.settle('v1','attempt-1','not-applied','tool-status:absent')
  state=self.root/'state';state.write_text('bad');actions=[]
  cp,status=self.host.step('v1',lambda:file_evidence(state,0),lambda e:'work-needed',lambda *a:actions.append(1),lambda:1,lambda:'attempt-2')
  self.assertEqual(status,'attempt-limit');self.assertEqual(actions,[])
 def test_duplicate_rejected(self):
  self.host.settle('v1','attempt-1','completed','r')
  with self.assertRaises(ValueError):self.host.settle('v1','attempt-1','completed','r')
 def test_save_failure_retains_pending(self):
  with patch.object(self.host,'save',side_effect=OSError('disk')):
   with self.assertRaises(OSError):self.host.settle('v1','attempt-1','completed','r')
  self.assertEqual(FileHost(self.host.path).read().pending,'attempt-1')
if __name__=='__main__':unittest.main()
