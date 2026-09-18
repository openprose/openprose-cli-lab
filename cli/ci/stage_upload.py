#!/usr/bin/env python3
"""Resume a qualified draft upload without overwriting release assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import publication as p

TRANSIENT = re.compile(r'\bHTTP\s+(429|500|502|503|504)\b', re.IGNORECASE)


def command(argv):
    timeout = 600 if argv[1:3] == ['release', 'upload'] else 60
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def checked_json(argv):
    result = command(argv)
    p.require(result.returncode == 0, 'GitHub metadata lookup failed; upload stopped')
    return json.loads(result.stdout)


def snapshot(plan):
    tag = 'v' + plan['version']
    ref = checked_json(['gh', 'api', 'repos/' + p.REPOSITORY + '/commits/' + tag])
    p.require(ref.get('sha') == plan['source'], 'Release tag does not match reviewed source')
    release = checked_json(['gh', 'release', 'view', tag, '--repo', p.REPOSITORY, '--json', 'databaseId,isDraft,tagName'])
    p.require(release.get('isDraft') is True and release.get('tagName') == tag, 'Expected the exact draft release')
    # The releases response may truncate assets; use the complete asset endpoint.
    release_id = release.get('databaseId')
    p.require(type(release_id) is int and release_id > 0, 'Invalid release ID')
    pages = checked_json(['gh', 'api', '--paginate', '--slurp', 'repos/' + p.REPOSITORY + '/releases/' + str(release_id) + '/assets?per_page=100'])
    p.require(isinstance(pages, list) and all(isinstance(page, list) for page in pages), 'Invalid asset listing')
    assets = [item for page in pages for item in page]
    wanted = {a['name']: a for a in plan['artifacts'] if a['size'] > 0}
    observed = {}
    for asset in assets:
        name = asset.get('name')
        p.require(name in wanted and name not in observed, 'Unknown or duplicate draft asset')
        p.require(asset.get('state') == 'uploaded', 'Incomplete draft asset requires operator inspection: ' + name)
        item = wanted[name]
        p.require(asset.get('size') == item['size'] and asset.get('digest') == 'sha256:' + item['sha256'], 'Existing draft asset differs or lacks a verified digest: ' + name)
        observed[name] = asset
    return observed


def stage(plan, root):
    p.verify_local(plan, root)
    observed = snapshot(plan)
    for item in plan['artifacts']:
        name = item['name']
        if item['size'] == 0:
            path = root / name
            p.require(item.get('kind') == 'evidence' and item['sha256'] == hashlib.sha256(b'').hexdigest(), 'Invalid empty evidence declaration: ' + name)
            p.require(path.is_file() and not path.is_symlink() and path.stat().st_size == 0 and p.digest(path) == item['sha256'], 'Empty evidence bytes changed: ' + name)
            print(name + ': verified virtual empty evidence; no upload', flush=True)
            continue
        if name in observed:
            print(name + ': verified existing', flush=True)
            continue
        for attempt in range(3):
            # Bind local bytes immediately before every attempt.
            path = root / name
            p.require(path.is_file() and not path.is_symlink() and path.stat().st_size == item['size'] and p.digest(path) == item['sha256'], 'Local artifact changed: ' + name)
            try:
                result = command(['gh', 'release', 'upload', 'v' + plan['version'], str(path), '--repo', p.REPOSITORY])
            except subprocess.TimeoutExpired:
                # Unknown completion is reconciled, never automatically retried.
                observed = snapshot(plan)
                p.require(name in observed, 'Upload timed out without verified completion: ' + name)
                break
            if result.returncode:
                status = re.search(r'\bHTTP\s+(\d{3})\b', result.stderr + '\n' + result.stdout)
                print(name + ': upload failed (' + (status.group(0) if status else 'no HTTP status') + '); reconciling', flush=True)
            observed = snapshot(plan)
            if name in observed:
                print(name + ': verified uploaded', flush=True)
                break
            p.require(result.returncode != 0, 'Upload reported success but asset is absent: ' + name)
            p.require(TRANSIENT.search(result.stderr + '\n' + result.stdout), 'Permanent upload error; stopped: ' + name)
            p.require(attempt < 2, 'Transient upload retries exhausted: ' + name)
            print(name + ': transient error; retry ' + str(attempt + 1), flush=True)
            time.sleep((2, 8)[attempt])
    final = snapshot(plan)
    p.require(set(final) == {a['name'] for a in plan['artifacts'] if a['size'] > 0}, 'Final draft inventory is incomplete')
    print('Complete draft inventory verified; draft remains unpublished', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    stage(p.load_plan(args.plan), args.artifacts)


if __name__ == '__main__':
    main()
