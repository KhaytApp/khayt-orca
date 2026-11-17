"""OrcaSlicer must not have to ask the user about this plugin more than once.

Nov 17 2026: the first real install asked again and again ("stuck in a loop of a
permission window"). Pairing went through urllib, which Orca 2.5.1 judges by the
full URL, so every route asked anew, and the files lived in ~/.khayt-orca, where
Orca asks about each create and delete, never remembers, and refuses them off the
main thread. `orca_audit_run.py` copies Orca's rules into an audit hook and counts.
The release workflow runs this file on its own, so a regression cannot ship.
"""

import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).parent.parent


def counted(mode):
    out = subprocess.run([sys.executable, "-m", "tests.orca_audit_run", mode], cwd=ROOT,
                         capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipIf(sys.version_info < (3, 10), "http.client.connect is audited from Python 3.10 (Orca bundles 3.12)")
class AsksOnce(unittest.TestCase):
    def test_pair_send_and_ten_minutes_of_polling_ask_once(self):
        got = counted("plugin")
        self.assertEqual(got["fs"], [], "a file question: keep every file inside Orca's data folder")
        self.assertEqual([a["target"] for a in got["net"]], ["host 127.0.0.1"],
                         "one network question, for the Mac's host, remembered")

    def test_the_counter_catches_files_outside_orcas_folder(self):
        got = counted("home")
        self.assertTrue(any(a["kind"] == "fs-create" for a in got["fs"]))

    def test_the_counter_catches_urllib(self):
        got = counted("urllib")
        self.assertGreaterEqual(sum(a["target"].startswith("url ") for a in got["net"]), 3)


class NeverInTheSource(unittest.TestCase):
    def test_no_urllib_requests_and_no_home_folder(self):
        for p in (ROOT / "khayt_orca").glob("*.py"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("urllib.request", text, p.name)
            self.assertNotIn("urlopen", text, p.name)
            self.assertNotIn("expanduser", text, p.name)

    def test_storage_is_inside_orcas_data_folder(self):
        from khayt_orca import paths
        here = "/Users/x/Library/Application Support/OrcaSlicer/orca_plugins/k.whl/__whl_extracted__/khayt_orca/khayt_orca/paths.py"
        got = paths.storage_root(here).replace("\\", "/")
        self.assertTrue(got.endswith("/Library/Application Support/OrcaSlicer/khayt-orca"), got)
        with self.assertRaises(RuntimeError):
            paths.storage_root("/somewhere/else/paths.py")
