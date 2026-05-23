"""Central location for all user-data paths.

Everything the app writes that should persist across updates lives under
  %LOCALAPPDATA%\PokerogueHelper\   (Windows)
  ~/.local/share/PokerogueHelper/   (fallback)

The directory is created on first import.
"""

import os

_APP_NAME = "PokerogueHelper"

def _base() -> str:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return os.path.join(local, _APP_NAME)
    xdg = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(xdg, _APP_NAME)

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
