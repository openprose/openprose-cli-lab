import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('stage_kernel_image',HERE/'stage-kernel-image.py')
staging=importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)

class KernelImageTests(unittest.TestCase):
    def fixture(self, root):
        cli=root/'trusted-cli'
        contracts={'contracts/framing.txt':b'Fixture framing.\n','contracts/task-envelope.schema.json':b'{"type":"object"}\n','contracts/terminal.schema.json':b'{"type":"object"}\n'}
        template={'schema':'openprose.skill-runtime-image-manifest/1','imageFormatVersion':'openprose.skill-runtime-image/1',
                  'imageVersion':'fixture-template','semanticSourceRevision':'e'*40,'purpose':'canonical-language-runtime','releaseEligible':False,
                  'normalization':{'encoding':'utf-8','newlines':'lf','byteOrderMark':'forbidden','pathSeparator':'/'},
                  'payload':[{'path':'payload/kernel.md'}]}
        for field,path in staging.CONTRACTS.items():
            template[field]={'path':path,'sha256':hashlib.sha256(contracts[path]).hexdigest()}
        template_root=cli/'cli/shared/image/kernel-startup'
        for path,value in contracts.items():
            target=template_root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(value)
        (template_root/'manifest.template.json').write_text(json.dumps(template))
        tool=cli/'cli/shared/image/bundle/image_bundle.py';tool.parent.mkdir(parents=True,exist_ok=True)
        # Deliberately synthetic tool protocol fixture, not an official image-format implementation.
        tool.write_text("""import sys,json,hashlib
from pathlib import Path
operation,source,bundle=sys.argv[1:4]
if '--require-release-eligible' in sys.argv: sys.exit(1)
root=Path(source); manifest=json.loads((root/'manifest.json').read_text())
assert manifest['releaseEligible'] is False
assert len(manifest['payload'])==1
payload=(root/'payload/kernel.md').read_bytes()
assert manifest['payload'][0]['sha256']==hashlib.sha256(payload).hexdigest()
encoded=b'synthetic-test-bundle\\n'+(root/'manifest.json').read_bytes()+payload
checksum=Path(sys.argv[sys.argv.index('--checksum')+1])
if operation=='build':
 Path(bundle).write_bytes(encoded);checksum.write_text(hashlib.sha256(encoded).hexdigest())
elif operation=='check':
 assert Path(bundle).read_bytes()==encoded
 assert checksum.read_text()==hashlib.sha256(encoded).hexdigest()
else: sys.exit(2)
""")
        kernel=root/'kernel.md';kernel.write_bytes('# Exact test kernel\n\nUnicode: café.\n'.encode())
        return cli,kernel

    def test_exact_payload_aggregate_and_selected_tool_build_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cli,kernel=self.fixture(root);out=root/'new-image'
            result=staging.stage(cli,kernel,'a'*40,out)
            image=Path(result['imageDirectory']);manifest=json.loads((image/'manifest.json').read_text())
            self.assertEqual((image/'payload/kernel.md').read_bytes(),kernel.read_bytes())
            body=kernel.read_bytes()
            aggregate=hashlib.sha256(b'payload/kernel.md\0'+str(len(body)).encode()+b'\0'+body+b'\0').hexdigest()
            self.assertEqual(result['expectedImageSha256'],aggregate)
            self.assertEqual(manifest['aggregateSha256']['sha256'],aggregate)
            self.assertEqual(manifest['semanticSourceRevision'],'a'*40)
            self.assertEqual(manifest['imageVersion'],'private-review')
            self.assertIs(manifest['releaseEligible'],False)
            self.assertEqual(len(manifest['payload']),1)
            self.assertEqual(result['imageToolSha256'],hashlib.sha256((cli/'cli/shared/image/bundle/image_bundle.py').read_bytes()).hexdigest())
            self.assertEqual(result['bundleSha256'],hashlib.sha256(Path(result['bundle']).read_bytes()).hexdigest())
            self.assertFalse(result['nativeCliBuilt'])
            self.assertIn('not verified',result['sourceRevisionAuthority'])
            self.assertEqual((out/'kernel.bundle.bin').stat().st_mode&0o777,0o600)
            tool=cli/'cli/shared/image/bundle/image_bundle.py'
            rejected=subprocess.run([sys.executable,'-I','-B',str(tool),'check',str(image),result['bundle'],'--checksum',result['checksum'],'--require-release-eligible'],env={},capture_output=True,timeout=10)
            self.assertNotEqual(rejected.returncode,0)

    def test_invalid_revision_kernel_and_existing_output_preserve_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cli,kernel=self.fixture(root)
            for value in ['', 'abc', 'G'*40]:
                with self.assertRaises(ValueError):staging.stage(cli,kernel,value,root/'unused')
            for content in [b'',b'  \n',b'\xff',b'\xef\xbb\xbfabc',b'a\r\nb',b'a\0b',b'a'*(staging.MAX_KERNEL+1)]:
                kernel.write_bytes(content)
                with self.assertRaises((ValueError,UnicodeDecodeError)):staging.stage(cli,kernel,'b'*40,root/'unused')
                self.assertFalse((root/'unused').exists())
            kernel.write_bytes(b'valid\n');out=root/'existing';out.mkdir();(out/'keep').write_text('unchanged')
            with self.assertRaises(FileExistsError):staging.stage(cli,kernel,'b'*40,out)
            self.assertEqual((out/'keep').read_text(),'unchanged')
            linked=root/'linked';linked.symlink_to(out,target_is_directory=True)
            with self.assertRaises(FileExistsError):staging.stage(cli,kernel,'b'*40,linked)
            linked_kernel=root/'linked-kernel';linked_kernel.symlink_to(kernel)
            with self.assertRaises(OSError):staging.stage(cli,linked_kernel,'b'*40,root/'unused')

    def test_contract_tampering_and_unsupported_template_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cli,kernel=self.fixture(root)
            contract=cli/'cli/shared/image/kernel-startup/contracts/framing.txt';contract.write_text('tampered')
            with self.assertRaises(ValueError):staging.stage(cli,kernel,'c'*40,root/'unused')
            self.assertFalse((root/'unused').exists())
            cli,kernel=self.fixture(root)
            path=cli/'cli/shared/image/kernel-startup/manifest.template.json';template=json.loads(path.read_text());template['payload'].append(template['payload'][0]);path.write_text(json.dumps(template))
            with self.assertRaises(ValueError):staging.stage(cli,kernel,'c'*40,root/'unused')
            self.assertFalse((root/'unused').exists())

    def test_command_prints_one_json_result_and_no_native_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cli,kernel=self.fixture(root)
            child=subprocess.run([sys.executable,'-I','-B',str(HERE/'stage-kernel-image.py'),'--cli-source',str(cli),'--kernel',str(kernel),'--source-revision','d'*40,'--output',str(root/'staged')],env={},capture_output=True,text=True,timeout=10,check=True)
            self.assertEqual(child.stderr,'');self.assertEqual(len(child.stdout.strip().splitlines()),1)
            result=json.loads(child.stdout);self.assertFalse(result['nativeCliBuilt'])
            self.assertFalse((root/'staged/prose-private').exists())
            self.assertEqual(result['buildCommands']['rust']['environment']['OPENPROSE_IMAGE_SOURCE_DIR'],result['imageDirectory'])

if __name__=='__main__':unittest.main()
