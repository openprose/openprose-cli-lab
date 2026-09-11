import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import argparse
import contextlib
import io
import json
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

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

class BudgetTest(unittest.IsolatedAsyncioTestCase):
    def args(self, directory):
        return argparse.Namespace(cwd=directory,model='fixture',instructions=None,env_file=None,
            prompt='opaque request',max_turns=40,timeout=300,tool_timeout=30,max_output_tokens=12000)

    async def test_forwarding_and_success_limits(self):
        result=SimpleNamespace(final_output='done',context_wrapper=SimpleNamespace(usage=SimpleNamespace(requests=1,input_tokens=1,output_tokens=1,total_tokens=2)))
        with tempfile.TemporaryDirectory() as directory, patch.object(harness.Runner,'run',AsyncMock(return_value=result)) as run:
            output=io.StringIO()
            with contextlib.redirect_stdout(output): code=await harness.run(self.args(directory))
            self.assertEqual(code,0)
            self.assertEqual(run.call_args.kwargs['max_turns'],40)
            records=[json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(records[0]['limits'],dict(maxTurns=40,timeoutSeconds=300,toolTimeoutSeconds=30,maxOutputTokens=12000))
            self.assertEqual(records[-1]['type'],'final')

    def test_invalid_cli_budgets_fail_before_run(self):
        for flag,value in [('--max-turns','0'),('--max-turns','-1'),('--max-turns','9007199254740992'),('--timeout','nan'),('--timeout','inf'),('--timeout','0')]:
            with patch('sys.argv',['run.py','--model','fixture','--cwd','/tmp','--prompt','opaque',flag,value]), patch.object(harness,'run') as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught: harness.main()
                self.assertEqual(caught.exception.code,2)
                run.assert_not_called()

    async def test_safe_error_limits_no_final(self):
        for name in ['MaxTurnsExceeded','TimeoutError','SecretExceptionName']:
            error=type(name,(Exception,),{})('must not appear')
            with tempfile.TemporaryDirectory() as directory, patch.object(harness.Runner,'run',AsyncMock(side_effect=error)):
                output=io.StringIO()
                with contextlib.redirect_stdout(output): code=await harness.run(self.args(directory))
                self.assertEqual(code,1)
                self.assertNotIn('must not appear',output.getvalue())
                records=[json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(records[-1]['error_type'],name if name in ('MaxTurnsExceeded','TimeoutError') else 'ExecutionError')
                self.assertFalse(any(r['type']=='final' for r in records))
                self.assertEqual(records[-1]['limits']['maxTurns'],40)

if __name__ == '__main__':
    unittest.main()
