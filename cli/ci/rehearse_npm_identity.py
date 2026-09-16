#!/usr/bin/env python3
"""Repackage and install the proposed npm identity offline; no release authority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import package_local as package


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rehearse(source, output):
    require(source.is_dir() and not source.is_symlink(), 'Invalid source package directory')
    manifest_path = source / 'release-manifest.json'
    require(manifest_path.is_file() and not manifest_path.is_symlink(), 'Missing regular manifest')
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest.get('schema') == 'openprose.local-release-manifest/1' and manifest.get('mode') == 'development', 'Only local development packages are accepted')
    items = [item for item in manifest['artifacts'] if item['kind'] == 'standalone-archive' and item['implementation'] == 'bun']
    require(len(items) == 1, 'Expected one Bun standalone')
    item = items[0]
    name = item['path']
    require(Path(name).name == name and name not in {'.', '..'}, 'Unsafe archive name')
    archive_path = source / name
    require(archive_path.is_file() and not archive_path.is_symlink(), 'Missing regular archive')
    require(0 < archive_path.stat().st_size <= 512 * 1024 * 1024, 'Archive exceeds byte budget')
    require(archive_path.stat().st_size == item['byteLength'] and package.sha256_file(archive_path) == item['sha256'], 'Source archive changed')
    with tarfile.open(archive_path) as archive:
        members = [member for member in archive.getmembers() if member.name.endswith('/prose')]
        require(len(members) == 1 and members[0].isfile() and 0 < members[0].size <= 512 * 1024 * 1024, 'Invalid executable member')
        binary = archive.extractfile(members[0]).read()
    npm = shutil.which('npm')
    require(npm is not None, 'npm is required')
    output.mkdir(parents=True, exist_ok=False)
    package_dir = output / 'packages'; package_dir.mkdir()
    meta, native = package.npm_packages(package_dir, binary, manifest['version'], manifest['platform'], 0, manifest['source']['revision'], manifest['image'], manifest['linuxRuntime'], 'development', package.HELLO_EXAMPLE.read_bytes(), package_name='@openprose/prose')
    home = output / 'home'; home.mkdir()
    temporary = output / 'tmp'; temporary.mkdir()
    prefix = output / 'prefix'
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'TMPDIR': str(temporary), 'npm_config_cache': str(output / 'cache'), 'npm_config_userconfig': str(output / 'empty-npmrc'), 'npm_config_registry': 'http://127.0.0.1:9'}
    install = subprocess.run([npm, 'install', '--global', '--prefix', str(prefix), '--ignore-scripts', '--offline', '--no-audit', '--no-fund', str(meta), str(native)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    require(install.returncode == 0, 'Offline npm installation failed')
    run = subprocess.run([str(prefix / 'bin/prose'), '--version'], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    require(run.returncode == 0 and run.stdout.strip() == 'prose ' + manifest['version'] + ' (bun)', 'Installed identity/version check failed')
    report = {'schema': 'openprose.npm-identity-rehearsal/1', 'package': '@openprose/prose', 'sourceManifestSha256': hashlib.sha256(manifest_bytes).hexdigest(), 'binarySha256': hashlib.sha256(binary).hexdigest(), 'artifacts': {path.name: package.sha256_file(path) for path in [meta, native]}, 'versionOutput': run.stdout.strip(), 'offline': True, 'providerCalls': 'none', 'publicationAuthorized': False}
    (output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rehearse(args.package.resolve(), args.out.absolute()), sort_keys=True))
