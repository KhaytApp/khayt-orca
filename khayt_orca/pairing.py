"""Pairing with a Khayt shop on the same network, and signing requests after.

The plugin never sees the shop's PIN. It asks the Mac to pair while the owner
has clicked "Pair a slicer" (Settings > Online > Connected apps), shows the
same 6-digit check number the Mac shows, and waits for the owner to approve.
Protocol: Khayt's docs/LAN_API.md, "POST /api/connectors/pair" (v2).

Commit, then reveal: the plugin first sends only SHA-256 of its key and a
random nonce, checks the Mac's signed answer, and only then reveals them. A
device in the middle therefore cannot grind its own keys until the numbers
match; never show a number for an answer whose signature does not verify.

The pure parts (check number, HKDF, request signing) use only the standard
library and are tested against Khayt's shared vectors. X25519 and Ed25519 come
from `cryptography`, imported only when a pairing actually runs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import http.client
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

CHECK_PREFIX = "khayt-connector-check-v2"
COMMIT_PREFIX = "khayt-connector-commit-v2"
OFFER_PREFIX = "khayt-enrol-commit-v2:"
NONCE_BYTES = 16
DEVICE_SALT = b"khayt-device-v1"
CANONICAL_PREFIX = "khayt-req-v1"
ENROL_PREFIX = "khayt-enrol:"
POLL_EVERY = 2.0
POLL_FOR = 130.0


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


# --- shared with the Mac (vectors in tests/vectors) -------------------------

# Every hash input is the exact UTF-8 of the strings as sent (keys and nonce
# base64 of the raw bytes), joined with "\n" after a fixed prefix.

def commitment(plugin_public: str, nonce: str) -> str:
    """What the plugin sends first: base64 SHA-256 of its key and nonce."""
    text = "\n".join([COMMIT_PREFIX, plugin_public, nonce])
    return b64(hashlib.sha256(text.encode("utf-8")).digest())


def offer_message(device_id: str, server_public: str, commit: str) -> bytes:
    """What the Mac's identity key signs in its answer."""
    return (OFFER_PREFIX + device_id + ":" + server_public + ":" + commit).encode("utf-8")


def check_number(device_id: str, identity_public: str, server_public: str, plugin_public: str,
                 nonce: str) -> str:
    """The 6 digits both screens show, once the reveal has been accepted."""
    text = "\n".join([CHECK_PREFIX, device_id, identity_public, server_public, plugin_public, nonce])
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "%06d" % (int.from_bytes(digest[:4], "big") % 1_000_000)


def spaced(check: str) -> str:
    """'418 207', as a person reads it."""
    return check[:3] + " " + check[3:] if len(check) == 6 else check


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869, as CryptoKit's hkdfDerivedSymmetricKey."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def device_key(shared_secret: bytes, device_id: str) -> bytes:
    """The request-signing key from the X25519 secret. Refuses the all-zero secret."""
    if len(shared_secret) != 32 or not any(shared_secret):
        raise ValueError("unusable shared secret")
    return hkdf_sha256(shared_secret, DEVICE_SALT, device_id.encode("utf-8"))


def enrol_message(device_id: str, server_public: str, plugin_public: str) -> bytes:
    return (ENROL_PREFIX + device_id + ":" + server_public + ":" + plugin_public).encode("utf-8")


def canonical(method: str, target: str, when: str, nonce: str, body: bytes) -> str:
    return "\n".join([CANONICAL_PREFIX, method.upper(), target, when, nonce, hashlib.sha256(body).hexdigest()])


def signed_headers(key: bytes, device_id: str, method: str, target: str, body: bytes = b"",
                   now: Optional[int] = None, nonce: Optional[str] = None) -> dict:
    """x-khayt-* headers for one request. `target` is the path plus ?query, as sent."""
    when = str(int(time.time()) if now is None else now)
    nonce = nonce or secrets.token_hex(16)
    mac = hmac.new(key, canonical(method, target, when, nonce, body).encode("utf-8"), hashlib.sha256).digest()
    return {"x-khayt-device": device_id, "x-khayt-time": when, "x-khayt-nonce": nonce, "x-khayt-mac": b64(mac)}


# --- a pairing ---------------------------------------------------------------

