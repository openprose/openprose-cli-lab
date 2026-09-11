#!/usr/bin/env python3
"""Extract bounded model-id metadata without emitting transcript content."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


MODEL_ID = re.compile(
    rb"(?<![A-Za-z0-9_.~-])"
    rb"(?:prime-inference/)?"
    rb"(?:openrouter/)?"
    rb"(?:openai|anthropic|deepseek|qwen)/"
    rb"[A-Za-z0-9][A-Za-z0-9._~:/-]{1,100}"
)


def extract(path: Path, maximum_bytes: int, maximum_ids: int) -> dict[str, Any]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        scanned = stream.read(maximum_bytes)
    ids = sorted(
        {match.group(0).decode("ascii") for match in MODEL_ID.finditer(scanned)}
    )[:maximum_ids]
    return {
        "sourceBasename": path.name,
        "sourceBytes": size,
        "bytesScanned": len(scanned),
        "scanTruncated": len(scanned) < size,
        "scannedPrefixSha256": hashlib.sha256(scanned).hexdigest(),
        "modelIds": ids,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--maximum-bytes-per-file", type=int, default=4_000_000)
    parser.add_argument("--maximum-ids-per-file", type=int, default=200)
    args = parser.parse_args(argv)
    if len(args.paths) > 8:
        parser.error("at most 8 targeted transcript files may be scanned")
    if not 1 <= args.maximum_bytes_per_file <= 4_000_000:
        parser.error("maximum bytes per file must be in [1, 4000000]")
    if not 1 <= args.maximum_ids_per_file <= 200:
        parser.error("maximum ids per file must be in [1, 200]")
    result = {
        "schema": "openprose.prior-model-metadata/1",
        "transcriptContentEmitted": False,
        "sources": [
            extract(path, args.maximum_bytes_per_file, args.maximum_ids_per_file)
            for path in args.paths
        ],
    }
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
