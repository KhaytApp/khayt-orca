# Khayt for OrcaSlicer

An OrcaSlicer plugin that sends what you slice to your Khayt shop's **From slicer** inbox, and
shows the shop's queue in a **Khayt** tab.

**Not available yet.** Pair a slicer is switched off in current Khayt releases, and there is no
release of this plugin to install. Both come after the plugin has been tested end to end
against the Khayt app on a Mac.

## What it does

- **Pairs with the Khayt app on your Mac** over your shop's network. In Khayt you click **Pair a
  slicer**; in the plugin you enter the address the Mac shows. Both screens show the same
  6-digit number: check it, then approve on the Mac. The plugin never sees your LAN PIN.
- **Sends each exported file to the From slicer inbox.** Files wait in a local outbox until the
  Mac takes them, so a Mac that is off or busy loses nothing. In Khayt, the shop decides what
  each file becomes. Nothing creates a job, a quote or a print on its own, and the plugin never
  starts a print.
- **Shows the shop's queue**: each open job's number, stage and machine, and the name of a file
  you sent for it. Never a job title, customer or note: the Mac does not send them.
- **Shows the shop's price for the plate you just exported, only if the shop allows it.** Prices
  appear only if the shop turns on *Show prices in OrcaSlicer* on the Mac and grants this device.
  The plugin only shows the figure the Mac sends, and never stores it. Cost and margin appear only
  with the second grant, *Price and cost*. Choose a quantity and the Mac prices it again. If the
  Mac can't be reached, the tab says so and shows no number.

Its key opens only the plugin's own routes on the Mac, never your book or customers. On your
network, what you sliced and the price are encrypted, and every answer from the Mac is signed. The
G-code file itself is checked against the hash it was announced with.

## Code

| File | What |
|---|---|
| `khayt_orca/pairing.py` | pairing (commit, then reveal; the check number) |
| `khayt_orca/transport.py` | signed requests, signed answers, sealed bodies; the client |
| `khayt_orca/capture.py`, `gcode.py` | what is announced about a file, and the slicer's own figures |
| `khayt_orca/outbox.py` | the send queue that survives a restart |
| `khayt_orca/price.py` | the shop's price: the facts asked about, what of the answer is believed |
| `khayt_orca/app.py` | the plugin's state and the tab's view, without Orca |
| `khayt_orca/plugin.py`, `tab.py` | the OrcaSlicer entry point (2.5 or later) and the tab page |

## Tests

```
python3 -m unittest discover -s tests -t .
```

`tests/vectors/` are copies of Khayt's shared test vectors (`docs/connector-pair.vectors.json`,
`lan-device-keys`, `connector` (G-code figures), `connector-capture`, `connector-routes`), so the
plugin and the Mac are checked against the same numbers. The tests that need `cryptography` are
skipped without it; CI installs it and runs everything on macOS, Windows and Linux.

MIT licence.
