#!/usr/bin/env python3
"""Provider-free Prime stand-in for the direct-skill evidence runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_fake_run(cwd: Path, program: bytes) -> int:
    run = cwd / ".prose/runs/fake-run"
    bindings = run / "bindings"
    bindings.mkdir(parents=True)
    (run / "program.prose").write_bytes(program)
    (run / "state.md").write_text("# Execution State\n\nstatus: complete\n", "utf-8")
    text = program.decode("utf-8")
    values = {
        "result": "DIRECT_SKILL_SIMPLE_OK",
        "left": "DIRECT_SKILL_LEFT_OK",
        "right": "DIRECT_SKILL_RIGHT_OK",
        "combined": "DIRECT_SKILL_FANOUT_OK",
    }
    count = 0
    for name, token in values.items():
        if token in text:
            (bindings / f"{name}.md").write_text(
                f"# {name}\n\nkind: let\n\n---\n\n{token}\n", "utf-8"
            )
            count += 1
    return count


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print(os.environ.get("FAKE_PRIME_VERSION", "0.7.0"))
        return 0
    cwd = Path.cwd()
    argv = sys.argv[1:]
    mode = os.environ.get("FAKE_DIRECT_SKILL_MODE", "pass")
    observation = os.environ.get("OPENPROSE_DIRECT_SKILL_TEST_OBSERVATION")
    env_bytes = (cwd / ".prose/.env").read_bytes()
    program = (cwd / "program.prose").read_bytes()
    if observation:
        Path(observation).write_text(
            json.dumps(
                {
                    "argv": argv[:-1] + ["[REDACTED_USER_MESSAGE]"],
                    "cwd": str(cwd),
                    "environmentNames": sorted(
                        name
                        for name in os.environ
                        if name.startswith("OPENPROSE_")
                        or name.endswith("_API_KEY")
                        or name.startswith("FAKE_")
                    ),
                    "telemetryEnvSha256": sha256(env_bytes),
                    "programSha256": sha256(program),
                },
                sort_keys=True,
            )
            + "\n",
            "utf-8",
        )
    if mode == "timeout":
        time.sleep(30)
    if mode == "large-output":
        sys.stdout.write("x" * 100_000)
        return 0
    if mode == "fail":
        print("provider failed api_key=fixture-super-secret", file=sys.stderr)
        return 7

    starts = write_fake_run(cwd, program)
    provider = argv[argv.index("--provider") + 1]
    model = argv[argv.index("--model") + 1]
    code = "\n".join("await rlm.run('redacted')" for _ in range(starts))
    if mode == "prose-invocation":
        code += "\nimport subprocess; subprocess.run(['prose','run','program.prose'])"
    if mode == "telemetry":
        code += "\nrequests.post('https://api-v2.prose.md/analytics')"
    print(
        json.dumps(
            {
                "type": "tool_execution_start",
                "toolName": "ipython",
                "args": {"code": code},
            },
            sort_keys=True,
        )
    )
    if mode != "text-only-subagent":
        for index in range(starts):
            print(
                json.dumps(
                    {
                        "type": "subagent_execution_start",
                        "subagentId": f"fake-{index + 1}",
                    },
                    sort_keys=True,
                )
            )
    print(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "api": "fake-api",
                    "provider": provider,
                    "model": model,
                    "content": [{"type": "text", "text": "completed"}],
                    "stopReason": "stop",
                    "usage": {
                        "input": 100,
                        "output": 20,
                        "totalTokens": 120,
                        "cost": {"total": 0.001},
                    },
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
