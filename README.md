# Khayt for OrcaSlicer

An OrcaSlicer plugin that sends what you slice to your Khayt shop's **From slicer** inbox.

Work in progress. This first piece pairs the plugin with the Khayt app on your Mac over your
shop's network:

1. In Khayt on the Mac: Settings → Online → Connected apps → **Pair a slicer**.
2. In OrcaSlicer: Khayt → Pair → On this network, and enter the address the Mac shows.
3. Both screens show the same 6-digit number. Check it, then click **Numbers match — approve** on the Mac.

The plugin never sees your LAN PIN, and its key can't read your book, customers or prices.

**Not available yet:** Pair a slicer is switched off in current Khayt releases. It will be
turned on in a later release, after this plugin has been tested end to end against it.

## Tests

```
python3 -m unittest discover -s tests -t .
```

`tests/vectors/` are copies of Khayt's shared test vectors (`docs/connector-pair.vectors.json`,
`docs/lan-device-keys.vectors.json`, `docs/connector.vectors.json`), so the plugin and the Mac
are checked against the same numbers. The pairing tests that need `cryptography` are skipped
without it.

MIT licence.
