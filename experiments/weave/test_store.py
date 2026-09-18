import json
import tempfile
import unittest
from pathlib import Path
from evidence import pack
from store import FileHost

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
  self.p=Path(self.t.name)/'checkpoint.json'
 def test_uncertain_effect_survives_host_restart_without_replay(self):
  effects=[]
  def actor(e,identity):effects.append(identity);raise RuntimeError('connection lost after effect')
  args=('v1',lambda:pack('s','v1','bad',0,60),lambda e:'work-needed',actor,lambda:1,lambda:'one')
  _,status=FileHost(self.p).step(*args)
  self.assertEqual(status,'action-outcome-unknown')
  _,status=FileHost(self.p).step(*args)
  self.assertEqual(status,'recovery-needed');self.assertEqual(effects,['one'])
 def test_competing_host_cannot_enter(self):
  with FileHost(self.p).locked():
   with self.assertRaises(BlockingIOError):
    with FileHost(self.p).locked():pass
 def test_corrupt_checkpoint_fails_before_action(self):
  self.p.write_text('bad')
  with self.assertRaises(json.JSONDecodeError):FileHost(self.p).read()
if __name__=='__main__':unittest.main()
