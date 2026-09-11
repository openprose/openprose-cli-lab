from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "cli" / "ci" / "check_linux_glibc.py"


class LinuxGlibcCheckTests(unittest.TestCase):
    def test_cli_requires_and_uses_one_explicit_direct_readelf(self) -> None:
        missing = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--rust-binary",
                "/fixture/rust",
                "--bun-binary",
                "/fixture/bun",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--readelf", missing.stderr)

        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".linux-glibc-check-") as raw:
            scope = Path(raw)
            rust = scope / "prose-rust"
            bun = scope / "prose-bun"
            rust.write_bytes(b"ELF-rust")
            bun.write_bytes(b"ELF-bun")
            readelf = scope / "readelf"
            readelf.write_text(
                "#!/bin/sh\nprintf 'Name: GLIBC_2.34\\n'\n", encoding="utf-8"
            )
            readelf.chmod(0o700)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--rust-binary",
                    str(rust),
                    "--bun-binary",
                    str(bun),
                    "--readelf",
                    str(readelf),
                ],
                cwd=ROOT,
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": str(scope),
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                report["requiredGlibcMaximum"], {"rust": "2.34", "bun": "2.34"}
            )

            alias = scope / "readelf-alias"
            alias.symlink_to(readelf)
            refused = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--rust-binary",
                    str(rust),
                    "--bun-binary",
                    str(bun),
                    "--readelf",
                    str(alias),
                ],
                cwd=ROOT,
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": str(scope),
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("direct non-symlink executable", refused.stderr)


if __name__ == "__main__":
    unittest.main()
