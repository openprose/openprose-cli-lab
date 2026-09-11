from __future__ import annotations

import json
from pathlib import Path
import os
import tempfile
import unittest

import rehearse_release


class RealReleaseRehearsalTest(unittest.TestCase):
    @unittest.skipIf(
        os.name == "nt", "native Windows rehearsal needs Job Object containment"
    )
    def test_build_package_install_measure_and_verify_owned_outputs(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="openprose-real-rehearsal-"
        ) as temporary:
            output = Path(temporary) / "evidence"
            summary = rehearse_release.rehearse(
                output,
                trials=1,
                timeout_seconds=10.0,
                budget_seconds=600.0,
            )
            verification = rehearse_release.verify_rehearsal(output)

            self.assertEqual(summary["status"], "passed-local-development-rehearsal")
            self.assertEqual(
                verification["status"], "verified-local-development-rehearsal"
            )
            self.assertEqual(
                verification["packageIdentity"], summary["packageIdentity"]
            )
            self.assertTrue(summary["bindings"]["threeInstalledSurfacesBound"])
            self.assertFalse(summary["claims"]["releaseEligible"])
            self.assertFalse(summary["claims"]["publicationAuthorized"])
            self.assertEqual(
                set(
                    json.loads((output / "installed-package-raw.json").read_text())[
                        "surfaces"
                    ]
                ),
                {"direct-rust", "direct-bun", "npm-launcher"},
            )


if __name__ == "__main__":
    unittest.main()
