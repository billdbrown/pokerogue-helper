"""Central location for all user-data paths.

In development (non-frozen): writes to <repo>/data/ for version control.
In distribution (frozen app): writes to the OS user-data directory.
  Windows: %LOCALAPPDATA%\PokerogueHelper\
  macOS/Linux: ~/.local/share/PokerogueHelper/
"""

import os
import sys

_APP_NAME = "PokerogueHelper"


def _base() -> str:
    if getattr(sys, "frozen", False):
        # Packaged build — use OS user-data directory
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return os.path.join(local, _APP_NAME)
        xdg = os.environ.get(
            "XDG_DATA_HOME",
            os.path.join(os.path.expanduser("~"), ".local", "share"),
        )
        return os.path.join(xdg, _APP_NAME)
    # Development build — repo-relative data/ for version control
    _src_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(_src_dir, "..", "data"))


DATA_DIR = _base()
os.makedirs(DATA_DIR, exist_ok=True)


def data_path(*parts: str) -> str:
    """Return an absolute path inside the user data directory.

    If the path looks like a directory (no extension on the last part),
    it is created automatically.
    """
    path = os.path.join(DATA_DIR, *parts)
    if parts and "." not in os.path.basename(parts[-1]):
        os.makedirs(path, exist_ok=True)
    return path
