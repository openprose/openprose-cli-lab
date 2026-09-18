"""Local serialized checkpoint host; explicit opt-in, no scheduling or model API."""
import contextlib
import fcntl
import json
import os
from dataclasses import asdict
from pathlib import Path
from engine import Checkpoint,reconcile

class FileHost:
    def __init__(self,path):self.path=Path(path)
    @contextlib.contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
    def read(self):
        return Checkpoint(**json.loads(self.path.read_text())) if self.path.exists() else Checkpoint()
    def save(self,checkpoint):
        target=self.path.with_suffix('.tmp')
        with target.open('w') as f:
            json.dump(asdict(checkpoint),f,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(target,self.path)
        directory=os.open(self.path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    def step(self,binding,observe,assess,act,clock,new_id,max_attempts=1):
        with self.locked():
            return reconcile(binding,self.read(),observe,assess,act,self.save,clock,new_id,max_attempts)