@dataclass
class Paired:
    """What the plugin keeps (pairing.json). `key` is secret."""
    base: str
    device_id: str
    key: str
    mac_identity: str

    def to_json(self) -> dict:
        return {"v": 1, "base": self.base, "deviceId": self.device_id, "key": self.key,
                "macIdentity": self.mac_identity}

    @staticmethod
    def from_json(d: dict) -> "Paired":
        return Paired(base=d["base"], device_id=d["deviceId"], key=d["key"], mac_identity=d["macIdentity"])


class PairingError(Exception):
    """`reason` is the server's (pairing-closed, pairing-busy, denied, expired, ...)."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


def _http(method: str, url: str, body: Optional[bytes] = None, headers: Optional[dict] = None,
          timeout: float = 10.0):
    """One plain HTTP request, through http.client and never urllib.

    OrcaSlicer asks the user about a plugin's network access. It judges a
    `urllib.Request` by its full URL and remembers the answer for that URL only,
    so every route (and pairing polls several) asked again; `http.client.connect`
    is judged by the host and remembered for it: one question per Mac.
    """
    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or not u.hostname:
        raise ValueError("the shop's address is http://host:port on this network")
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    sent = dict(headers or {})
    if body is not None:
        sent.setdefault("Content-Type", "application/json")
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=sent)
        reply = conn.getresponse()
        return reply.status, reply.read()
    finally:
        conn.close()


def pair_on_lan(base: str, name: str, version: str, show_check: Callable[[str], None],
                http=_http, sleep=time.sleep, clock=time.monotonic, app: str = "OrcaSlicer") -> Paired:
    """Ask the Mac at `base` (http://host:port) to pair, show the number, wait for the owner.

    `show_check` gets the 6-digit number once the Mac's answer is verified.
    Raises PairingError when the window is shut, busy, denied, expired, or
    the answer does not hold together.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature

    base = base.rstrip("/")
    mine = X25519PrivateKey.generate()
    plugin_public = b64(mine.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    nonce = b64(secrets.token_bytes(NONCE_BYTES))
    commit = commitment(plugin_public, nonce)
    asked = json.dumps({"v": 2, "commit": commit, "name": name, "app": app, "version": version}).encode("utf-8")
    status, raw = http("POST", base + "/api/connectors/pair", asked)
    answer = _json(raw)
    if status != 200:
        raise PairingError(answer.get("reason") or "http-%d" % status, answer.get("error", ""))
    try:
        offer = {k: answer[k] for k in ("pairId", "pollSecret", "deviceId", "publicKey", "sig", "identity")}
        identity = Ed25519PublicKey.from_public_bytes(unb64(offer["identity"]))
        identity.verify(unb64(offer["sig"]), offer_message(offer["deviceId"], offer["publicKey"], commit))
        server = X25519PublicKey.from_public_bytes(unb64(offer["publicKey"]))
        key = device_key(mine.exchange(server), offer["deviceId"])
    except (KeyError, ValueError, TypeError, InvalidSignature) as e:
        raise PairingError("bad-answer", str(e))

    # Only now, with the Mac's key signed over our commitment, do we reveal.
    shown = json.dumps({"publicKey": plugin_public, "nonce": nonce}).encode("utf-8")
    status, raw = http("POST", base + "/api/connectors/pair/" + offer["pairId"] + "/reveal", shown,
                       {"x-khayt-poll": offer["pollSecret"]})
    if status != 200:
        answer = _json(raw)
        raise PairingError(answer.get("reason") or answer.get("state") or "http-%d" % status)

    show_check(check_number(offer["deviceId"], offer["identity"], offer["publicKey"], plugin_public, nonce))

    started = clock()
    while clock() - started < POLL_FOR:
        sleep(POLL_EVERY)
        status, raw = http("GET", base + "/api/connectors/pair/" + offer["pairId"], None,
                           {"x-khayt-poll": offer["pollSecret"]})
        state = _json(raw).get("state")
        if status == 200 and state == "approved":
            return Paired(base=base, device_id=offer["deviceId"], key=b64(key), mac_identity=offer["identity"])
        if status == 200 and state in ("denied", "expired"):
            raise PairingError(state)
        if status != 200 or state != "waiting":
            raise PairingError(state or "http-%d" % status)
    raise PairingError("expired")


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}
