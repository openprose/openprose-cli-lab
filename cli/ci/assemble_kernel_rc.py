#!/usr/bin/env python3
"""Assemble verified native build outputs; retain qualification as a separate input."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import publication as pub


def assemble(roots, output, evidence, live_smoke=None):
    pub.require(not output.exists(), 'Use a fresh assembly directory')
    pub.require(len(roots) == 4, 'Exactly four native build directories required')
    inventory = {}
    reports = {}
    manifests = {}
    version = source = diagnostic = policy = None
    for root in roots:
        report = pub.read_json(root / 'build-report.json')
        platform = report.get('platform')
        pub.require(report.get('schema') == 'openprose.kernel-rc-build/1' and platform in pub.PLATFORMS and platform not in reports, 'Invalid or duplicate native build report')
        pub.require(report.get('imageSource') == 'published-on-run' and report.get('testSeamsEnabled') is False and report.get('qualification') == 'offline-install-only' and report.get('publicationAuthorized') is False and report.get('modelCalls') == 0, 'Unexpected build qualification claims')
        wanted = {'built-bun', 'built-rust', 'installed-bun', 'installed-rust', 'installed-npm'}
        checks = report.get('checks', [])
        pub.require(len(checks) == 5 and {c.get('name') for c in checks} == wanted and all(c.get('status') == 'passed' for c in checks), 'Native build/install checks failed or missing')
        for relative, record in report['evidence'].items():
            path = Path(relative)
            pub.require(not path.is_absolute() and '..' not in path.parts and '\\' not in relative, 'Unsafe evidence path')
            item = root / path
            pub.require(item.is_file() and not item.is_symlink() and item.stat().st_size == record['byteLength'] and pub.digest(item) == record['sha256'], 'Build evidence bytes changed')
        package = root / 'package'
        pub.require('package/release-manifest.json' in report['evidence'], 'Package manifest not bound by build report')
        manifest = pub.read_json(package / 'release-manifest.json')
        pub.require(manifest['mode'] == 'kernel-rc' and manifest['platform'] == platform and manifest['imageSource'] == 'published-on-run', 'Wrong package mode/platform')
        if version is None:
            version, source = manifest['version'], manifest['source']['revision']
            diagnostic, policy = manifest['embeddedDiagnosticImage'], manifest['kernelPolicy']
        pub.require(manifest['version'] == version == report['version'] and manifest['source']['revision'] == source == report['sourceRevision'], 'Mixed candidate versions or source commits')
        pub.require(manifest['embeddedDiagnosticImage'] == diagnostic and manifest['kernelPolicy'] == policy, 'Mixed diagnostic image or kernel policy')
        for item in manifest['artifacts']:
            name = item['path']
            pub.require(pub.safe_name(name), 'Unsafe package artifact name')
            actual = package / name
            pub.require('package/' + name in report['evidence'] and actual.is_file() and not actual.is_symlink() and actual.stat().st_size == item['byteLength'] and pub.digest(actual) == item['sha256'], 'Packaged artifact differs from evidence')
            kind = {'standalone-archive': 'standalone', 'npm-meta': 'npm', 'npm-platform': 'npm'}[item['kind']]
            record = {'name': name, 'sha256': item['sha256'], 'size': item['byteLength'], 'kind': kind, 'platform': item['platform'] or 'all', 'implementation': item['implementation']}
            if name in inventory:
                pub.require(item['kind'] == 'npm-meta' and inventory[name][1] == record, 'Artifact collision or inconsistent root npm package')
            else:
                inventory[name] = (actual, record)
        # Keep complete immutable package evidence, disambiguating target-local names.
        for relative in report['evidence']:
            actual = root / relative
            if relative.startswith('package/') and actual.name not in {a['path'] for a in manifest['artifacts']}:
                name = platform + '-' + actual.name
                pub.require(pub.safe_name(name) and name not in inventory, 'Evidence collision')
                inventory[name] = (actual, {'name': name, 'sha256': pub.digest(actual), 'size': actual.stat().st_size, 'kind': 'evidence', 'platform': platform, 'implementation': 'shared'})
        name = platform + '-build-report.json'
        actual = root / 'build-report.json'
        inventory[name] = (actual, {'name': name, 'sha256': pub.digest(actual), 'size': actual.stat().st_size, 'kind': 'evidence', 'platform': platform, 'implementation': 'shared'})
        reports[platform] = {'status': 'pass', 'report': name}
        manifests[platform] = manifest
    output.mkdir(parents=True)
    for name, (path, _) in inventory.items():
        shutil.copyfile(path, output / name)
    live = pub.read_json(live_smoke) if live_smoke else {'status': 'not-run'}
    preflight = {'schema': 'openprose.kernel-rc-release-evidence/1', 'version': version, 'sourceSha': source, 'status': 'pass' if live_smoke else 'incomplete', 'failures': [] if live_smoke else ['Exact-source live smoke remains required'], 'imageSource': 'published-on-run', 'embeddedDiagnosticImage': diagnostic, 'kernelPolicy': policy, 'platforms': reports, 'liveSmoke': live}
    if live_smoke:
        pub.require(live.get('schema') == 'openprose.kernel-rc-live-smoke/1' and live.get('sourceSha') == source and live.get('version') == version and live.get('status') == 'pass', 'Live smoke does not qualify this source/version')
        for implementation in ('bun', 'rust'):
            observation = live.get('runners', {}).get(implementation, {})
            pub.require(observation.get('accepted') is True and observation.get('helloExact') is True, 'Both runners require accepted Hello World observations')
            item = next(a for a in inventory.values() if a[1]['implementation'] == implementation and a[1]['platform'] == 'darwin-arm64' and a[1]['kind'] == 'standalone')
            binaries = [data for name, data in pub.archive_members(item[0]).items() if name.endswith('/prose')]
            pub.require(len(binaries) == 1 and hashlib.sha256(binaries[0]).hexdigest() == observation.get('binarySha256'), 'Live smoke binary differs from release bytes')
    preflight_path = output / 'kernel-rc-release-evidence.json'
    preflight_path.write_text(json.dumps(preflight, indent=2, sort_keys=True) + '\n')
    artifacts = [record for _, record in inventory.values()]
    artifacts.append({'name': preflight_path.name, 'sha256': pub.digest(preflight_path), 'size': preflight_path.stat().st_size, 'kind': 'evidence', 'platform': 'all', 'implementation': 'shared'})
    plan = {'schema': 'openprose.cli-publication/1', 'version': version, 'source': source, 'qualification': {'status': 'kernel-smoke-qualified' if live_smoke else 'development', 'evidence': evidence}, 'artifacts': artifacts, 'preflight': preflight_path.name, 'macos': {}, 'npmProvenance': True, 'signing': 'unsigned-rc'}
    plan_path = output / 'publication-plan.json'
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    if live_smoke:
        pub.load_plan(plan_path)
        pub.verify_local(plan, output)
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs=4, type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--live-smoke', type=Path)
    args = parser.parse_args()
    result = assemble(args.roots, args.out, args.evidence, args.live_smoke)
    print('Assembly retained; qualification=' + result['qualification']['status'] + '; no publication performed')
