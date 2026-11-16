"""Talking to the shop's Mac once paired: signed requests, signed answers, sealed bodies.

Protocol: Khayt's docs/LAN_API.md, "The connector's own routes". Shared vectors:
tests/vectors/connector-routes.vectors.json (a copy of Khayt's docs/).

- Every request is signed with the pairing key (`pairing.signed_headers`).
  A file upload is signed at its HEAD over the hash the capture declared
  (`x-khayt-content-sha256`), so the Mac can refuse it before reading a byte.
- Every answer to a signed request carries `x-khayt-reply-mac`. An answer
  whose MAC does not verify is dropped: a device on the Wi-Fi cannot say
  "it's in your inbox" for the shop.
- The capture (what was sliced) and the queue are sealed with AES-256-GCM
  under a key derived from the pairing key, bound to their route.
- On the LAN the G-code itself goes in clear, bound by its signed SHA-256.
  A Cloud relay must NOT reuse this: there the file is sealed end to end.

Only the standard library here, except AES-GCM, which comes from
`cryptography` and is imported when something is actually sealed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import select
import socket
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from . import pairing

REPLY_PREFIX = "khayt-reply-v1"
SEAL_SALT = b"khayt-connector-seal-v1"
HASH_HEADER = "x-khayt-content-sha256"
CONTINUE_WAIT = 3.0
CHUNK = 1 << 20
TIMEOUT = 30.0


# --- shared with the Mac (vectors in tests/vectors) -------------------------

def canonical(method: str, target: str, when: str, nonce: str, body: bytes = b"",
              body_hash: Optional[str] = None) -> str:
    """`khayt-req-v1`; `body_hash` stands in for a body not sent yet (a file head)."""
    digest = body_hash if body_hash is not None else hashlib.sha256(body).hexdigest()
    return "\n".join([pairing.CANONICAL_PREFIX, method.upper(), target, when, nonce, digest])


def signed_headers(key: bytes, device_id: str, method: str, target: str, body: bytes = b"",
                   body_hash: Optional[str] = None, now: Optional[int] = None,
                   nonce: Optional[str] = None) -> dict:
    when = str(int(time.time()) if now is None else now)
    nonce = nonce or secrets.token_hex(16)
    text = canonical(method, target, when, nonce, body, body_hash)
    mac = hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()
    headers = {"x-khayt-device": device_id, "x-khayt-time": when, "x-khayt-nonce": nonce,
               "x-khayt-mac": pairing.b64(mac)}
    if body_hash is not None:
        headers[HASH_HEADER] = body_hash
    return headers


def reply_mac(key: bytes, nonce: str, status: int, body: bytes) -> str:
    text = "\n".join([REPLY_PREFIX, nonce, str(status), hashlib.sha256(body).hexdigest()])
    return pairing.b64(hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest())


def reply_is_signed(key: bytes, nonce: str, status: int, body: bytes, sent: Optional[str]) -> bool:
    return bool(sent) and hmac.compare_digest(reply_mac(key, nonce, status, body), sent)


def seal_key(key: bytes, connector_id: str) -> bytes:
    return pairing.hkdf_sha256(key, SEAL_SALT, connector_id.encode("utf-8"))


def seal_aad(method: str, path: str) -> bytes:
    """`METHOD + " " + path`, the route the sealed body belongs to (always the /api spelling)."""
    return (method.upper() + " " + path).encode("utf-8")


def seal(plain: bytes, key: bytes, aad: bytes, nonce: Optional[bytes] = None) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = nonce or os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plain, aad)  # ciphertext ‖ 16-byte tag
    return json.dumps({"v": 1, "alg": "A256GCM", "nonce": pairing.b64(nonce), "ct": pairing.b64(ct)},
                      separators=(",", ":")).encode("utf-8")


def unseal(sealed: bytes, key: bytes, aad: bytes) -> Optional[bytes]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        e = json.loads(sealed)
        if e.get("v") != 1 or e.get("alg") != "A256GCM":
            return None
        nonce, ct = pairing.unb64(e["nonce"]), pairing.unb64(e["ct"])
        if len(nonce) != 12 or len(ct) < 16:
            return None
        return AESGCM(key).decrypt(nonce, ct, aad)
    except (ValueError, KeyError, TypeError, InvalidTag):
        return None


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# --- the client --------------------------------------------------------------

class ShopError(Exception):
    """The Mac said no (`reason` from its answer), or the answer could not be trusted."""

    def __init__(self, status: int, reason: str, retry_after: Optional[int] = None, body: Optional[dict] = None):
        super().__init__(f"{status} {reason}")
        self.status = status
        self.reason = reason
        self.retry_after = retry_after
        self.body = body or {}


@dataclass
class Answer:
    status: int
    headers: dict
    body: bytes

    def json(self) -> dict:
        try:
            out = json.loads(self.body or b"{}")
            return out if isinstance(out, dict) else {}
        except ValueError:
            return {}


class Shop:
    """One paired Mac: `base` like "http://192.0.2.20:8765", `paired` from `pairing.pair_on_lan`."""

    def __init__(self, base: str, paired: "pairing.Paired", timeout: float = TIMEOUT):
        u = urllib.parse.urlsplit(base)
        if u.scheme != "http" or not u.hostname:
            raise ValueError("the shop's address is http://host:port on this network")
        self.host, self.port = u.hostname, u.port or 80
        self.paired = paired
        self.timeout = timeout

    @property
    def key(self) -> bytes:
        return pairing.unb64(self.paired.key)

    @property
    def device_id(self) -> str:
        return self.paired.device_id

    # The answer is trusted only when signed. An unsigned 401/403/421 is a
    # refusal from before the signature was checked: its reason is reported,
    # nothing else in it is believed.
    def _trusted(self, answer: Answer, nonce: str) -> Answer:
        sent = answer.headers.get("x-khayt-reply-mac")
        if reply_is_signed(self.key, nonce, answer.status, answer.body, sent):
            return answer
        if not sent and answer.status in (401, 403, 404, 421):
            raise ShopError(answer.status, str(answer.json().get("reason") or answer.json().get("error") or "refused"))
        raise ShopError(answer.status, "unsigned-answer")

    def _raise_for(self, answer: Answer) -> None:
        if answer.status < 400:
            return
        body = answer.json()
        retry = answer.headers.get("retry-after")
        raise ShopError(answer.status, str(body.get("reason") or "error"),
                        int(retry) if retry and retry.isdigit() else None, body)

    def _send(self, method: str, path: str, body: bytes = b"", extra: Optional[dict] = None) -> Answer:
        headers = signed_headers(self.key, self.device_id, method, path, body)
        if extra:
            headers.update(extra)
        if body:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body=body or None, headers=headers)
            r = conn.getresponse()
            answer = Answer(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()
        return self._trusted(answer, headers["x-khayt-nonce"])

    def _open(self, sealed: bytes, method: str, path: str) -> dict:
        plain = unseal(sealed, seal_key(self.key, self.device_id), seal_aad(method, path))
        if plain is None:
            raise ShopError(200, "seal")
        return json.loads(plain)

    # GET /api/connectors/self
    def whoami(self) -> dict:
        a = self._send("GET", "/api/connectors/self")
        self._raise_for(a)
        return a.json()

    # DELETE /api/connectors/self
    def unpair(self) -> None:
        self._raise_for(self._send("DELETE", "/api/connectors/self"))

    # POST /api/connectors/slices
    def announce(self, capture: dict) -> dict:
        path = "/api/connectors/slices"
        plain = json.dumps(capture, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        a = self._send("POST", path, seal(plain, seal_key(self.key, self.device_id), seal_aad("POST", path)))
        self._raise_for(a)
        return a.json()

    # GET /api/connectors/slices/{id}
    def slice_state(self, slice_id: str) -> str:
        a = self._send("GET", f"/api/connectors/slices/{slice_id}")
        self._raise_for(a)
        return str(a.json().get("state"))

    # GET /api/connectors/queue → (snapshot or None when unchanged, etag)
    def queue(self, etag: Optional[str] = None):
        path = "/api/connectors/queue"
        a = self._send("GET", path, extra={"If-None-Match": etag} if etag else None)
        if a.status == 304:
            return None, etag
        self._raise_for(a)
        return self._open(a.body, "GET", path), a.headers.get("etag")

    # PUT /api/connectors/slices/{id}/file — the head first, the body only on 100 Continue.
    def upload(self, slice_id: str, file_path: str, sha256: Optional[str] = None) -> dict:
        path = f"/api/connectors/slices/{slice_id}/file"
        size = os.path.getsize(file_path)
        digest = sha256 or file_sha256(file_path)
        headers = signed_headers(self.key, self.device_id, "PUT", path, body_hash=digest)
        headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(size),
                        "Expect": "100-continue"})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.putrequest("PUT", path, skip_accept_encoding=True)
            for k, v in headers.items():
                conn.putheader(k, v)
            conn.endheaders()
            # The Mac judges the head first: wait for 100 Continue or its refusal.
            early = _await_continue(conn.sock, CONTINUE_WAIT)
            if early is None or early == b"continue":
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(CHUNK), b""):
                        conn.sock.sendall(chunk)
            r = conn.getresponse()
            answer = Answer(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()
        answer = self._trusted(answer, headers["x-khayt-nonce"])
        self._raise_for(answer)
        return answer.json()


def _await_continue(sock, wait: float) -> Optional[bytes]:
    """b"continue" after a 100 Continue (consumed), b"answer" when a final answer
    is waiting (left unread for `getresponse`), None when nothing came in time."""
    ready, _, _ = select.select([sock], [], [], wait)
    if not ready:
        return None
    head = sock.recv(64, socket.MSG_PEEK)
    if head.startswith(b"HTTP/1.1 100") or head.startswith(b"HTTP/1.0 100"):
        buf = b""
        while b"\r\n\r\n" not in buf:
            part = sock.recv(1)
            if not part:
                break
            buf += part
        return b"continue"
    return b"answer"
