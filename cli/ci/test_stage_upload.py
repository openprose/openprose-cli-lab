import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import stage_upload as s


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.items = []
        for name in ('first.tgz', 'second.tgz'):
            data = name.encode()
            (self.root / name).write_bytes(data)
            self.items.append({'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.plan = {'version': '0.15.0-rc.1', 'source': 'a'*40, 'artifacts': self.items}
        self.one = {'first.tgz': {}}
        self.all = {a['name']: {} for a in self.items}

    def test_transient_failure_then_success(self):
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', side_effect=[self.one, self.one, self.all, self.all]), patch.object(s, 'command', side_effect=[subprocess.CompletedProcess([], 1, '', 'HTTP 500: Error saving asset'), subprocess.CompletedProcess([], 0, '', '')]) as command, patch.object(s.time, 'sleep') as sleep:
            s.stage(self.plan, self.root)
        self.assertEqual(command.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertTrue(all('--clobber' not in call.args[0] for call in command.call_args_list))

    def test_failure_after_completed_upload_is_not_retried(self):
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', side_effect=[self.one, self.all, self.all]), patch.object(s, 'command', return_value=subprocess.CompletedProcess([], 1, '', 'HTTP 502')) as command, patch.object(s.time, 'sleep') as sleep:
            s.stage(self.plan, self.root)
        command.assert_called_once()
        sleep.assert_not_called()

    def test_permanent_error_has_no_retry(self):
        for error in ('HTTP 401', 'HTTP 403', 'HTTP 422', 'connection reset'):
            with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', side_effect=[self.one, self.one]), patch.object(s, 'command', return_value=subprocess.CompletedProcess([], 1, '', error)) as command, patch.object(s.time, 'sleep') as sleep, self.assertRaisesRegex(ValueError, 'Permanent'):
                s.stage(self.plan, self.root)
            command.assert_called_once()
            sleep.assert_not_called()

    def test_transient_budget_is_three_attempts(self):
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', return_value=self.one), patch.object(s, 'command', return_value=subprocess.CompletedProcess([], 1, '', 'HTTP 429')) as command, patch.object(s.time, 'sleep') as sleep, self.assertRaisesRegex(ValueError, 'exhausted'):
            s.stage(self.plan, self.root)
        self.assertEqual(command.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 8])

    def test_complete_inventory_never_uploads(self):
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', return_value=self.all), patch.object(s, 'command') as command:
            s.stage(self.plan, self.root)
        command.assert_not_called()

    def test_timeout_reconciles_but_does_not_retry(self):
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', side_effect=[self.one, self.one]), patch.object(s, 'command', side_effect=subprocess.TimeoutExpired('gh', 180)) as command, self.assertRaisesRegex(ValueError, 'timed out'):
            s.stage(self.plan, self.root)
        command.assert_called_once()

    def test_existing_asset_mismatch_starter_unknown_and_duplicate_fail(self):
        good = {'name': self.items[0]['name'], 'size': self.items[0]['size'], 'digest': 'sha256:' + self.items[0]['sha256'], 'state': 'uploaded'}
        bad = [{**good, 'size': 0}, {**good, 'digest': None}, {**good, 'state': 'starter'}, {**good, 'name': 'unknown'}, good]
        for item in bad:
            assets = [item, good] if item == good else [item]
            responses = [{'sha': self.plan['source']}, {'isDraft': True, 'tagName': 'v'+self.plan['version'], 'databaseId': 123}, [assets]]
            with patch.object(s, 'checked_json', side_effect=responses), self.assertRaises(ValueError):
                s.snapshot(self.plan)

    def test_exact_paginated_snapshot_and_source_gate(self):
        assets = [{'name': a['name'], 'size': a['size'], 'digest': 'sha256:'+a['sha256'], 'state': 'uploaded'} for a in self.items]
        with patch.object(s, 'checked_json', side_effect=[{'sha': self.plan['source']}, {'isDraft': True, 'tagName': 'v'+self.plan['version'], 'databaseId': 123}, [[assets[0]], [assets[1]]]]) as command:
            self.assertEqual(set(s.snapshot(self.plan)), set(self.all))
            self.assertIn('--paginate', command.call_args.args[0])
        with patch.object(s, 'checked_json', return_value={'sha': 'b'*40}), self.assertRaisesRegex(ValueError, 'source'):
            s.snapshot(self.plan)

    def test_qualification_is_required(self):
        with patch.object(s.p, 'verify_local', side_effect=ValueError('unqualified')), patch.object(s, 'snapshot') as snapshot, self.assertRaisesRegex(ValueError, 'unqualified'):
            s.stage(self.plan, self.root)
        snapshot.assert_not_called()

    def test_fresh_empty_draft_uploads_every_nonempty_artifact(self):
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', side_effect=[{}, self.one, self.all, self.all]), patch.object(s, 'command', return_value=subprocess.CompletedProcess([], 0, '', '')) as command:
            s.stage(self.plan, self.root)
        self.assertEqual(command.call_count, 2)

    def test_virtual_empty_evidence_is_verified_but_never_uploaded(self):
        (self.root / 'empty.log').write_bytes(b'')
        self.items.append({'name': 'empty.log', 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest(), 'kind': 'evidence'})
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', return_value=self.all), patch.object(s, 'command') as command:
            s.stage(self.plan, self.root)
        command.assert_not_called()
        self.items[-1]['kind'] = 'npm'
        with patch.object(s.p, 'verify_local'), patch.object(s, 'snapshot', return_value=self.all), self.assertRaisesRegex(ValueError, 'Invalid empty evidence'):
            s.stage(self.plan, self.root)

    def test_snapshot_excludes_virtual_empty_evidence(self):
        self.items.append({'name': 'empty.log', 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest(), 'kind': 'evidence'})
        assets = [{'name': a['name'], 'size': a['size'], 'digest': 'sha256:'+a['sha256'], 'state': 'uploaded'} for a in self.items if a['size']]
        with patch.object(s, 'checked_json', side_effect=[{'sha': self.plan['source']}, {'isDraft': True, 'tagName': 'v'+self.plan['version'], 'databaseId': 123}, [assets]]):
            self.assertEqual(set(s.snapshot(self.plan)), set(self.all))


if __name__ == '__main__':
    unittest.main()
