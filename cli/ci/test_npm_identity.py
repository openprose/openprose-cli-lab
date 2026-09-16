"""Exercise the proposed identity without changing legacy release authority."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
import package_local as package


class IdentityTests(unittest.TestCase):
    def test_names_launcher_bindings_and_self_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            image={'formatVersion':'1','version':'test','sha256':'a'*64,'manifestSha256':'b'*64,'purpose':'transport-test','releaseEligible':False}
            meta,platform=package.npm_packages(root,b'fixture binary','0.1.0','darwin-arm64',0,'c'*40,image,'not-applicable','development',b'hello',package_name='@openprose/prose')
            self.assertEqual(meta.name,'openprose-prose-0.1.0.tgz')
            with tarfile.open(meta) as archive:
                manifest=json.load(archive.extractfile('package/package.json'))
                launcher=archive.extractfile('package/bin/prose.js').read()
                readme=archive.extractfile('package/README.md').read()
            self.assertEqual(manifest['name'],'@openprose/prose')
            self.assertEqual(manifest['openproseLauncher']['sha256'],hashlib.sha256(launcher).hexdigest())
            self.assertIn(b'path.basename(metaRoot) !== "prose"',launcher)
            self.assertNotIn(b'@openprose/prose-cli',launcher+readme)
            self.assertNotIn(b'openprose-prose-cli-',readme)
            self.assertTrue(all(k.startswith('@openprose/prose-') for k in manifest['optionalDependencies']))
            with tarfile.open(platform) as archive:
                p=json.load(archive.extractfile('package/package.json'))
                self.assertEqual(p['name'],'@openprose/prose-darwin-arm64')
                self.assertEqual(p['openproseBinarySha256'],hashlib.sha256(b'fixture binary').hexdigest())

    def test_new_identity_installs_offline_and_preserves_exit_status(self):
        npm = shutil.which("npm")
        node = shutil.which("node")
        if npm is None or node is None:
            self.skipTest("Node and npm are required for the installation test")
        platform = package.current_platform_id()
        if platform.startswith("win32"):
            self.skipTest("Windows distribution is not admitted")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_source, image_hash = package.read_image_manifest(package.SENTINEL_IMAGE_MANIFEST)
            image = package.image_identity(image_source, image_hash)
            binary = ("#!" + sys.executable + "\nimport sys\nprint('identity fixture')\nraise SystemExit(37 if '--fail' in sys.argv else 0)\n").encode()
            runtime = {"minimumGlibc": "2.34", "requiredGlibcMaximum": {"rust": "2.34", "bun": "2.34"}, "executionEvidence": "ubuntu-22.04-only"} if platform.startswith("linux") else "not-applicable"
            meta, native = package.npm_packages(root, binary, "0.1.0", platform, 0, "c"*40, image, runtime, "development", b"hello", package_name="@openprose/prose")
            home = root / "home"; home.mkdir()
            prefix = root / "prefix"
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "npm_config_cache": str(root / "cache"), "npm_config_userconfig": str(root / "empty-npmrc"), "npm_config_registry": "http://127.0.0.1:9"}
            installed = subprocess.run([npm, "install", "--global", "--prefix", str(prefix), "--ignore-scripts", "--offline", "--no-audit", "--no-fund", str(meta), str(native)], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            launcher = prefix / "bin/prose"
            result = subprocess.run([str(launcher), "--fail"], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 37, result.stderr)
            self.assertEqual(result.stdout, "identity fixture\n")

    def test_unapproved_names_and_release_modes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name,mode in [('@other/prose','development'),('@openprose/prose','alpha'),('@openprose/prose','release')]:
                with self.assertRaises(package.PackageError):
                    package.npm_packages(Path(tmp),b'x','0.1.0','darwin-arm64',0,'c'*40,{},'not-applicable',mode,b'',package_name=name)
            self.assertEqual(list(Path(tmp).iterdir()),[])


if __name__=='__main__': unittest.main()
