"""Run with python3 experiments/weave/demo.py. No credentials or network needed."""
import json
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from evidence import query_evidence
from store import FileHost


def demo():
    with tempfile.TemporaryDirectory(prefix='prose-weave-demo-') as directory:
        root=Path(directory);database=root/'state.sqlite';checkpoint=root/'checkpoint.json'
        def execute(sql):
            with sqlite3.connect(database) as db:db.execute(sql)
        execute('CREATE TABLE brief (desired TEXT, actual TEXT, approved INTEGER, note TEXT)')
        execute("INSERT INTO brief VALUES ('Monday','Monday',1,'draft')")
        counts={'assessments':0,'actions':0}
        def observe():
            return query_evidence(database,'SELECT desired,actual,approved FROM brief',(),time.time(),named_rows=True)
        def assess(evidence):
            counts['assessments']+=1
            rows=json.loads(evidence.payload)['rows']
            if len(rows)!=1 or rows[0]['approved']!=1:return 'unknown'
            return 'satisfied' if rows[0]['desired']==rows[0]['actual'] else 'work-needed'
        def act(evidence,attempt):
            counts['actions']+=1
            execute('UPDATE brief SET actual=desired')
        events=[('initial',None),('irrelevant note',"UPDATE brief SET note='reviewed'"),
                ('date changed',"UPDATE brief SET desired='Tuesday'"),('duplicate',None),
                ('approval revoked','UPDATE brief SET approved=0')]
        results=[]
        for label,mutation in events:
            if mutation:execute(mutation)
            # Reconstruct the host each event to demonstrate persisted reuse.
            cp,status=FileHost(checkpoint).step('brief-contract-and-selector-v1',observe,assess,act,
                                              time.time,lambda:str(uuid.uuid4()),max_attempts=2)
            result={'event':label,'status':status,**counts,'attempts':cp.attempts}
            results.append(result);print(json.dumps(result))
        return results

if __name__=='__main__':demo()
