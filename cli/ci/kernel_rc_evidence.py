"""Bind native kernel RC observations to the exact final package bytes."""
import hashlib
import re
from pathlib import PurePosixPath

CHECKS = ('built-bun', 'built-rust', 'installed-bun', 'installed-rust', 'installed-npm')
CHECK_PATHS = tuple('logs/' + name + '.json' for name in CHECKS)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def asset_name(platform, relative, artifacts):
    require(isinstance(relative, str), 'Invalid evidence path')
    path = PurePosixPath(relative)
    require(len(path.parts) == 2 and path.parts[0] in ('package', 'logs')
            and relative == '/'.join(path.parts) and '\\' not in relative
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', path.name)
            and '..' not in path.name, 'Unsafe evidence path')
    if path.parts[0] == 'package':
        return path.name if path.name in artifacts else platform + '-' + path.name
    return platform + '-logs-' + path.name


def validate_native(report, manifest, checks, final_hashes, launcher_hash):
    platform = report.get('platform')
    source, version = report.get('sourceRevision'), report.get('version')
    require(report.get('schema') == 'openprose.kernel-rc-build/1'
            and report.get('imageSource') == 'published-on-run'
            and report.get('testSeamsEnabled') is False
            and report.get('publicationAuthorized') is False
            and report.get('qualification') == 'offline-install-only'
            and type(report.get('modelCalls')) is int and report['modelCalls'] == 0
            and type(report.get('kernelFetches')) is int and report['kernelFetches'] == 0,
            'Invalid native build claims')
    labels = report.get('checks', [])
    require(len(labels) == 5 and {c.get('name') for c in labels} == set(CHECKS)
            and all(c.get('status') == 'passed' for c in labels), 'Missing native check labels')
    require(manifest.get('schema') == 'openprose.local-release-manifest/1'
            and manifest.get('mode') == 'kernel-rc' and manifest.get('platform') == platform
            and manifest.get('version') == version
            and manifest.get('source') == {'revision': source, 'verification': 'matched-product-doctor'}
            and manifest.get('imageSource') == 'published-on-run'
            and manifest.get('releaseEligible') is False
            and manifest.get('publicationAuthorized') is False
            and manifest.get('buildProfiles') == {
                name: {'profile': 'release', 'testSeamsEnabled': False} for name in ('bun', 'rust')},
            'Native manifest identity or release profile mismatch')
    artifacts = manifest.get('artifacts', [])
    require(len(artifacts) == 4
            and sorted((a.get('kind'), a.get('implementation')) for a in artifacts) ==
            [('npm-meta', 'bun'), ('npm-platform', 'bun'), ('standalone-archive', 'bun'), ('standalone-archive', 'rust')]
            and all(a.get('platform') == (None if a['kind'] == 'npm-meta' else platform) for a in artifacts),
            'Native package artifact coverage/platform mismatch')
    require(set(checks) == set(CHECKS), 'Five retained structured checks are required')
    for name, check in checks.items():
        runner = 'rust' if name.endswith('-rust') else 'bun'
        expected_hash = launcher_hash if name == 'installed-npm' else final_hashes[(runner, platform)]
        require(check.get('schema') == 'openprose.published-release-check/1'
                and check.get('status') == 'passed-offline-release-check'
                and check.get('runner') == runner and check.get('commit') == source
                and check.get('version') == version and check.get('binarySha256') == expected_hash
                and check.get('imageSource') == 'published-on-run'
                and check.get('testSeamsEnabled') is False
                and type(check.get('modelCalls')) is int and check['modelCalls'] == 0
                and type(check.get('networkCalls')) is int and check['networkCalls'] == 0,
                'Structured native check does not bind exact release bytes: ' + name)
        if name == 'installed-npm':
            require(re.fullmatch(r'[0-9a-f]{64}', check.get('nodeInterpreterSha256', '')),
                    'Installed npm check lacks Node interpreter identity')


def verify_platform_evidence(plan, root, platform, report_name, binary_hashes):
    # Runtime import keeps the validator usable from publication.py's CLI without
    # a module initialization cycle. These helpers never execute package content.
    import publication as pub
    inventory = {a['name']: a for a in plan['artifacts']}
    require(report_name in inventory and inventory[report_name]['kind'] == 'evidence', 'Native report is not in reviewed inventory')
    report_path = root / report_name
    require(report_path.stat().st_size == inventory[report_name]['size'] and pub.digest(report_path) == inventory[report_name]['sha256'], 'Native report bytes differ from reviewed inventory')
    report = pub.read_json(report_path)
    require(report.get('platform') == platform and report.get('sourceRevision') == plan['source']
            and report.get('version') == plan['version'], 'Native report is for a different candidate')
    manifest_name = platform + '-release-manifest.json'
    require(manifest_name in inventory, 'Native manifest is not retained')
    manifest = pub.read_json(root / manifest_name)
    preflight = pub.read_json(root / plan['preflight'])
    require(manifest.get('embeddedDiagnosticImage') == preflight.get('embeddedDiagnosticImage') and manifest.get('kernelPolicy') == preflight.get('kernelPolicy'), 'Native manifest kernel policy differs from qualification')
    artifact_names = {a['path'] for a in manifest.get('artifacts', [])}
    evidence = report.get('evidence', {})
    require(isinstance(evidence, dict) and set(CHECK_PATHS).union({'package/release-manifest.json'}).issubset(evidence), 'Required structured evidence is missing')
    seen = set()
    for relative, record in evidence.items():
        name = asset_name(platform, relative, artifact_names)
        require(name not in seen and name in inventory, 'Missing or colliding retained evidence')
        seen.add(name)
        item = inventory[name]
        require(set(record) == {'sha256', 'byteLength'} and record['sha256'] == item['sha256']
                and record['byteLength'] == item['size'], 'Retained evidence differs from build report')
        path = root / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == record['byteLength']
                and pub.digest(path) == record['sha256'], 'Retained evidence bytes changed')
    for artifact in manifest.get('artifacts', []):
        name = artifact['path']
        require('package/' + name in evidence and name in inventory, 'Package artifact lacks native evidence')
        item = inventory[name]
        kind = {'standalone-archive': 'standalone', 'npm-meta': 'npm', 'npm-platform': 'npm'}.get(artifact['kind'])
        require(item['sha256'] == artifact['sha256'] and item['size'] == artifact['byteLength']
                and item['kind'] == kind and item['implementation'] == artifact['implementation']
                and item['platform'] == (artifact['platform'] or 'all'), 'Native artifact differs from final reviewed inventory')
    meta = next((a for a in manifest['artifacts'] if a['kind'] == 'npm-meta'), None)
    require(meta is not None, 'Missing root npm package')
    members = pub.archive_members(root / meta['path'])
    require('package/bin/prose.js' in members, 'Missing npm launcher')
    launcher_hash = hashlib.sha256(members['package/bin/prose.js']).hexdigest()
    checks = {name: pub.read_json(root / asset_name(platform, 'logs/' + name + '.json', artifact_names)) for name in CHECKS}
    validate_native(report, manifest, checks, binary_hashes, launcher_hash)
    return manifest
