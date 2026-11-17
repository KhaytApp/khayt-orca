"""Import, at plugin load, everything the plugin's threads will need later.

Nov 17 2026, second real run: Pair imported `cryptography` lazily on its own
thread. Each first import makes Python write a bytecode file, and it writes it
through a file descriptor, an `open` with no path. OrcaSlicer 2.5.1 asks about a
pathless open as an event ("FsReadWrite:open"), and on a background thread it
refuses at once and queues a box for the user: 36 boxes in one second, one per
module. At load Orca does not ask, so everything is imported here, at load, and
the plugin's threads never import anything (tests/test_orca_permissions.py).
"""


def warm() -> None:
    # The connector's crypto (pairing.py and transport.py import these lazily so
    # their tests run without cryptography; inside Orca it is a declared dependency).
    from cryptography.exceptions import InvalidSignature, InvalidTag  # noqa: F401
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey  # noqa: F401
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401

    # Load OpenSSL's bindings now (the first key made pulls in the backend).
    X25519PrivateKey.generate()

    # The G-code reader, which capture.py imports lazily.
    from . import gcode  # noqa: F401

    # http.client encodes the host with the idna codec on first connect, which
    # imports encodings.idna, stringprep and unicodedata.
    "127.0.0.1".encode("idna")
