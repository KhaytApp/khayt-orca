"""Signed requests, signed answers and sealed bodies against Khayt's shared vectors,
then the whole client against a fake Mac that plays the routes by the same rules
(docs/LAN_API.md, "The connector's own routes")."""

import base64
import hashlib
import http.server
import json
import os
import pathlib
import tempfile
import threading
import unittest

from khayt_orca import capture, pairing, transport

VECTORS = pathlib.Path(__file__).parent / "vectors"

try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


def load(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


class RouteVectors(unittest.TestCase):
    v = load("connector-routes.vectors.json")

    def test_declared_hash_signature(self):
        self.assertGreaterEqual(len(self.v["request"]), 3)
        for c in self.v["request"]:
            with self.subTest(c["name"]):
                body = base64.b64decode(c["bodyB64"])
                self.assertEqual(hashlib.sha256(body).hexdigest(), c["bodyHash"])
                head = transport.canonical(c["method"], c["target"], c["time"], c["nonce"], body_hash=c["bodyHash"])
                self.assertEqual(head, c["canonical"])
                self.assertEqual(transport.canonical(c["method"], c["target"], c["time"], c["nonce"], body), c["canonical"])
                h = transport.signed_headers(bytes.fromhex(c["keyHex"]), "dev_x", c["method"], c["target"],
                                             body_hash=c["bodyHash"], now=int(c["time"]), nonce=c["nonce"])
                self.assertEqual(h["x-khayt-mac"], c["mac"])
                self.assertEqual(h[transport.HASH_HEADER], c["bodyHash"])

    def test_reply_mac(self):
        for c in self.v["reply"]:
            with self.subTest(c["name"]):
                key, body = bytes.fromhex(c["keyHex"]), base64.b64decode(c["bodyB64"])
                self.assertEqual(transport.reply_mac(key, c["nonce"], c["status"], body), c["mac"])
                self.assertTrue(transport.reply_is_signed(key, c["nonce"], c["status"], body, c["mac"]))
                self.assertFalse(transport.reply_is_signed(key, c["nonce"], c["status"] + 1, body, c["mac"]))
                self.assertFalse(transport.reply_is_signed(key, c["nonce"], c["status"], body + b"x", c["mac"]))
                self.assertFalse(transport.reply_is_signed(key, c["nonce"], c["status"], body, None))

    def test_seal_key_and_aad(self):
        for c in self.v["seal"]:
            with self.subTest(c["name"]):
                key = transport.seal_key(bytes.fromhex(c["keyHex"]), c["connectorId"])
                self.assertEqual(key.hex(), c["sealKeyHex"])
                self.assertEqual(transport.seal_aad(c["method"], c["path"]).decode(), c["aad"])

    @unittest.skipUnless(HAVE_CRYPTO, "needs cryptography")
    def test_seal_and_open(self):
        for c in self.v["seal"]:
            with self.subTest(c["name"]):
                key = bytes.fromhex(c["sealKeyHex"])
                aad = c["aad"].encode()
                plain = base64.b64decode(c["plainB64"])
                sealed = json.loads(transport.seal(plain, key, aad, base64.b64decode(c["nonceB64"])))
                self.assertEqual(sealed["ct"], c["ctB64"])
                theirs = json.dumps({"v": 1, "alg": "A256GCM", "nonce": c["nonceB64"], "ct": c["ctB64"]}).encode()
                self.assertEqual(transport.unseal(theirs, key, aad), plain)
                self.assertIsNone(transport.unseal(theirs, key, b"PUT /elsewhere"), "bound to its route")
                self.assertIsNone(transport.unseal(b"not json", key, aad))


class Capture(unittest.TestCase):
    def test_shape_matches_what_the_mac_takes(self):
        allowed = set(load("connector-capture.vectors.json")["cases"][0]["input"].keys()) | {
            "jobId", "machineId", "startPrint"}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Bracket_PLA.gcode")
            pathlib.Path(p).write_bytes(b"G28\nG1 X10\n")
            c = capture.build(p, intent="print", machine_id="m_1", start_print=True, time_sec=60,
                              slicer={"name": "OrcaSlicer", "version": "2.5.1", "plugin": "0.1.0"},
                              filaments=[{"slot": 1, "grams": 1.5}, {"slot": 2, "grams": 0}])
            self.assertTrue(set(c).issubset(allowed), set(c) - allowed)
            sha = hashlib.sha256(b"G28\nG1 X10\n").hexdigest()
            self.assertEqual(c["idem"], "sha256:" + sha)
            self.assertEqual(c["file"], {"name": "Bracket_PLA.gcode", "bytes": 11, "sha256": sha, "type": "gcode"})
            self.assertEqual(len(c["id"]), 16)
            self.assertEqual([f["slot"] for f in c["filaments"]], [1], "an unused slot is not a row")
            stl = os.path.join(d, "part.stl")
            pathlib.Path(stl).write_bytes(b"solid")
            with self.assertRaises(ValueError):
                capture.build(stl)


# --- a fake Mac, by the same rules --------------------------------------------

KEY = bytes(range(32))
DEVICE = "dev_FakePluginDevice01"


class FakeMac(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = {}
    forge = False

    def log_message(self, *a):
        pass

    def _answer(self, status, body, nonce, extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        mac = transport.reply_mac(KEY, nonce, status, data)
        if FakeMac.forge:
            data = data.replace(b"in-inbox", b"in-inbax")
        self.send_header("x-khayt-reply-mac", mac)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _verify(self, body=b"", body_hash=None):
        h = {k.lower(): v for k, v in self.headers.items()}
        text = transport.canonical(self.command, self.path, h.get("x-khayt-time", ""), h.get("x-khayt-nonce", ""),
                                   body, body_hash)
        want = transport.signed_headers(KEY, DEVICE, self.command, self.path, body, body_hash,
                                        now=int(h.get("x-khayt-time", "0")), nonce=h.get("x-khayt-nonce"))
        assert text and want["x-khayt-mac"] == h.get("x-khayt-mac"), "bad signature"
        return h["x-khayt-nonce"]

    def do_GET(self):
        nonce = self._verify()
        if self.path == "/api/connectors/self":
            return self._answer(200, {"connectorId": DEVICE, "name": "Fake", "shop": {"name": "Sample Print Shop"}}, nonce)
        if self.path.startswith("/api/connectors/slices/"):
            sid = self.path.rsplit("/", 1)[1]
            return self._answer(200, {"sliceId": sid, "state": FakeMac.state.get(sid, {}).get("state", "unknown")}, nonce)
        if self.path == "/api/connectors/queue":
            if self.headers.get("If-None-Match") == '"q1"':
                return self._answer(304, b"", nonce, {"ETag": '"q1"'})
            snap = json.dumps({"v": 1, "jobs": [{"id": "J-1", "number": "J-1", "stage": "pending", "machine": "U1-01",
                                                 "file": None}]}).encode()
            sealed = transport.seal(snap, transport.seal_key(KEY, DEVICE), transport.seal_aad("GET", self.path))
            return self._answer(200, sealed, nonce, {"ETag": '"q1"'})
        self._answer(404, {"ok": False}, nonce)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        nonce = self._verify(body)
        key, aad = transport.seal_key(KEY, DEVICE), transport.seal_aad("POST", self.path)
        plain = transport.unseal(body, key, aad)
        if self.path == "/api/connectors/price":
            if plain is None:
                return self._answer(400, {"ok": False, "reason": "seal"}, nonce)
            facts = json.loads(plain)
            FakeMac.state["price-facts"] = facts
            answer = {"prices": "on", "currency": "SAR", "qty": facts["qty"], "unit": 41.5,
                      "total": 41.5 * facts["qty"], "rounded": False, "basis": "measured"}
            return self._answer(200, transport.seal(json.dumps(answer).encode(), key, aad), nonce)
        cap = json.loads(plain)
        FakeMac.state["S" * 16] = {"state": "awaiting-file", "sha": cap["file"]["sha256"], "bytes": cap["file"]["bytes"]}
        self._answer(201, {"sliceId": "S" * 16, "state": "awaiting-file"}, nonce)

    def do_PUT(self):
        declared = self.headers[transport.HASH_HEADER]
        nonce = self._verify(body_hash=declared)
        sid = self.path.split("/")[4]
        s = FakeMac.state[sid]
        if declared != s["sha"]:
            return self._answer(409, {"ok": False, "reason": "hash-not-declared"}, nonce)
        self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if hashlib.sha256(data).hexdigest() != s["sha"]:
            return self._answer(422, {"ok": False, "reason": "hash-mismatch", "retry": True}, nonce)
        s["state"] = "in-inbox"
        self._answer(201, {"sliceId": sid, "state": "in-inbox"}, nonce)


@unittest.skipUnless(HAVE_CRYPTO, "needs cryptography")
class AgainstAFakeMac(unittest.TestCase):
    def setUp(self):
        FakeMac.state, FakeMac.forge = {}, False
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeMac)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.shop = transport.Shop(base, pairing.Paired(base=base, device_id=DEVICE, key=pairing.b64(KEY),
                                                        mac_identity=""))
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def gcode(self, data=b"G28\nG1 X10 Y10\n" * 1000):
        p = os.path.join(self.dir.name, "Bracket_PLA.gcode")
        pathlib.Path(p).write_bytes(data)
        return p

    def test_announce_upload_state_queue(self):
        self.assertEqual(self.shop.whoami()["shop"]["name"], "Sample Print Shop")
        p = self.gcode()
        got = self.shop.announce(capture.build(p, intent="quote"))
        self.assertEqual(got["state"], "awaiting-file")
        self.assertEqual(self.shop.upload(got["sliceId"], p)["state"], "in-inbox")
        self.assertEqual(self.shop.slice_state(got["sliceId"]), "in-inbox")
        snap, etag = self.shop.queue()
        self.assertEqual(snap["jobs"][0]["number"], "J-1")
        self.assertEqual(set(snap["jobs"][0]), {"id", "number", "stage", "machine", "file"})
        self.assertEqual(self.shop.queue(etag), (None, etag), "unchanged: a signed 304")

    def test_price_is_sealed_both_ways(self):
        got = self.shop.price({"v": 1, "qty": 3, "timeSec": 600, "filaments": [{"type": "PLA", "grams": 12.5}]})
        self.assertEqual((got["prices"], got["qty"], got["total"]), ("on", 3, 124.5))
        self.assertEqual(FakeMac.state["price-facts"]["filaments"], [{"type": "PLA", "grams": 12.5}])

    def test_price_facts_over_8_kb_are_not_sent(self):
        with self.assertRaises(ValueError):
            self.shop.price({"v": 1, "qty": 1, "process": {"preset": "x" * 9000}})
        self.assertNotIn("price-facts", FakeMac.state)

    def test_a_refused_head_sends_no_body(self):
        p = self.gcode()
        got = self.shop.announce(capture.build(p))
        with self.assertRaises(transport.ShopError) as e:
            self.shop.upload(got["sliceId"], p, sha256="a" * 64)
        self.assertEqual((e.exception.status, e.exception.reason), (409, "hash-not-declared"))

    def test_an_answer_that_was_changed_is_not_believed(self):
        p = self.gcode()
        got = self.shop.announce(capture.build(p))
        FakeMac.forge = True
        with self.assertRaises(transport.ShopError) as e:
            self.shop.upload(got["sliceId"], p)
        self.assertEqual(e.exception.reason, "unsigned-answer")


if __name__ == "__main__":
    unittest.main()
