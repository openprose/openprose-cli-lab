"""Mutation tests for source/check/package custody, using generated npm bytes."""
import hashlib
import json
import unittest
import kernel_rc_evidence as custody
import publication as pub
import test_assemble_kernel_rc as assembly_tests


class EvidenceTests(unittest.TestCase):
    setUp = assembly_tests.AssemblyTests.setUp
    record = assembly_tests.AssemblyTests.record

    def assembled(self):
        import assemble_kernel_rc
        output = self.root / 'assembly'
        plan = assemble_kernel_rc.assemble(self.roots, output, self.evidence)
        hashes = {(runner, platform): hashlib.sha256((runner + platform).encode()).hexdigest()
                  for runner in ('bun', 'rust') for platform in pub.PLATFORMS}
        return plan, output, hashes

    def verify(self, plan, output, hashes):
        return custody.verify_platform_evidence(plan, output, 'darwin-arm64', 'darwin-arm64-build-report.json', hashes)

    def update_bound(self, plan, output, relative, value):
        report_path = output / 'darwin-arm64-build-report.json'
        report = pub.read_json(report_path)
        manifest = pub.read_json(output / 'darwin-arm64-release-manifest.json')
        name = custody.asset_name('darwin-arm64', relative, {a['path'] for a in manifest['artifacts']})
        path = output / name
        path.write_text(json.dumps(value))
        report['evidence'][relative] = {'sha256': pub.digest(path), 'byteLength': path.stat().st_size}
        report_path.write_text(json.dumps(report))
        for name, changed in ((name, path), (report_path.name, report_path)):
            record = next(item for item in plan['artifacts'] if item['name'] == name)
            record.update(sha256=pub.digest(changed), size=changed.stat().st_size)

    def test_all_structured_logs_retained_and_verified(self):
        plan, output, hashes = self.assembled()
        self.verify(plan, output, hashes)
        names = {a['name'] for a in plan['artifacts']}
        for platform in pub.PLATFORMS:
            for check in custody.CHECKS:
                self.assertIn(platform + '-logs-' + check + '.json', names)

    def test_rehashed_passing_log_cannot_describe_other_binary(self):
        plan, output, hashes = self.assembled()
        check = pub.read_json(output / 'darwin-arm64-logs-installed-bun.json')
        check['binarySha256'] = '0'*64
        self.update_bound(plan, output, 'logs/installed-bun.json', check)
        with self.assertRaisesRegex(ValueError, 'exact release bytes'):
            self.verify(plan, output, hashes)

    def test_rehashed_checks_reject_fixed_startup_wrong_source_and_test_seams(self):
        plan, output, hashes = self.assembled()
        original = pub.read_json(output / 'darwin-arm64-logs-built-rust.json')
        for mutation in ({'imageSource': 'embedded'}, {'commit': 'f'*40}, {'testSeamsEnabled': True}):
            with self.subTest(mutation=mutation):
                self.update_bound(plan, output, 'logs/built-rust.json', dict(original, **mutation))
                with self.assertRaisesRegex(ValueError, 'exact release bytes'):
                    self.verify(plan, output, hashes)

    def test_missing_retained_evidence_cannot_pass(self):
        plan, output, hashes = self.assembled()
        plan['artifacts'] = [a for a in plan['artifacts'] if a['name'] != 'darwin-arm64-logs-installed-npm.json']
        with self.assertRaisesRegex(ValueError, 'Missing or colliding'):
            self.verify(plan, output, hashes)

    def test_rehashed_manifest_cannot_change_native_platform(self):
        plan, output, hashes = self.assembled()
        manifest = pub.read_json(output / 'darwin-arm64-release-manifest.json')
        manifest['platform'] = 'linux-x64-gnu'
        self.update_bound(plan, output, 'package/release-manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'manifest identity'):
            self.verify(plan, output, hashes)

    def test_rehashed_manifest_cannot_authorize_or_enable_test_seams(self):
        plan, output, hashes = self.assembled()
        original = pub.read_json(output / 'darwin-arm64-release-manifest.json')
        for mutation in ({'publicationAuthorized': True}, {'releaseEligible': True},
                         {'buildProfiles': {'bun': {'profile': 'release', 'testSeamsEnabled': True}, 'rust': {'profile': 'release', 'testSeamsEnabled': False}}}):
            with self.subTest(mutation=mutation):
                self.update_bound(plan, output, 'package/release-manifest.json', dict(original, **mutation))
                with self.assertRaisesRegex(ValueError, 'manifest identity'):
                    self.verify(plan, output, hashes)

    def test_evidence_paths_are_closed(self):
        for name in ('../key', '/tmp/key', 'logs/../key', 'logs/nested/key', 'package/./file', 'logs/a\\b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                custody.asset_name('darwin-arm64', name, set())


if __name__ == '__main__':
    unittest.main()
