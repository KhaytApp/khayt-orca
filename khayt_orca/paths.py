"""Where the plugin keeps its files (no `orca` import, so tests can use it)."""

import os


def orca_data_dir(here: str = __file__) -> str:
    """OrcaSlicer's data folder: the one above `orca_plugins` in this file's own
    path, as Orca's own sample plugins find it."""
    parts = os.path.abspath(here).replace("\\", "/").split("/")
    if "orca_plugins" not in parts:
        raise RuntimeError("not installed as an OrcaSlicer plugin")
    return "/".join(parts[: parts.index("orca_plugins")])


def storage_root(here: str = __file__) -> str:
    """The plugin's own folder: the pairing key and the outbox.

    Inside OrcaSlicer's data folder, the one place Orca lets a plugin read,
    write, create and delete without asking (2.5.1 `install_hook`: data_dir() is
    a global allowed root). Anywhere else Orca asks the user each time a file is
    created or deleted, never remembers the answer, and refuses outright off the
    main thread, where the outbox works. Beside `orca_plugins`, not inside it, so
    the pairing survives the plugin being updated or reinstalled.
    """
    return os.path.join(orca_data_dir(here), "khayt-orca")
