#!/usr/bin/env python3
"""Deterministic, independently owned structured-process test fixture."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable


VERSION = "1.0.0"
EVENT_SCHEMA = "openprose.fake-harness-event/1"
SESSION_ID = "fake-session-0001"
ALLOWLISTED_ENVIRONMENT = (
    "OPENPROSE_INVOCATION_ID",
    "OPENPROSE_RECURSION_TOKEN",
    "OPENPROSE_RUN_NONCE",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _observation(args: argparse.Namespace, image: bytes, task: bytes) -> dict[str, Any]:
    return {
        "schema": "openprose.fake-harness-observation/1",
        "scenario": args.scenario,
        "argv": sys.argv.copy(),
        "cwd": os.getcwd(),
        "image": {
            "byteLength": len(image),
            "sha256": _sha256(image),
            "bytesBase64": base64.b64encode(image).decode("ascii"),
        },
        "task": {
            "byteLength": len(task),
            "sha256": _sha256(task),
            "bytesBase64": base64.b64encode(task).decode("ascii"),
        },
        "environment": {
            name: os.environ[name]
            for name in ALLOWLISTED_ENVIRONMENT
            if name in os.environ
        },
    }


def _event(event_type: str, **values: Any) -> dict[str, Any]:
    return {
        "schema": EVENT_SCHEMA,
        "type": event_type,
        "sessionId": SESSION_ID,
        **values,
    }


def _records() -> list[dict[str, Any]]:
    return [
        _event("session.started", harnessVersion=VERSION),
        _event("assistant.message", text="fake harness completed"),
        _event(
            "session.completed",
            terminalEnvelope={
                "schema": "openprose.sentinel-terminal-envelope/1",
                "semanticStatus": "not-applicable",
                "marker": "OPENPROSE_SENTINEL_TERMINAL_V1",
            },
        ),
    ]


def _encode_records(records: Iterable[dict[str, Any]], line_ending: bytes = b"\n") -> bytes:
    return b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + line_ending
        for record in records
    )


def _write_fragmented(payload: bytes) -> None:
    pattern = (1, 2, 5, 3, 8, 13)
    cursor = 0
    index = 0
    while cursor < len(payload):
        width = pattern[index % len(pattern)]
        os.write(sys.stdout.fileno(), payload[cursor : cursor + width])
        cursor += width
        index += 1


def _run_descendant(args: argparse.Namespace) -> int:
    assert args.descendant_pid_file is not None
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_descendant",
            "--pid-file",
            str(args.descendant_pid_file),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + 5.0
    while not args.descendant_pid_file.exists():
        if child.poll() is not None:
            raise RuntimeError("fixture descendant exited before publishing identities")
        if time.monotonic() >= deadline:
            raise RuntimeError("fixture descendant did not publish identities")
        time.sleep(0.01)
    print(json.dumps(_event("session.started", harnessVersion=VERSION), separators=(",", ":")), flush=True)
    print(
        json.dumps(
            _event("fixture.descendants", identities=json.loads(args.descendant_pid_file.read_text("utf-8"))),
            separators=(",", ":"),
        ),
        flush=True,
    )
    while True:
        time.sleep(60)


def run(args: argparse.Namespace) -> int:
    image = args.image_file.read_bytes()
    task = args.task_file.read_bytes()
    if args.observation_file is not None:
        _atomic_json(args.observation_file, _observation(args, image, task))

    records = _records()
    scenario = args.scenario
    if scenario == "success":
        os.write(sys.stdout.fileno(), _encode_records(records))
        return 0
    if scenario == "malformed":
        os.write(sys.stdout.fileno(), b'{"schema":"openprose.fake-harness-event/1",not-json}\n')
        return 0
    if scenario == "truncated":
        os.write(sys.stdout.fileno(), b'{"schema":"openprose.fake-harness-event/1","type":"session')
        return 0
    if scenario == "eof-without-terminal":
        os.write(sys.stdout.fileno(), _encode_records(records[:-1]))
        return 0
    if scenario == "nonzero":
        os.write(sys.stdout.fileno(), _encode_records(records[:1]))
        return 17
    if scenario == "terminal-nonzero":
        os.write(sys.stdout.fileno(), _encode_records(records))
        return 17
    if scenario == "delay":
        time.sleep(args.delay_ms / 1000.0)
        os.write(sys.stdout.fileno(), _encode_records(records))
        return 0
    if scenario == "stderr":
        print("fake harness diagnostic: stderr remains separate", file=sys.stderr, flush=True)
        os.write(sys.stdout.fileno(), _encode_records(records))
        return 0
    if scenario == "fragmented":
        _write_fragmented(_encode_records(records))
        return 0
    if scenario == "crlf":
        os.write(sys.stdout.fileno(), _encode_records(records, b"\r\n"))
        return 0
    if scenario == "duplicate-terminal":
        os.write(sys.stdout.fileno(), _encode_records(records + records[-1:]))
        return 0
    if scenario == "reordered":
        os.write(sys.stdout.fileno(), _encode_records([records[1], records[0], records[2]]))
        return 0
    if scenario == "descendant":
        return _run_descendant(args)
    raise AssertionError(f"unhandled scenario: {scenario}")


def _ignore_catchable_termination() -> None:
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, signal.SIG_IGN)


def descendant(pid_file: Path) -> int:
    _ignore_catchable_termination()
    leaf = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_leaf"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    _atomic_json(
        pid_file,
        {
            "childPid": os.getpid(),
            "grandchildPid": leaf.pid,
            "processGroupId": os.getpgrp() if hasattr(os, "getpgrp") else None,
            "attemptedDetachment": False,
            "runNonce": os.environ.get("OPENPROSE_RUN_NONCE"),
        },
    )
    while True:
        time.sleep(60)


def leaf() -> int:
    _ignore_catchable_termination()
    while True:
        time.sleep(60)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="openprose-fake-harness")
    result.add_argument("--version", action="version", version=f"openprose-fake-harness {VERSION}")
    subparsers = result.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--scenario",
        choices=(
            "success", "malformed", "truncated", "eof-without-terminal",
            "nonzero", "terminal-nonzero", "delay", "stderr", "fragmented",
            "crlf", "duplicate-terminal", "reordered", "descendant",
        ),
        required=True,
    )
    run_parser.add_argument("--image-file", type=Path, required=True)
    run_parser.add_argument("--task-file", type=Path, required=True)
    run_parser.add_argument("--observation-file", type=Path)
    run_parser.add_argument("--descendant-pid-file", type=Path)
    run_parser.add_argument("--delay-ms", type=int, default=25)

    descendant_parser = subparsers.add_parser("_descendant")
    descendant_parser.add_argument("--pid-file", type=Path, required=True)
    subparsers.add_parser("_leaf")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "run":
        if args.scenario == "descendant" and args.descendant_pid_file is None:
            parser().error("--descendant-pid-file is required for descendant scenario")
        return run(args)
    if args.command == "_descendant":
        return descendant(args.pid_file)
    if args.command == "_leaf":
        return leaf()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
