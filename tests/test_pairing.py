"""Pairing against Khayt's shared vectors (copied from Khayt's docs/)."""

import json
import pathlib
import unittest

from khayt_orca import pairing

VECTORS = pathlib.Path(__file__).parent / "vectors"


def load(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


try:
    import cryptography  # noqa: F401
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


class CheckNumber(unittest.TestCase):
    cases = load("connector-pair.vectors.json")["cases"]

    def test_vectors(self):
        self.assertGreaterEqual(len(self.cases), 8)
        self.assertGreaterEqual(sum(c["check"].startswith("0") for c in self.cases), 2, "a leading zero is kept")
        for c in self.cases:
            with self.subTest(c["name"]):
                self.assertGreaterEqual(len(bytes.fromhex(c["nonceHex"])), pairing.NONCE_BYTES)
                self.assertEqual(pairing.b64(bytes.fromhex(c["nonceHex"])), c["nonce"])
                self.assertEqual(pairing.commitment(c["pluginPublic"], c["nonce"]), c["commit"])
                self.assertEqual(pairing.offer_message(c["deviceId"], c["serverPublic"], c["commit"]).decode("utf-8"),
                                 c["offerMessage"])
                self.assertEqual(pairing.check_number(c["deviceId"], c["identityPublic"], c["serverPublic"],
                                                      c["pluginPublic"], c["nonce"]), c["check"])

    @unittest.skipUnless(HAVE_CRYPTO, "needs cryptography")
    def test_vector_signatures_and_keys(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        raw = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        for c in self.cases:
            with self.subTest(c["name"]):
                ident = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(c["identitySeedHex"]))
                self.assertEqual(pairing.b64(ident.public_key().public_bytes(*raw)), c["identityPublic"])
                for k in ("server", "plugin"):
                    x = X25519PrivateKey.from_private_bytes(bytes.fromhex(c[k + "PrivateHex"]))
                    self.assertEqual(pairing.b64(x.public_key().public_bytes(*raw)), c[k + "Public"])
                Ed25519PublicKey.from_public_bytes(pairing.unb64(c["identityPublic"])).verify(
                    pairing.unb64(c["sig"]), c["offerMessage"].encode("utf-8"))

    def test_spaced(self):
        self.assertEqual(pairing.spaced("028159"), "028\u2009159", "a thin space, as the Mac shows it")


class DeviceKeys(unittest.TestCase):
    v = load("lan-device-keys.vectors.json")

    def test_hkdf_matches_the_mac(self):
        key = pairing.device_key(pairing.unb64(self.v["shared"]), self.v["deviceId"])
        self.assertEqual(pairing.b64(key), self.v["key"])

    def test_zero_secret_is_refused(self):
        with self.assertRaises(ValueError):
            pairing.device_key(bytes(32), self.v["deviceId"])

    def test_enrol_message(self):
        message = pairing.enrol_message(self.v["deviceId"], self.v["macPublic"], self.v["phonePublic"])
        self.assertEqual(message.decode("utf-8"), self.v["enrol"]["message"])

    def test_signed_requests(self):
        key = pairing.unb64(self.v["key"])
        for name in ("patch", "get"):
            with self.subTest(name):
                case = self.v[name]
                _, method, target, when, nonce, _ = case["canonical"].split("\n")
                body = case.get("body", "").encode("utf-8")
                self.assertEqual(pairing.canonical(method, target, when, nonce, body), case["canonical"])
                headers = pairing.signed_headers(key, self.v["deviceId"], method, target, body,
                                                 now=int(when), nonce=nonce)
                self.assertEqual(headers["x-khayt-mac"], case["mac"])
                self.assertEqual(headers["x-khayt-device"], self.v["deviceId"])


@unittest.skipUnless(HAVE_CRYPTO, "needs cryptography (installed with the plugin; CI has it)")
class PairOnLan(unittest.TestCase):
    """The whole exchange against a fake Mac that does what LanConnectorPairing does."""

    def mac(self, states, tamper=None, reveal_status=200):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives import serialization
        raw = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        identity, server = Ed25519PrivateKey.generate(), X25519PrivateKey.generate()
        dev = "dev_SampleConnector0001"
        server_public = pairing.b64(server.public_key().public_bytes(*raw))
        identity_public = pairing.b64(identity.public_key().public_bytes(*raw))
        seen = {"calls": []}
        replies = list(states)

        def http(method, url, body=None, headers=None):
            seen["calls"].append((method, url, headers, body))
            if url.endswith("/api/connectors/pair"):
                asked = json.loads(body)
                self.assertEqual(asked["v"], 2)
                self.assertNotIn("publicKey", asked, "the key is not sent before the reveal")
                seen["commit"] = asked["commit"]
                sig = identity.sign(pairing.offer_message(dev, server_public, asked["commit"]))
                offer = {"pairId": "abcdefghij12", "pollSecret": "s" * 32, "deviceId": dev,
                         "publicKey": server_public, "sig": pairing.b64(sig), "identity": identity_public}
                if tamper:
                    tamper(offer)
                return 200, json.dumps(offer).encode()
            self.assertEqual(headers, {"x-khayt-poll": "s" * 32})
            if url.endswith("/reveal"):
                shown = json.loads(body)
                self.assertGreaterEqual(len(pairing.unb64(shown["nonce"])), pairing.NONCE_BYTES)
                self.assertEqual(pairing.commitment(shown["publicKey"], shown["nonce"]), seen["commit"],
                                 "the reveal matches the commitment")
                seen["check"] = pairing.check_number(dev, identity_public, server_public, shown["publicKey"],
                                                     shown["nonce"])
                shared = server.exchange(X25519PublicKey.from_public_bytes(pairing.unb64(shown["publicKey"])))
                seen["key"] = pairing.b64(pairing.device_key(shared, dev))
                if reveal_status != 200:
                    return reveal_status, b'{"state":"expired","reason":"bad-reveal"}'
                return 200, b'{"state":"waiting"}'
            return 200, json.dumps({"state": replies.pop(0)}).encode()
        return http, seen

    def run_pair(self, http):
        shown = []
        clock = iter(range(0, 1000, 2))
        result = pairing.pair_on_lan("http://192.0.2.20:8765/", "Studio iMac", "0.1.0", shown.append,
                                     http=http, sleep=lambda s: None, clock=lambda: next(clock))
        return result, shown

    def test_approved(self):
        http, seen = self.mac(["waiting", "waiting", "approved"])
        paired, shown = self.run_pair(http)
        self.assertEqual(shown, [seen["check"]], "the plugin shows the Mac's number")
        self.assertEqual(paired.key, seen["key"], "both sides hold the same key")
        self.assertEqual(paired.base, "http://192.0.2.20:8765")
        self.assertNotIn("pin", repr(seen["calls"]).lower(), "no PIN is ever sent")

    def test_denied(self):
        http, _ = self.mac(["waiting", "denied"])
        with self.assertRaises(pairing.PairingError) as caught:
            self.run_pair(http)
        self.assertEqual(caught.exception.reason, "denied")

    def test_swapped_identity_is_refused_before_any_number(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        other = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

        def tamper(offer):
            offer["identity"] = pairing.b64(other)
        self.refused_before_reveal(tamper)

    def test_swapped_server_key_is_refused_before_any_number(self):
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        other = X25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

        def tamper(offer):
            offer["publicKey"] = pairing.b64(other)
        self.refused_before_reveal(tamper)

    def refused_before_reveal(self, tamper):
        http, seen = self.mac(["approved"], tamper)
        shown = []
        with self.assertRaises(pairing.PairingError) as caught:
            pairing.pair_on_lan("http://192.0.2.20:8765", "Studio iMac", "0.1.0", shown.append,
                                http=http, sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual(caught.exception.reason, "bad-answer")
        self.assertEqual(shown, [], "no number is shown for an answer that does not verify")
        self.assertEqual([c[1].rsplit("/", 1)[-1] for c in seen["calls"]], ["pair"],
                         "the key is never revealed and nothing is polled")

    def test_refused_reveal_shows_no_number(self):
        http, seen = self.mac(["approved"], reveal_status=403)
        shown = []
        with self.assertRaises(pairing.PairingError) as caught:
            pairing.pair_on_lan("http://192.0.2.20:8765", "Studio iMac", "0.1.0", shown.append,
                                http=http, sleep=lambda s: None, clock=lambda: 0)
        self.assertEqual(caught.exception.reason, "bad-reveal")
        self.assertEqual(shown, [])

    def test_closed_window(self):
        def http(method, url, body=None, headers=None):
            return 403, b'{"reason":"pairing-closed","error":"On the Mac, click Pair a slicer"}'
        with self.assertRaises(pairing.PairingError) as caught:
            self.run_pair(http)
        self.assertEqual(caught.exception.reason, "pairing-closed")


if __name__ == "__main__":
    unittest.main()
