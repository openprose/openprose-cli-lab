import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('harness', Path(__file__).with_name('run.py'))
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

class ShellTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_write_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'in.txt').write_text('a\nb\n')
            result = await harness.shell('cat in.txt > out.txt; cat out.txt; exit 7', directory, 2, {'PATH': os.environ['PATH']})
            self.assertEqual(result['exit_code'], 7)
            self.assertEqual(result['stdout'], 'a\nb\n')
            self.assertEqual(Path(directory, 'out.txt').read_text(), 'a\nb\n')

    async def test_timeout_stops_delayed_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(asyncio.TimeoutError):
                await harness.shell('(sleep 0.5; touch late) & wait', directory, 0.03, {'PATH': os.environ['PATH']})
            await asyncio.sleep(0.6)
            self.assertFalse(Path(directory, 'late').exists())

if __name__ == '__main__':
    unittest.main()
