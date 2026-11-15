"""Pairing with a Khayt shop on the same network, and signing requests after.

The plugin never sees the shop's PIN. It asks the Mac to pair while the owner
has clicked "Pair a slicer" (Settings > Online > Connected apps), shows the
same 6-digit check number the Mac shows, and waits for the owner to approve.
Protocol: Khayt's docs/LAN_API.md, "POST /api/connectors/pair".

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
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

CHECK_PREFIX = "khayt-connector-check-v1"
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

def check_number(device_id: str, identity_public: str, server_public: str, plugin_public: str) -> str:
    """The 6 digits both screens show. Keys are base64 of the raw 32 bytes, as sent."""
    text = "\n".join([CHECK_PREFIX, device_id, identity_public, server_public, plugin_public])
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
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


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
    asked = json.dumps({"publicKey": plugin_public, "name": name, "app": app, "version": version}).encode("utf-8")
    status, raw = http("POST", base + "/api/connectors/pair", asked)
    answer = _json(raw)
    if status != 200:
        raise PairingError(answer.get("reason") or "http-%d" % status, answer.get("error", ""))
    try:
        offer = {k: answer[k] for k in ("pairId", "pollSecret", "deviceId", "publicKey", "sig", "identity")}
        identity = Ed25519PublicKey.from_public_bytes(unb64(offer["identity"]))
        identity.verify(unb64(offer["sig"]), enrol_message(offer["deviceId"], offer["publicKey"], plugin_public))
        server = X25519PublicKey.from_public_bytes(unb64(offer["publicKey"]))
        key = device_key(mine.exchange(server), offer["deviceId"])
    except (KeyError, ValueError, InvalidSignature) as e:
        raise PairingError("bad-answer", str(e))

    show_check(check_number(offer["deviceId"], offer["identity"], offer["publicKey"], plugin_public))

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
