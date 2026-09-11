#!/usr/bin/env python3
"""Provider-free prime-agent stand-in used only by real-harness unit tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print(os.environ.get("FAKE_PRIME_VERSION", "0.7.0"))
        return 0
    argv = sys.argv[1:]
    observation = os.environ.get("OPENPROSE_REAL_HARNESS_TEST_OBSERVATION")
    if observation:
        safe_environment = sorted(
            name
            for name in os.environ
            if name.startswith("OPENPROSE_") or name.startswith("FAKE_")
        )
        Path(observation).write_text(
            json.dumps(
                {
                    "argv": argv[:-1] + ["[REDACTED_PROMPT]"],
                    "cwd": os.getcwd(),
                    "environmentNames": safe_environment,
                },
                sort_keys=True,
            )
            + "\n",
            "utf-8",
        )
    mode = os.environ.get("FAKE_PRIME_MODE", "pass")
    if mode == "timeout":
        time.sleep(30)
    if mode == "fail":
        print("authentication failed for api_key=super-secret-value", file=sys.stderr)
        return 7
    prompt = argv[-1] if argv else ""
    match = re.search(r"<answer>([A-Z0-9_]+)</answer>", prompt)
    answer = match.group(1) if match else "MISSING"
    if mode == "wrong":
        answer = "WRONG"
    if mode == "internal-retry":
        for _ in range(4):
            print(
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [],
                            "stopReason": "error",
                            "errorMessage": "route unavailable",
                            "usage": {
                                "input": 0,
                                "output": 0,
                                "totalTokens": 0,
                                "cost": {"total": 0},
                            },
                        },
                    }
                ),
                flush=True,
            )
            time.sleep(0.2)
        return 0
    print(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                    "stopReason": "stop",
                    "usage": {
                        "input": 12,
                        "output": 4,
                        "totalTokens": 16,
                        "cost": {"input": 0.00008, "output": 0.00002, "total": 0.0001},
                    },
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
