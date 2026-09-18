#!/usr/bin/env python3
"""Stage an exact private kernel image through the selected CLI's official tool."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

MAX_KERNEL = 32768
MAX_SUPPORT = 1024 * 1024
CONTRACTS = {'taskEnvelope': 'contracts/task-envelope.schema.json',
             'oneFieldFraming': 'contracts/framing.txt',
             'terminalEnvelope': 'contracts/terminal.schema.json'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def bounded(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('regular file required')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError('file limit')
        return value
    finally:
        os.close(descriptor)


def normalized(value):
    if not value or value.startswith(b'\xef\xbb\xbf') or b'\0' in value or b'\r' in value:
        raise ValueError('normalized nonempty text required')
    text = value.decode('utf-8', errors='strict')
    if not text.strip():
        raise ValueError('nonblank text required')
    return text


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def stage(cli_source, kernel, source_revision, output):
    if not re.fullmatch('[0-9a-f]{40}', source_revision):
        raise ValueError('full lowercase source revision required')
    if not all(path.is_absolute() for path in (cli_source, kernel, output)):
        raise ValueError('absolute paths required')
    cli_source = cli_source.resolve(strict=True)
    if not cli_source.is_dir():
        raise ValueError('CLI checkout required')
    kernel_bytes = bounded(kernel, MAX_KERNEL)
    normalized(kernel_bytes)
    template_root = cli_source / 'cli/shared/image/kernel-startup'
    template_bytes = bounded(template_root / 'manifest.template.json', MAX_SUPPORT)
    manifest = json.loads(normalized(template_bytes), object_pairs_hook=strict_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if (not isinstance(manifest, dict)
            or manifest.get('schema') != 'openprose.skill-runtime-image-manifest/1'
            or manifest.get('imageFormatVersion') != 'openprose.skill-runtime-image/1'
            or manifest.get('purpose') != 'canonical-language-runtime'
            or manifest.get('normalization') != {'encoding':'utf-8','newlines':'lf','byteOrderMark':'forbidden','pathSeparator':'/'}
            or not isinstance(manifest.get('payload'), list) or len(manifest['payload']) != 1
            or manifest['payload'][0].get('path') != 'payload/kernel.md'):
        raise ValueError('unsupported kernel startup template')
    contracts = {}
    for field, path in CONTRACTS.items():
        descriptor = manifest[field]
        if descriptor['path'] != path:
            raise ValueError('unsupported template contract path')
        value = bounded(template_root / path, MAX_SUPPORT)
        normalized(value)
        if digest(value) != descriptor['sha256']:
            raise ValueError('template contract integrity')
        contracts[path] = value
    tool = cli_source / 'cli/shared/image/bundle/image_bundle.py'
    tool_hash = digest(bounded(tool, MAX_SUPPORT))
    payload_path = 'payload/kernel.md'
    kernel_hash = digest(kernel_bytes)
    aggregate = digest(payload_path.encode('utf-8') + b'\0' + str(len(kernel_bytes)).encode('ascii') + b'\0' + kernel_bytes + b'\0')
    manifest['imageVersion'] = 'private-review'
    manifest['semanticSourceRevision'] = source_revision
    manifest['releaseEligible'] = False
    manifest['payload'] = [{'path':payload_path,'mediaType':'text/markdown; charset=utf-8','byteLength':len(kernel_bytes),'sha256':kernel_hash}]
    manifest['modelVisibleBytes'] = {'serialization':'ordered-raw-concatenation-v1','byteLength':len(kernel_bytes),'sha256':kernel_hash}
    manifest['aggregateSha256'] = {'algorithm':'sha256-path-length-nul-v1','sha256':aggregate}
    # Exclusive claim; failures retain only this new partial output for inspection.
    output.mkdir(mode=0o700)
    image = output / 'image'
    image.mkdir(mode=0o700)
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(value)
    write(image / payload_path, kernel_bytes)
    for path, value in contracts.items():
        write(image / path, value)
    write(image / 'manifest.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    bundle, checksum = output / 'kernel.bundle.bin', output / 'kernel.bundle.sha256'
    for operation in ('build', 'check'):
        if digest(bounded(tool, MAX_SUPPORT)) != tool_hash:
            raise ValueError('image tool changed')
        subprocess.run([sys.executable, '-I', '-B', str(tool), operation, str(image), str(bundle), '--checksum', str(checksum)],
                       cwd=cli_source, env={}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30, check=True)
    if digest(bounded(tool, MAX_SUPPORT)) != tool_hash:
        raise ValueError('image tool changed')
    os.chmod(bundle, 0o600)
    os.chmod(checksum, 0o600)
    result = {
        'schema':'openprose.private-kernel-image/1', 'releaseEligible':False,
        'imageDirectory':str(image), 'bundle':str(bundle), 'checksum':str(checksum),
        'kernelSha256':kernel_hash, 'kernelBytes':len(kernel_bytes),
        'expectedImageSha256':aggregate, 'bundleSha256':digest(bounded(bundle, 4 * MAX_SUPPORT)),
        'sourceRevision':source_revision, 'sourceRevisionAuthority':'caller-supplied; Git provenance not verified',
        'templateSha256':digest(template_bytes), 'imageToolSha256':tool_hash,
        'contractSha256':{path:digest(value) for path,value in contracts.items()},
        'nativeCliBuilt':False,
        'buildCommands': {
            'bun': {'cwd':str(cli_source),'argv':['bun','--no-env-file',str(cli_source/'cli/bun/scripts/image-bundle.ts'),'build','--image-dir',str(image),'--bundle',str(bundle),'--checksum',str(checksum),'--outfile',str(output/'prose-private')]},
            'rust': {'cwd':str(cli_source),'argv':['cargo','build','--offline','--locked','--manifest-path',str(cli_source/'cli/rust/Cargo.toml'),'-p','prose-cli','--bin','prose'],
                     'environment':{'OPENPROSE_IMAGE_SOURCE_DIR':str(image),'OPENPROSE_IMAGE_BUNDLE':str(bundle),'OPENPROSE_IMAGE_BUNDLE_CHECKSUM':str(checksum),'CARGO_TARGET_DIR':str(output/'rust-target')}}
        }
    }
    write(output / 'staging.json', (json.dumps(result, indent=2) + '\n').encode('utf-8'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli-source', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(stage(args.cli_source, args.kernel, args.source_revision, args.output)))
    except Exception:
        parser.exit(1, 'KERNEL_IMAGE_STAGING_REJECTED: check absolute inputs, revision, normalized kernel, trusted template/tool, and fresh output; a newly created partial output may remain.\n')


if __name__ == '__main__':
    main()
