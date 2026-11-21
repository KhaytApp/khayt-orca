"""One version everywhere, and the manifest a directory reads."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import unittest

from khayt_orca import compat

ROOT = pathlib.Path(__file__).parent.parent

# A copy of `checkSlicerRecord` in Khayt's lib/plugin-directory.js (#2598): the
# record the directory signs into its index. Keep the two the same; the last test
# runs the real one when a Khayt checkout is at hand.
_SLICER_KEYS = {"kind", "id", "version", "host", "hostVersion", "package", "requires", "scopes",
                "hostPermissions", "source", "licence"}
_SLICER_SCOPES = {"slices:send", "queue:read"}


def slicer_record_problems(r):
    p = []
    if not isinstance(r, dict):
        return ["a slicer record is an object"]
    p += [f"{k}: not a slicer record key" for k in r if k not in _SLICER_KEYS]
    if r.get("kind") != "slicer":
        p.append("kind")
    if not re.fullmatch(r"[a-z0-9]+(\.[a-z0-9][a-z0-9-]*)+", str(r.get("id", ""))):
        p.append("id")
    if not re.fullmatch(r"(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,5})", str(r.get("version", ""))):
        p.append("version")
    if r.get("host") != "orca":
        p.append("host")
    if not re.fullmatch(r">=\d+\.\d+(\.\d+)?", str(r.get("hostVersion", ""))):
        p.append("hostVersion")
    pk = r.get("package")
    size = pk.get("size") if isinstance(pk, dict) else None
    if (not isinstance(pk, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(pk.get("name", "")))
            or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", str(pk.get("file", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(pk.get("sha256", "")))
            or not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 20 * 1024 * 1024):
        p.append("package")
    elif "version" in pk:
        p.append("package.version")
    sc = r.get("scopes")
    if not isinstance(sc, list) or not sc or any(s not in _SLICER_SCOPES for s in sc) or len(set(sc)) != len(sc):
        p.append("scopes")
    hp = r.get("hostPermissions")
    if hp is not None and (not isinstance(hp, list) or len(hp) > 10 or any(
            not isinstance(h, str) or not re.fullmatch(r"[a-z_]+(:[A-Za-z0-9._~/-]{1,120})?", h) for h in hp)):
        p.append("hostPermissions")
    if "requires" in r:
        k = r["requires"].get("khayt") if isinstance(r["requires"], dict) else None
        if not isinstance(k, dict) or any(x not in ("mac", "desktop") or not isinstance(v, str) for x, v in k.items()):
            p.append("requires")
    if not re.match(r"^https://", str(r.get("source", ""))):
        p.append("source")
    if not re.fullmatch(r"[A-Za-z0-9.+-]{2,40}( (AND|OR|WITH) [A-Za-z0-9.+-]{2,40})*", str(r.get("licence", ""))):
        p.append("licence")
    return p


class Compat(unittest.TestCase):
    def test_pyproject_says_the_same_version(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', text, re.M).group(1), compat.VERSION)

    def test_no_khayt_release_carries_the_routes_yet(self):
        # Set this at the first Khayt for Mac release that ships them switched on;
        # the release workflow refuses a tag while it is None.
        self.assertIsNone(compat.MIN_KHAYT_MAC)
        self.assertEqual(compat.MIN_ORCA, "2.5.1")

    def test_manifest_is_a_record_the_directory_accepts(self):
        m = compat.manifest("a" * 64, "khayt_orca-0.1.0-py3-none-any.whl", 48213)
        self.assertEqual(slicer_record_problems(m), [])
        self.assertEqual(m["package"], {"name": "khayt-orca", "file": "khayt_orca-0.1.0-py3-none-any.whl",
                                        "sha256": "a" * 64, "size": 48213})
        self.assertEqual((m["id"], m["version"], m["host"], m["hostVersion"]),
                         ("app.khayt.orca", compat.VERSION, "orca", ">=2.5.1"))
        self.assertNotIn("requires", m, "no Khayt for Mac release carries the routes yet")
        self.assertNotIn("printer:send", m["scopes"], "v1 never prints")
        self.assertEqual(m["hostPermissions"], ["threading"], "its files are in Orca's own folder")
        json.dumps(m)

    def test_once_a_mac_release_carries_the_routes_the_record_says_so(self):
        old = compat.MIN_KHAYT_MAC
        compat.MIN_KHAYT_MAC = "4.1.0"
        try:
            m = compat.manifest("b" * 64, "khayt_orca-0.1.0-py3-none-any.whl", 1)
        finally:
            compat.MIN_KHAYT_MAC = old
        self.assertEqual(m["requires"], {"khayt": {"mac": ">=4.1.0"}})
        self.assertEqual(slicer_record_problems(m), [])

    def test_the_mirror_refuses_what_the_directory_refuses(self):
        good = compat.manifest("c" * 64, "khayt_orca-0.1.0-py3-none-any.whl", 10)
        for change in ({"v": 1}, {"protocol": {}}, {"host": "OrcaSlicer"}, {"id": "Khayt"},
                       {"scopes": ["printer:send"]}, {"scopes": []}, {"licence": ""},
                       {"source": "http://x"}, {"hostVersion": "2.5.1"},
                       {"requires": {"khaytMac": None}},
                       {"package": dict(good["package"], version="0.1.0")},
                       {"package": dict(good["package"], size=0)},
                       {"package": dict(good["package"], sha256="A" * 64)}):
            self.assertNotEqual(slicer_record_problems(dict(good, **change)), [], change)

    def test_against_khayts_own_checker_when_it_is_here(self):
        # KHAYT_DIR=<a Khayt checkout with lib/plugin-directory.js> runs the real rule.
        root = os.environ.get("KHAYT_DIR")
        lib = pathlib.Path(root or "", "lib", "plugin-directory.js")
        if not root or not lib.is_file() or not shutil.which("node"):
            self.skipTest("set KHAYT_DIR to a Khayt checkout that has lib/plugin-directory.js")
        m = compat.manifest("d" * 64, "khayt_orca-0.1.0-py3-none-any.whl", 48213)
        js = ("const D = require(process.argv[1]); let s = ''; process.stdin.on('data', d => s += d)"
              ".on('end', () => console.log(JSON.stringify(D.checkSlicerRecord(JSON.parse(s)))));")
        out = subprocess.run(["node", "-e", js, str(lib)], input=json.dumps(m), capture_output=True,
                             text=True, timeout=30, check=True).stdout
        self.assertEqual(json.loads(out), [])

    def test_the_release_workflow_never_publishes(self):
        wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("--draft", wf)
        self.assertNotIn("gh release edit", wf)
        self.assertIn("MIN_KHAYT_MAC", wf)
        self.assertIn("tests.test_orca_permissions", wf, "the permission budget gates a release")
