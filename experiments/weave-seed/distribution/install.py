#!/usr/bin/env python3
"""Install a reviewed private bundle side by side. No launch, network or PATH edits."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys

MAX_MANIFEST = 4 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 4096
SCHEMA = 'openprose.private-local-bundle/1'
EXECUTABLES = {'bin/weave-bun', 'bin/weave-rust'}
REQUIRED = EXECUTABLES | {'README.md', 'LICENSE', 'source/LICENSE',
    'source/experiments/weave-seed/getting-started/create.mjs',
    'source/experiments/weave-seed/getting-started/configure.mjs'}


def reject():
    raise ValueError('PRIVATE_INSTALL_REJECTED')


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            reject()
        result[key] = value
    return result


def sha(value):
    return hashlib.sha256(value).hexdigest()


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        reject()
    return info


def read_small(path, maximum):
    info = regular(path)
    if info.st_size > maximum:
        reject()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            reject()
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            reject()
        return data
    finally:
        os.close(descriptor)


def valid_path(name):
    return (isinstance(name, str) and 0 < len(name.encode('utf-8')) <= 4096
        and '\\' not in name and '\0' not in name and not name.startswith('/')
        and all(part not in ('', '.', '..') for part in name.split('/'))
        and str(PurePosixPath(name)) == name)


def load_manifest(root, expected):
    if not re.fullmatch('[0-9a-f]{64}', expected):
        reject()
    raw = read_small(root / 'manifest.json', MAX_MANIFEST)
    if sha(raw) != expected:
        reject()
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
        parse_constant=lambda _: reject())
    if (not isinstance(value, dict) or set(value) != {'schema','published','qualification','platform','tools','source','files'}
        or value['schema'] != SCHEMA or value['published'] is not False
        or not isinstance(value['qualification'],str) or not value['qualification'].strip()
        or not isinstance(value['tools'],dict) or not isinstance(value['source'],dict)
        or value['platform'] != {'system':'Darwin','machine':'arm64'}
        or (platform.system(), platform.machine()) != ('Darwin','arm64')):
        reject()
    files = value['files']
    if not isinstance(files, dict) or not REQUIRED.issubset(files) or len(files) > MAX_FILES:
        reject()
    total = 0
    for name, entry in files.items():
        if not valid_path(name) or name == 'manifest.json' or not isinstance(entry, dict) or set(entry) != {'sha256','bytes'}:
            reject()
        if (not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
            or type(entry['bytes']) is not int or not 0 <= entry['bytes'] <= MAX_FILE):
            reject()
        total += entry['bytes']
    if total > MAX_TOTAL:
        reject()
    # No file may also serve as another file's parent directory.
    names = set(files) | {'manifest.json'}
    if any(any(str(parent) in names for parent in PurePosixPath(name).parents if str(parent) != '.') for name in names):
        reject()
    return raw, value


def inventory_paths(root):
    """Reject symlinks, special files and unlisted directories, without following them."""
    found = set()
    directories = set()
    def visit(directory, depth):
        if depth > 64:
            reject()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                name = path.relative_to(root).as_posix()
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    directories.add(name)
                    if len(directories) > MAX_FILES * 4:
                        reject()
                    visit(path, depth + 1)
                elif stat.S_ISREG(info.st_mode):
                    found.add(name)
                    if len(found) > MAX_FILES + 1:
                        reject()
                else:
                    reject()
    if not stat.S_ISDIR(root.lstat().st_mode):
        reject()
    visit(root, 0)
    return found, directories


def stream_file(source, entry, destination=None):
    before = regular(source)
    if before.st_size != entry['bytes']:
        reject()
    descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    writer = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev,before.st_ino) != (opened.st_dev,opened.st_ino):
            reject()
        if destination is not None:
            writer = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        count = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            count += len(chunk)
            if count > entry['bytes']:
                reject()
            digest.update(chunk)
            if writer is not None:
                view = memoryview(chunk)
                while view:
                    written = os.write(writer, view)
                    if written <= 0:
                        reject()
                    view = view[written:]
        if count != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            reject()
        if writer is not None:
            os.fsync(writer)
    finally:
        os.close(descriptor)
        if writer is not None:
            os.close(writer)


def verify(root, expected):
    raw, manifest = load_manifest(root, expected)
    expected_files = set(manifest['files']) | {'manifest.json'}
    expected_directories = {str(parent) for name in expected_files for parent in PurePosixPath(name).parents if str(parent) != '.'}
    actual_files, actual_directories = inventory_paths(root)
    if actual_files != expected_files or actual_directories != expected_directories:
        reject()
    for name, entry in manifest['files'].items():
        stream_file(root / name, entry)
    if read_small(root / 'manifest.json', MAX_MANIFEST) != raw:
        reject()
    return raw, manifest


def install(bundle, expected, destination):
    if not bundle.is_absolute() or not destination.is_absolute() or bundle.is_symlink():
        reject()
    bundle = bundle.resolve(strict=True)
    # Canonical existing parent prevents ambiguous destinations, without creating it.
    target = destination.parent.resolve(strict=True) / destination.name
    if (not destination.name or destination.name in ('.','..') or target == bundle
        or bundle in target.parents or target in bundle.parents):
        reject()
    raw, manifest = verify(bundle, expected)
    target.mkdir(mode=0o700)  # Exclusive claim; no existing directory/file/symlink replacement.
    owner = target.lstat()
    payload = target / 'payload'
    payload.mkdir(mode=0o700)
    for name, entry in sorted(manifest['files'].items()):
        out = payload / name
        for parent in reversed(out.parent.relative_to(payload).parents):
            if str(parent) != '.':
                (payload / parent).mkdir(mode=0o700, exist_ok=True)
        out.parent.mkdir(mode=0o700, exist_ok=True)
        stream_file(bundle / name, entry, out)
        os.chmod(out, 0o700 if name in EXECUTABLES else 0o600)
    descriptor = os.open(payload / 'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    # Source and installed inventories must still match before acceptance is recorded.
    verify(bundle, expected)
    verify(payload, expected)
    current = target.lstat()
    if (owner.st_dev,owner.st_ino) != (current.st_dev,current.st_ino):
        reject()
    record = {'schema':'openprose.private-installation/1','status':'installed-files-verified',
        'manifestSha256':expected,'platform':manifest['platform'],
        'installation':str(target),'payload':str(payload),'sourceRoot':str(payload/'source'),
        'executables':{name: str(payload/name) for name in sorted(EXECUTABLES)},
        'helpers':{name:str(payload/'source/experiments/weave-seed/getting-started'/name) for name in ['create.mjs','configure.mjs']},
        'providerVerified':False,'executablesLaunched':False,'pathModified':False,
        'upgradePolicy':'side-by-side; existing configurations and checkpoint state are not modified'}
    receipt = target / 'installation.json'
    descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(record,stream,indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.bundle,args.manifest_sha256,args.destination)))
    except Exception:
        parser.exit(1,'PRIVATE_INSTALL_REJECTED: verify the reviewed manifest hash, supported platform, regular complete bundle and fresh destination; any newly created partial installation remains unaccepted.\n')


if __name__ == '__main__':
    main()
