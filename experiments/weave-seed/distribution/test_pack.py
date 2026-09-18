import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pack

class PackTests(unittest.TestCase):
    def test_explicit_source_allowlist_excludes_build_secrets_results_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'seed';root.mkdir()
            for relative in ['README.md','local/run.mjs','rust/src/lib.rs','rust/Cargo.lock','rust/target/private.json','node_modules/secret.mjs','results/private.json','.env','local/.env','local/.hidden.mjs','local/binary','unapproved/secret.json']:
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            (root/'local/linked.mjs').symlink_to(root/'README.md')
            self.assertEqual(set(map(str,pack.source_files(root))),{'README.md','local/run.mjs','rust/src/lib.rs','rust/Cargo.lock'})
            out=Path(temp)/'copy';pack.copy_sources(out,root)
            self.assertEqual((out/'local/run.mjs').read_text(),'fixture')
            self.assertFalse((out/'local/.env').exists())

    def test_existing_output_never_overwritten_or_built(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'exists';out.mkdir();(out/'preserve').write_text('keep')
            executable=Path('/bin/sh')
            with patch('pack.platform.system',return_value='Darwin'),patch('pack.platform.machine',return_value='arm64'),patch('pack.run') as run:
                with self.assertRaises(FileExistsError):pack.pack(executable,executable,out)
                run.assert_not_called()
            self.assertEqual((out/'preserve').read_text(),'keep')

    def test_inventory_detects_changed_extra_and_missing_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'file').write_text('original')
            (root/'manifest.json').write_text(json.dumps({'files':pack.inventory(root)}));self.assertTrue(pack.verify(root))
            (root/'file').write_text('changed')
            with self.assertRaises(ValueError):pack.verify(root)
            (root/'file').write_text('original');(root/'extra').write_text('added')
            with self.assertRaises(ValueError):pack.verify(root)
            (root/'extra').unlink();(root/'file').unlink()
            with self.assertRaises(ValueError):pack.verify(root)

    def test_smoke_generation_uses_relocated_bundle_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle=Path(temp)/'bundle';bundle.mkdir();(bundle/'source').mkdir();(bundle/'bin').mkdir()
            def fake_run(argv,**kwargs):
                args=list(map(str,argv))
                if args[-1]=='--help':return 'usage'
                if 'create.mjs' in args[2]:
                    target=Path(args[-1]);target.mkdir();copied=Path(args[2]).parents[1]
                    config=target/'config.json';config.write_text(json.dumps({'assessor':['bun','--no-env-file',str(copied/'local/fixture.mjs')],'actor':['bun','--no-env-file',str(copied/'local/fixture.mjs')]}))
                    (target/'source.txt').write_text('one');(target/'report.txt').write_text('one');return json.dumps({'config':str(config)})
                if args[1]=='check':return json.dumps({'status':'configured','providerVerified':False})
                if args[1]=='status':return json.dumps({'checkpoint':None})
                marker=Path(args[2]).parent/'stepped'
                state='reused' if marker.exists() else 'satisfied';marker.touch();return json.dumps({'status':state,'attempts':1})
            with patch('pack.run',side_effect=fake_run):
                results=pack.smoke(bundle,Path('/absolute/bun'))
            self.assertEqual(len(results),2);self.assertTrue(all(r['sourceMapping']=='relocated bundle only' for r in results))

if __name__=='__main__':unittest.main()
