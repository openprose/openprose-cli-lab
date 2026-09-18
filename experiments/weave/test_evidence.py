import sqlite3
import tempfile
import unittest
from pathlib import Path
from evidence import file_evidence, query_evidence, can_reuse

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root/'state.md'
        self.file.write_text('Release: September 20')
        self.db = self.root/'state.sqlite'
        self.write('CREATE TABLE releases(id INTEGER, date TEXT, note TEXT)')
        self.write("INSERT INTO releases VALUES (1, 'September 20', 'draft')")
    def write(self, sql):
        with sqlite3.connect(self.db) as db: db.execute(sql)
    def query(self, sql='SELECT id,date FROM releases', **kw):
        return query_evidence(self.db, sql, (), now=10, **kw)
    def test_file_change_invalidates(self):
        old=file_evidence(self.file,0)
        self.file.write_text('Release: September 21')
        self.assertFalse(can_reuse(old,file_evidence(self.file,1),'v1','v1',1))
    def test_same_file_reuses(self):
        self.assertTrue(can_reuse(file_evidence(self.file,0),file_evidence(self.file,1),'v1','v1',1))
    def test_missing_file_is_gap(self):
        self.file.unlink(); self.assertIsNotNone(file_evidence(self.file,1).gap)
    def test_expiry_invalidates_unchanged_content(self):
        self.assertFalse(can_reuse(file_evidence(self.file,0),file_evidence(self.file,61),'v1','v1',61))
    def test_contract_revision_invalidates(self):
        e=file_evidence(self.file,0); self.assertFalse(can_reuse(e,e,'v1','v2',1))
    def test_query_ignores_unselected_column(self):
        old=self.query(); self.write("UPDATE releases SET note='approved'")
        self.assertEqual(old.identity,self.query().identity)
    def test_query_detects_selected_change(self):
        old=self.query(); self.write("UPDATE releases SET date='September 21'")
        self.assertNotEqual(old.identity,self.query().identity)
    def test_query_order_is_explicitly_unordered(self):
        self.write("INSERT INTO releases VALUES (2,'September 22','draft')")
        old=self.query(); self.write('CREATE INDEX reverse ON releases(id DESC)')
        self.assertEqual(old.identity,self.query().identity)
    def test_query_preserves_duplicate_rows(self):
        old=self.query(); self.write("INSERT INTO releases VALUES (1,'September 20','draft')")
        self.assertNotEqual(old.identity,self.query().identity)
    def test_query_change_invalidates_even_same_result(self):
        self.assertNotEqual(self.query().identity,self.query('SELECT id,date FROM releases WHERE id=1').identity)
    def test_query_rejects_writes(self):
        before=self.query(); self.assertIsNotNone(self.query('DELETE FROM releases').gap)
        self.assertEqual(before.identity,self.query().identity)
    def test_missing_db_is_not_created(self):
        p=self.root/'missing.sqlite'
        self.assertIsNotNone(query_evidence(p,'SELECT 1',(),0).gap)
        self.assertFalse(p.exists())
    def test_size_limits_are_gaps_not_truncation(self):
        self.assertIsNotNone(file_evidence(self.file,0,limit=2).gap)
        self.assertIsNotNone(self.query(row_limit=0).gap)
    def test_sql_error_is_gap(self):
        self.assertIsNotNone(self.query('SELECT absent FROM releases').gap)

if __name__=='__main__': unittest.main()
