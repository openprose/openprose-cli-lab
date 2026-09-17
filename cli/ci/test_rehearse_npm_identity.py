import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from rehearse_npm_identity import rehearse


class RehearsalTests(unittest.TestCase):
    def test_corrupt_archive_fails_before_subprocess_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            archive=source/'bun.tar.gz';archive.write_bytes(b'corrupt')
            manifest={'schema':'openprose.local-release-manifest/1','mode':'development','artifacts':[{'kind':'standalone-archive','implementation':'bun','path':'bun.tar.gz','byteLength':7,'sha256':'a'*64}]}
            (source/'release-manifest.json').write_text(json.dumps(manifest))
            with patch('rehearse_npm_identity.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError,'Source archive changed'):rehearse(source,root/'out')
                run.assert_not_called()
            self.assertFalse((root/'out').exists())

    def test_release_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'release-manifest.json').write_text(json.dumps({'schema':'openprose.local-release-manifest/1','mode':'release'}))
            with self.assertRaisesRegex(ValueError,'Only local development'):rehearse(root,root/'out')


if __name__=='__main__':unittest.main()
