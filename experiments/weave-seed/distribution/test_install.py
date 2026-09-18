import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('private_install',HERE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.system=patch.object(installer.platform,'system',return_value='Darwin');self.system.start()
        self.machine=patch.object(installer.platform,'machine',return_value='arm64');self.machine.start()
        self.addCleanup(self.system.stop);self.addCleanup(self.machine.stop)

    def fixture(self,root):
        bundle=root/'bundle';bundle.mkdir()
        for name in installer.REQUIRED:
            path=bundle/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(('fixture:'+name).encode())
        manifest={'schema':installer.SCHEMA,'published':False,'qualification':'synthetic test only',
                  'platform':{'system':'Darwin','machine':'arm64'},'tools':{},'source':{},
                  'files':{name:{'bytes':(bundle/name).stat().st_size,'sha256':installer.sha((bundle/name).read_bytes())} for name in sorted(installer.REQUIRED)}}
        return bundle,manifest,self.save(bundle,manifest)

    def save(self,bundle,manifest):
        value=json.dumps(manifest).encode();(bundle/'manifest.json').write_bytes(value);return installer.sha(value)

    def test_independent_copy_private_modes_and_side_by_side_preserve_old_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle,manifest,digest=self.fixture(root)
            first=installer.install(bundle,digest,root/'first')
            for path in (root/'first').rglob('*'):
                expected=0o700 if path.is_dir() or path.relative_to(root/'first').as_posix() in ['payload/'+p for p in installer.EXECUTABLES] else 0o600
                self.assertEqual(path.stat().st_mode&0o777,expected)
            subject=root/'subject';subject.mkdir();config=subject/'config.json';config.write_text(json.dumps({'actor':[first['executables']['bin/weave-bun']]}));checkpoint=subject/'checkpoint.json';checkpoint.write_text('{"pending":"old-attempt","attempts":3}')
            before=(config.read_bytes(),checkpoint.read_bytes())
            second=installer.install(bundle,digest,root/'second')
            shutil.rmtree(bundle)
            installer.verify(Path(first['payload']),digest);installer.verify(Path(second['payload']),digest)
            self.assertEqual((config.read_bytes(),checkpoint.read_bytes()),before)
            self.assertFalse(first['executablesLaunched']);self.assertFalse(first['pathModified'])
            self.assertNotEqual(first['payload'],second['payload'])

    def test_wrong_hash_platform_schema_inventory_and_unsafe_paths(self):
        cases=[lambda m:m.update(schema='unknown'),lambda m:m.update(platform={'system':'Linux','machine':'x86_64'}),
               lambda m:m['files'].pop('bin/weave-rust'),lambda m:m['files'].update({'../escape':{'bytes':0,'sha256':installer.sha(b'')}}),
               lambda m:m['files'].update({'/absolute':{'bytes':0,'sha256':installer.sha(b'')}}),
               lambda m:m['files'].update({'source//alias':{'bytes':0,'sha256':installer.sha(b'')}}),
               lambda m:m['files'].update({'source\\alias':{'bytes':0,'sha256':installer.sha(b'')}}),
               lambda m:m['files'].update({'manifest.json':{'bytes':0,'sha256':installer.sha(b'')}}),
               lambda m:m['files']['README.md'].update(bytes=True),lambda m:m['files']['README.md'].update(bytes=installer.MAX_FILE+1)]
        for modify in cases:
            with self.subTest(modify=modify),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);bundle,manifest,_=self.fixture(root);modify(manifest);digest=self.save(bundle,manifest)
                with self.assertRaises((ValueError,OSError)):installer.install(bundle,digest,root/'new')
                self.assertFalse((root/'new').exists())
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle,_,_=self.fixture(root)
            with self.assertRaises(ValueError):installer.install(bundle,'a'*64,root/'new')
            self.assertFalse((root/'new').exists())
            digest=installer.sha((bundle/'manifest.json').read_bytes())
            with patch.object(installer.platform,'machine',return_value='x86_64'):
                with self.assertRaises(ValueError):installer.install(bundle,digest,root/'new')
            raw=(bundle/'manifest.json').read_text().replace('"schema":','"schema":"duplicate","schema":',1).encode()
            (bundle/'manifest.json').write_bytes(raw)
            with self.assertRaises(ValueError):installer.install(bundle,installer.sha(raw),root/'new')
            self.assertFalse((root/'new').exists())

    def test_corruption_missing_extra_symlink_fifo_and_unlisted_directory(self):
        for mutation in ['corrupt','missing','extra','symlink','fifo','directory']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);bundle,_,digest=self.fixture(root);readme=bundle/'README.md'
                if mutation=='corrupt':readme.write_text('changed')
                if mutation=='missing':readme.unlink()
                if mutation=='extra':(bundle/'unlisted').write_text('unlisted')
                if mutation=='symlink':readme.unlink();readme.symlink_to(bundle/'LICENSE')
                if mutation=='fifo':readme.unlink();os.mkfifo(readme)
                if mutation=='directory':(bundle/'unlisted-directory').mkdir()
                with self.assertRaises((ValueError,OSError)):installer.install(bundle,digest,root/'new')
                self.assertFalse((root/'new').exists())

    def test_existing_destinations_and_nested_locations_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle,_,digest=self.fixture(root);existing=root/'existing';existing.mkdir();(existing/'keep').write_text('keep')
            linked=root/'linked';linked.symlink_to(existing,target_is_directory=True)
            for target in [existing,linked,bundle/'nested',root]:
                with self.assertRaises((ValueError,FileExistsError)):installer.install(bundle,digest,target)
            self.assertEqual((existing/'keep').read_text(),'keep')
            self.assertFalse((bundle/'nested').exists())

    def test_midcopy_failure_retains_unaccepted_partial_and_preserves_prior_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle,_,digest=self.fixture(root);old=installer.install(bundle,digest,root/'old');before=(root/'old/installation.json').read_bytes()
            original=installer.stream_file;count=0
            def fail_midcopy(source,entry,destination=None):
                nonlocal count
                if destination is not None:
                    count+=1
                    if count==2:raise OSError('injected write failure')
                return original(source,entry,destination)
            with patch.object(installer,'stream_file',side_effect=fail_midcopy):
                with self.assertRaises(OSError):installer.install(bundle,digest,root/'partial')
            self.assertTrue((root/'partial/payload').is_dir());self.assertFalse((root/'partial/installation.json').exists())
            self.assertEqual((root/'old/installation.json').read_bytes(),before);installer.verify(Path(old['payload']),digest)

    def test_source_inventory_change_during_copy_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bundle,_,digest=self.fixture(root);original=installer.stream_file;changed=False
            def mutate_after_copy(source,entry,destination=None):
                nonlocal changed
                result=original(source,entry,destination)
                if destination is not None and not changed:
                    changed=True;(bundle/'late-extra').write_text('changed source inventory')
                return result
            with patch.object(installer,'stream_file',side_effect=mutate_after_copy):
                with self.assertRaises(ValueError):installer.install(bundle,digest,root/'partial')
            self.assertFalse((root/'partial/installation.json').exists())

    def test_copied_standalone_installer_imports_without_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);script=root/'install.py';shutil.copyfile(HERE/'install.py',script)
            result=subprocess.run([sys.executable,'-I','-B',str(script),'--help'],env={},capture_output=True,text=True,timeout=5,check=True)
            self.assertIn('--manifest-sha256',result.stdout)
            self.assertEqual(result.stderr,'')

if __name__=='__main__':unittest.main()
