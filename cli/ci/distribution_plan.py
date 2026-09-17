#!/usr/bin/env python3
"""Export a reviewable hosting plan from already-packaged bytes; never publish."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def create_plan(package, evidence):
    require(package.is_dir() and not package.is_symlink(), 'Invalid package directory')
    manifest_path = package / 'release-manifest.json'
    require(manifest_path.is_file() and not manifest_path.is_symlink(), 'Missing regular release manifest')
    manifest = json.loads(manifest_path.read_text())
    require(manifest.get('schema') == 'openprose.local-release-manifest/1', 'Unsupported package manifest')
    require(re.fullmatch(r'https://github.com/openprose/[a-z0-9-]+/(?:blob|tree)/[0-9a-f]{40}/[^\s?#]+', evidence), 'Evidence must be an immutable link')
    version = manifest['version']
    require(re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:rc|dev)\.(?:0|[1-9][0-9]*))?', version), 'Version must use the agreed stable, rc or dev syntax; no automatic renaming')
    source = manifest['source']['revision']
    require(re.fullmatch(r'[0-9a-f]{40}', source), 'Invalid source commit')
    artifacts = []
    names = set()
    for item in manifest['artifacts']:
        name = item['path']
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', name) and '..' not in name and name not in names, 'Unsafe or duplicate artifact name')
        names.add(name)
        path = package / name
        require(path.is_file() and not path.is_symlink(), 'Missing regular artifact')
        data = path.read_bytes()
        require(len(data) == item['byteLength'] and hashlib.sha256(data).hexdigest() == item['sha256'], 'Package bytes changed')
        kind = {'standalone-archive': 'standalone', 'npm-meta': 'npm', 'npm-platform': 'npm'}.get(item['kind'])
        require(kind is not None, 'Unsupported artifact kind')
        artifacts.append({'name': name, 'sha256': item['sha256'], 'size': len(data), 'implementation': item['implementation'], 'platform': item['platform'] or 'all', 'kind': kind})
    # Keep original package and supply-chain evidence bound to the hosting plan.
    for name in ['release-manifest.json', 'sbom.cdx.json', 'provenance.json', 'dependency-evidence.json', 'SHA256SUMS']:
        path = package / name
        require(path.is_file() and not path.is_symlink(), 'Missing package evidence: ' + name)
        data = path.read_bytes()
        artifacts.append({'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'implementation': 'shared', 'platform': 'all', 'kind': 'evidence'})
    pairs = {(a['implementation'], a['platform']) for a in artifacts if a['kind'] == 'standalone'}
    require(pairs == {('bun', manifest['platform']), ('rust', manifest['platform'])}, 'Both implementations must match the package platform')
    return {'schema': 'openprose.cli-distribution/1', 'version': version, 'source': source, 'qualification': {'status': 'development', 'evidence': evidence}, 'artifacts': artifacts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    plan = create_plan(args.package, args.evidence)
    with args.out.open('x') as stream:
        stream.write(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    print('Review plan created; qualification and publication are not granted')
