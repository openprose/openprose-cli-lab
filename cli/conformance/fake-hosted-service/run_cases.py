#!/usr/bin/env python3
"""Run the immutable hosted manifests against the independent Python oracle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from case_oracle import execute_case


HERE = Path(__file__).resolve().parent
CASES = HERE.parent / "cases" / "hosted"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="openprose-hosted-case-oracle")
    result.add_argument("--case", action="append", default=[])
    return result


def main() -> int:
    selected = set(parser().parse_args().case)
    paths = [
        path
        for path in sorted(CASES.glob("*.json"))
        if path.name not in {"hosted-case-manifest.schema.json", "case-index.v1.json"}
    ]
    results = []
    for path in paths:
        manifest = json.loads(path.read_text("utf-8"))
        if selected and manifest["id"] not in selected:
            continue
        results.append(execute_case(path))
    if selected and {result["id"] for result in results} != selected:
        missing = sorted(selected - {result["id"] for result in results})
        raise SystemExit(f"unknown hosted case id(s): {', '.join(missing)}")
    print(json.dumps({
        "schema": "openprose.hosted-oracle-run/1",
        "cases": results,
        "count": len(results),
        "providerCalls": 0,
        "network": "not-used",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
