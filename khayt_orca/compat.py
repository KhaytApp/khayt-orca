"""What this plugin needs, in one place: read by the plugin, its tests and the release workflow.

- `VERSION`: the plugin's own version. pyproject.toml must say the same, and
  a release tag is `v` + this (the workflow checks both).
- `MIN_ORCA`: the oldest OrcaSlicer it is built and tested for. Orca's plugin
  system arrived in 2.5; the bundled CPython 3.12 of 2.5.1 is the floor in
  pyproject.toml.
- `MIN_KHAYT_MAC`: the oldest Khayt for Mac whose LAN server has the connector
  routes this plugin calls (`/api/connectors/self`, `slices`, `queue`).
  **None until a Mac release ships them**: the routes are on Khayt's main but
  dark behind "Pair a slicer", and no release carries them switched on. The
  release workflow refuses to build a release while it is None, so a plugin
  can never be published that no shop's Khayt can talk to.
- `PROTOCOL`: the connector protocol version (pairing v2, routes v1). A Mac that
  does not answer the routes is told apart at run time (`app.Controller`).
"""

VERSION = "0.1.0"
MIN_ORCA = "2.5.1"
MIN_KHAYT_MAC = None
PROTOCOL = {"pairing": 2, "routes": 1}


def manifest(sha256: str, wheel: str) -> dict:
    """The record a plugin directory (and anyone checking a download) reads:
    which file, its hash, and what it needs. Data only."""
    return {
        "v": 1,
        "kind": "slicer",
        "host": "OrcaSlicer",
        "hostVersion": ">=" + MIN_ORCA,
        "package": {"name": "khayt-orca", "version": VERSION, "file": wheel, "sha256": sha256},
        "requires": {"khaytMac": (">=" + MIN_KHAYT_MAC) if MIN_KHAYT_MAC else None},
        "protocol": PROTOCOL,
        # Scopes in the Mac's own words; printing is not a scope (v1 never prints).
        "scopes": ["slices:send", "queue:read"],
        # Asked up front: the worker thread only. Its files are in Orca's own data
        # folder (no question), and the Mac is asked about once, by host.
        "hostPermissions": ["threading"],
        "source": "https://github.com/KhaytApp/khayt-orca",
        "licence": "MIT",
    }
