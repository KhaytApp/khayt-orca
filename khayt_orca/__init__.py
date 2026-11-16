"""Khayt for OrcaSlicer.

Inside OrcaSlicer (which provides the `orca` module) importing the package loads the
plugin class; anywhere else (tests, tools) only the pure modules are used.
"""

try:
    import orca  # noqa: F401  (OrcaSlicer's own module)
except ImportError:
    pass
else:
    from .plugin import KhaytPlugin  # noqa: F401
