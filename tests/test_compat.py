"""One version everywhere, and the manifest a directory reads."""

import json
import pathlib
import re
import unittest

from khayt_orca import compat

ROOT = pathlib.Path(__file__).parent.parent


class Compat(unittest.TestCase):
    def test_pyproject_says_the_same_version(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', text, re.M).group(1), compat.VERSION)

    def test_no_khayt_release_carries_the_routes_yet(self):
        # Set this at the first Khayt for Mac release that ships them switched on;
        # the release workflow refuses a tag while it is None.
        self.assertIsNone(compat.MIN_KHAYT_MAC)
        self.assertEqual(compat.MIN_ORCA, "2.5.1")

    def test_manifest_shape(self):
        m = compat.manifest("a" * 64, "khayt_orca-0.1.0-py3-none-any.whl")
        self.assertEqual(m["kind"], "slicer")
        self.assertEqual(m["package"], {"name": "khayt-orca", "version": compat.VERSION,
                                        "file": "khayt_orca-0.1.0-py3-none-any.whl", "sha256": "a" * 64})
        self.assertEqual(m["hostVersion"], ">=2.5.1")
        self.assertIsNone(m["requires"]["khaytMac"])
        self.assertNotIn("printer:send", m["scopes"], "v1 never prints")
        json.dumps(m)

    def test_the_release_workflow_never_publishes(self):
        wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("--draft", wf)
        self.assertNotIn("gh release edit", wf)
        self.assertIn("MIN_KHAYT_MAC", wf)
