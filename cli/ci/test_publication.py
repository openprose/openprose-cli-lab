import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import publication as p


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plan = {'schema': 'openprose.cli-publication/1', 'version': '0.15.0-rc.1', 'source': 'a'*40, 'qualification': {'status': 'kernel-smoke-qualified', 'evidence': 'https://github.com/openprose/openprose-expedition/tree/' + 'b'*40 + '/test'}, 'artifacts': [], 'preflight': 'preflight.json', 'macos': {}, 'npmProvenance': True, 'signing': 'apple-notarized'}
        self.image = {'formatVersion': 1, 'version': 'test', 'sha256': 'c'*64, 'manifestSha256': 'd'*64, 'purpose': 'canonical-language-runtime', 'releaseEligible': True}
        self.add('preflight.json', json.dumps({'schema': 'openprose.release-preflight-report/1', 'status': 'pass', 'failures': [], 'sourceSha': 'a'*40, 'version': self.plan['version'], 'protectedAuthority': {'status': 'pass'}, 'image': {'imageSha256': 'c'*64, 'manifestSha256': 'd'*64, 'version': 'test', 'purpose': 'canonical-language-runtime', 'releaseEligible': True}}).encode())
        for platform in p.PLATFORMS:
            for implementation in ('bun', 'rust'):
                self.tar(implementation + '-' + platform + '.tgz', {'root/prose': (implementation+platform).encode()}, 'standalone', platform, implementation)
            if platform.startswith('darwin'):
                self.add(platform + '-receipt.json', b'{}')
                self.add(platform + '-notarization.zip', b'not a real signature')
                self.plan['macos'][platform] = {'receipt': platform + '-receipt.json', 'zip': platform + '-notarization.zip', 'teamId': 'ABCD123456'}
        for name in p.PACKAGES:
            meta = {'name': name, 'version': self.plan['version'], 'repository': {'url': 'git+https://github.com/' + p.REPOSITORY + '.git'}, 'openproseCohort': {'schema': 'openprose.npm-cohort/1', 'releaseChannel': 'release-candidate', 'releaseEligible': False, 'publicationAuthorized': False, 'version': self.plan['version'], 'sourceRevision': 'a'*40, 'image': self.image, 'purpose': 'canonical-language-runtime', 'admittedPlatforms': sorted(p.PLATFORMS)}}
            members = {}
            platform = 'all'
            if name == p.PACKAGES[-1]:
                meta['optionalDependencies'] = {n: self.plan['version'] for n in p.PACKAGES[:-1]}
            else:
                platform = name.removeprefix('@openprose/prose-cli-')
                meta.update(openproseSourceRevision='a'*40, openproseImage=self.image)
                members['package/bin/prose'] = ('bun'+platform).encode()
            members['package/package.json'] = json.dumps(meta).encode()
            self.tar(name.split('/')[1] + '.tgz', members, 'npm', platform, 'bun')
        self.path = self.root / 'plan.json'
        self.save()

    def add(self, name, data, kind='evidence', platform='all', implementation='shared'):
        (self.root/name).write_bytes(data)
        self.plan['artifacts'].append({'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'kind': kind, 'platform': platform, 'implementation': implementation})

    def tar(self, name, members, kind, platform, implementation):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for member, data in members.items():
                info = tarfile.TarInfo(member)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        self.add(name, stream.getvalue(), kind, platform, implementation)

    def save(self):
        self.path.write_text(json.dumps(self.plan))

    def test_complete_inventory_validates_but_is_not_signing_evidence(self):
        plan = p.load_plan(self.path)
        packages, hashes = p.verify_local(plan, self.root)
        self.assertEqual(set(packages), set(p.PACKAGES))
        self.assertEqual(len(hashes), 12)
        with self.assertRaisesRegex(ValueError, 'Wrong signing identity'):
            p.verify_macos(plan, self.root, Path('unused'), 'unused', 'unused')

    def test_development_plan_rejected(self):
        self.plan['qualification']['status'] = 'development'
        self.save()
        with self.assertRaisesRegex(ValueError, 'Kernel qualification'):
            p.load_plan(self.path)

    def test_missing_platform_cannot_be_silently_dropped(self):
        self.plan['artifacts'] = [a for a in self.plan['artifacts'] if a['name'] != 'rust-linux-arm64-gnu.tgz']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Both runners'):
            p.load_plan(self.path)

    def test_mutated_archive_fails_before_parsing(self):
        (self.root/'bun-darwin-arm64.tgz').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            p.verify_local(self.plan, self.root)

    def test_private_provenance_override_not_allowed(self):
        self.plan['npmProvenance'] = False
        self.save()
        with self.assertRaisesRegex(ValueError, 'owner approves'):
            p.load_plan(self.path)

    def test_unsigned_exception_requires_rc_and_no_false_apple_claim(self):
        self.plan['signing'] = 'unsigned-rc'
        self.plan['macos'] = {}
        self.save()
        p.load_plan(self.path)
        self.plan['version'] = '0.15.0'
        self.save()
        with self.assertRaisesRegex(ValueError, 'limited to an explicit RC'):
            p.load_plan(self.path)

    def test_duplicate_json_and_unsafe_names_rejected(self):
        self.path.write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            p.load_plan(self.path)
        for value in ('../x', '/absolute', '--flag', 'x\nINJECT=y'):
            self.assertFalse(p.safe_name(value))

    def test_links_cannot_escape_archive(self):
        path = self.root/'escape.tgz'
        with tarfile.open(path, 'w:gz') as archive:
            item = tarfile.TarInfo('package/bin/prose')
            item.type = tarfile.SYMTYPE
            item.linkname = '/tmp/elsewhere'
            archive.addfile(item)
        with self.assertRaisesRegex(ValueError, 'Links'):
            p.archive_members(path)

    def test_registry_errors_are_not_missing_packages(self):
        import subprocess
        with patch.object(p.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,'{"error":{"code":"E429"}}','')):
            with self.assertRaisesRegex(ValueError, 'Registry lookup failed'):
                p.registry_integrity('@openprose/prose-cli', '0.15.0')
        with patch.object(p.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,'{"error":{"code":"E404"}}','')):
            self.assertIsNone(p.registry_integrity('@openprose/prose-cli', '0.15.0'))

    def test_publication_forbidden_outside_main_workflow(self):
        with patch.dict(p.os.environ, {}, clear=True), patch.object(p, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'main workflow'):
                p.publish(self.plan, self.root, None, None, None)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
