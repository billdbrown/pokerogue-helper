"""
Locate the Tesseract-OCR binary on Windows.

Discovery order:
  1. tesseract/tesseract.exe  bundled alongside the exe (or script in dev mode)
  2. TESSERACT_CMD environment variable
  3. Windows registry  (HKLM and HKCU SOFTWARE\Tesseract-OCR\InstallDir)
  4. Common install locations
  5. System PATH (shutil.which)
"""

import os
import sys
import shutil


def _base_dir() -> str:
    """Directory to search for a bundled tesseract/ subfolder."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_tesseract() -> str:
    """Return the absolute path to tesseract.exe, or raise RuntimeError."""

    # 1. Bundled copy shipped alongside the exe
    bundled = os.path.join(_base_dir(), "tesseract", "tesseract.exe")
    if os.path.isfile(bundled):
        # Tell Tesseract where its tessdata lives so it doesn't search PATH.
        tessdata = os.path.join(os.path.dirname(bundled), "tessdata")
        if os.path.isdir(tessdata):
            os.environ.setdefault("TESSDATA_PREFIX", tessdata)
        return bundled

    # 2. Environment variable override
    env = os.environ.get("TESSERACT_CMD")
    if env and os.path.isfile(env):
        return env

    # 3. Windows registry
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Tesseract-OCR")
                install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                winreg.CloseKey(key)
                candidate = os.path.join(install_dir, "tesseract.exe")
                if os.path.isfile(candidate):
                    return candidate
            except OSError:
                pass
    except ModuleNotFoundError:
        pass  # not on Windows — fall through to PATH check

    # 4. Common install locations
    for path in [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\Public\Tesseract-OCR\tesseract.exe",
    ]:
        if os.path.isfile(path):
            return path

    # 5. System PATH
    found = shutil.which("tesseract")
    if found:
        return found

    raise RuntimeError(
        "Tesseract OCR not found.\n\n"
        "Install it from https://github.com/UB-Mannheim/tesseract/wiki\n"
        "or set the TESSERACT_CMD environment variable to its full path."
    )


def find_tessdata() -> str:
    """Return the tessdata directory path, or raise RuntimeError."""
    exe = find_tesseract()
    tessdata = os.path.join(os.path.dirname(exe), "tessdata")
    if os.path.isdir(tessdata):
        return tessdata
    env = os.environ.get("TESSDATA_PREFIX")
    if env and os.path.isdir(env):
        return env
    raise RuntimeError(f"tessdata directory not found near {exe}")
