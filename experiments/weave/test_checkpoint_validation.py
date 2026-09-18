import json,tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from engine import Checkpoint,reconcile
from evidence import pack
from store import FileHost
class CheckpointValidationTests(unittest.TestCase):
 def test_malformed_disk_values_fail_before_capabilities(self):
  bad=[('attempts',-1),('attempts',True),('attempts',1.5),('valid_until',float('nan')),('valid_until',float('inf')),('pending',''),('pending',False),('binding',{}),('disposition','complete'),('settlement',{'outcome':'completed'})]
  with tempfile.TemporaryDirectory() as d:
   host=FileHost(Path(d)/'cp.json')
   for field,value in bad:
    with self.subTest(field=field,value=value):
     data=asdict(Checkpoint());data[field]=value;host.path.write_text(json.dumps(data));calls=[]
     with self.assertRaises(ValueError):host.step('v1',lambda:calls.append('observe'),None,None,None,None)
     self.assertEqual(calls,[])
 def test_invalid_limit_fails_before_observe(self):
  for limit in (-1,True,float('nan'),1.5):
   calls=[]
   with self.assertRaises(ValueError):reconcile('v1',Checkpoint(),lambda:calls.append(1),None,None,None,None,None,max_attempts=limit)
   self.assertEqual(calls,[])
 def test_invalid_attempt_id_never_acts(self):
  for identity in ('',None,False):
   calls=[]
   with self.assertRaises(ValueError):reconcile('v1',Checkpoint(),lambda:pack('s','v','bad',0,60),lambda e:'work-needed',lambda *a:calls.append(1),lambda c:None,lambda:1,lambda:identity)
   self.assertEqual(calls,[])
 def test_legacy_checkpoint_without_settlement_loads(self):
  with tempfile.TemporaryDirectory() as d:
   host=FileHost(Path(d)/'cp.json');data=asdict(Checkpoint());data.pop('settlement');host.path.write_text(json.dumps(data))
   self.assertIsNone(host.read().settlement)
if __name__=='__main__':unittest.main()
