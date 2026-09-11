#!/usr/bin/env python3
"""Verify the fixed functional-alpha glibc floor for exact Linux ELF candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

import package_local


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--rust-binary", type=Path, required=True)
    result.add_argument("--bun-binary", type=Path, required=True)
    result.add_argument("--readelf", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="openprose-readelf-") as temporary:
            snapshot, length, digest = package_local.snapshot_executable_tool(
                args.readelf, Path(temporary) / "readelf", "readelf"
            )
            runtime = package_local.linux_runtime_record(
                args.rust_binary, args.bun_binary, snapshot, length, digest
            )
    except (package_local.PackageError, OSError) as error:
        print(f"linux-glibc: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema": "openprose.linux-glibc-admission/1",
                "status": "pass",
                **runtime,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
