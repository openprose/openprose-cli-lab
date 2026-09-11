#!/usr/bin/env python3
"""Generic local shell agent. No program-language interpretation is implemented here."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import time

from agents import Agent, Runner, RunConfig, ModelSettings, function_tool
from dotenv import dotenv_values


async def shell(command, cwd, timeout, env):
    process = await asyncio.create_subprocess_exec(
        '/bin/bash', '-c', command, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return {'exit_code': process.returncode,
            'stdout': stdout.decode(errors='replace')[:30000],
            'stderr': stderr.decode(errors='replace')[:30000]}


async def run(args):
    # Capture tool environment before loading provider credential; never give it to shell.
    tool_env = {k: v for k, v in os.environ.items()
                if not any(s in k.upper() for s in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))}
    if args.env_file:
        key = dotenv_values(args.env_file).get('OPENAI_API_KEY')
        if key:
            os.environ['OPENAI_API_KEY'] = key
    cwd = str(Path(args.cwd).resolve())
    instructions = 'You are a helpful coding agent. Use available tools to complete the user request.'
    if args.instructions:
        instructions += '\n\n' + Path(args.instructions).read_text()
    instructions += '\nYour working directory is: ' + cwd
    start = time.monotonic()
    def emit(kind, **data):
        print(json.dumps({'type': kind, 'event': kind, 'elapsed_seconds': round(time.monotonic()-start, 3), **data}), flush=True)

    @function_tool
    async def execute_shell(command: str) -> str:
        """Execute a bash command in the working directory. Read and edit files using ordinary shell tools."""
        emit('tool_call', name='execute_shell', command=command)
        try:
            result = await shell(command, cwd, args.tool_timeout, tool_env)
        except asyncio.TimeoutError:
            result = {'error': 'shell timeout; process group terminated'}
        emit('tool_result', name='execute_shell', result=result)
        return json.dumps(result)

    emit('start', model=args.model, cwd=cwd)
    agent = Agent(name='Local coding agent', instructions=instructions,
                  model=args.model, tools=[execute_shell],
                  model_settings=ModelSettings(max_tokens=args.max_output_tokens, timeout=args.timeout))
    try:
        result = await asyncio.wait_for(
            Runner.run(agent, args.prompt, max_turns=args.max_turns,
                       run_config=RunConfig(tracing_disabled=True)), args.timeout)
        usage = result.context_wrapper.usage
        emit('final', output=result.final_output, usage={
            'requests': usage.requests, 'input_tokens': usage.input_tokens,
            'output_tokens': usage.output_tokens, 'total_tokens': usage.total_tokens})
        return 0
    except Exception as error:
        # Exception bodies may contain request/credential information; type suffices for public log.
        emit('error', error_type=type(error).__name__)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='prose-agents-sdk 0.1.0')
    parser.add_argument('--model', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--instructions', help='Opaque text appended to generic instructions')
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--env-file', help='Optional dotenv containing OPENAI_API_KEY; shell never receives it')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--tool-timeout', type=float, default=30)
    parser.add_argument('--max-turns', type=int, default=20)
    parser.add_argument('--max-output-tokens', type=int, default=12000)
    raise SystemExit(asyncio.run(run(parser.parse_args())))

if __name__ == '__main__':
    main()
