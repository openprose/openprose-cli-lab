#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("signal_oracle.py")
SPEC = importlib.util.spec_from_file_location("openprose_signal_oracle", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
oracle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle
SPEC.loader.exec_module(oracle)


@unittest.skipUnless(
    oracle.supported(),
    "direct installed-artifact SIGINT oracle requires native POSIX signals",
)
class DirectInstalledArtifactSignalTest(unittest.TestCase):
    @staticmethod
    def _build_ordinary_echo_products() -> None:
        clean = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith(("PROSE_", "OPENPROSE_"))
            and not name.upper().endswith(
                ("_API_KEY", "_PASSWORD", "_SECRET", "_TOKEN", "_CREDENTIALS")
            )
        }
        clean["OPENPROSE_BUILD_COMMIT"] = "development"
        subprocess.run(
            ["cargo", "build", "--workspace", "--locked", "--offline"],
            cwd=oracle.host.CLI / "rust",
            env=clean,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            [
                "bun",
                "--no-env-file",
                f"--config={oracle.host.CLI / 'bun/config/empty-bunfig.toml'}",
                "run",
                str(oracle.host.CLI / "bun/scripts/image-bundle.ts"),
                "build",
                "--test-seams",
            ],
            cwd=oracle.host.ROOT,
            env=clean,
            stdin=subprocess.DEVNULL,
            check=True,
        )

    def test_rust_and_bun_cancel_and_remove_descendants(self) -> None:
        # Reproduce the hostile order explicitly: ordinary echo-v0 products
        # refuse the sentinel-only mock. The oracle must replace that ambient
        # state and execute private snapshots of its own sentinel build.
        self._build_ordinary_echo_products()
        failures: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="openprose-signal-test-candidates-"
        ) as raw:
            products = oracle.build_owned_sentinel_products(Path(raw) / "snapshots")
            for product in products:
                self.assertIsNotNone(product.execution_executable)
                self.assertNotEqual(product.executable, product.execution_executable)
                with self.subTest(product=product.name):
                    result = oracle.probe(product)
                    failures.extend(
                        f"{product.name}: {failure}" for failure in result.failures
                    )
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main(verbosity=2)
