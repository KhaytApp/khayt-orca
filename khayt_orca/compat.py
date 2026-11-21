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


ID = "app.khayt.orca"
# What the plugin may use on the Mac, in the Mac's own words. Printing is not a
# scope (v1 never prints). The Mac grants these at pairing; the record says them.
SCOPES = ["slices:send", "queue:read"]


def manifest(sha256: str, wheel: str, size: int) -> dict:
    """The `kind: "slicer"` record the plugin directory signs into its index, in the
    shape Khayt's lib/plugin-directory.js `checkSlicerRecord` accepts: which file,
    its hash and size (Orca checks no signature, so the hash is the only proof of
    a download), and what it needs. Data only. The record's `version` is the
    package's; `requires` appears once a Khayt for Mac release carries the routes."""
    record = {
        "kind": "slicer",
        "id": ID,
        "version": VERSION,
        "host": "orca",
        "hostVersion": ">=" + MIN_ORCA,
        "package": {"name": "khayt-orca", "file": wheel, "sha256": sha256, "size": size},
        "scopes": list(SCOPES),
        # Asked up front: the worker thread only. Its files are in Orca's own data
        # folder (no question), and the Mac is asked about once, by host.
        "hostPermissions": ["threading"],
        "source": "https://github.com/KhaytApp/khayt-orca",
        "licence": "MIT",
    }
    if MIN_KHAYT_MAC:
        record["requires"] = {"khayt": {"mac": ">=" + MIN_KHAYT_MAC}}
    return record
