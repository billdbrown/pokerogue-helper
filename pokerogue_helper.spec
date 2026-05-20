# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Pokerogue Helper (embedded single-window build).
#
# Output: dist\PokerogueHelper\  — a folder you can zip and ship.
# QtWebEngine requires QtWebEngineProcess.exe to live alongside the main exe,
# so true single-file packaging is not possible; onedir is the right target.
#
# Build with:  .\build.ps1  (handles Tesseract bundling and cleanup too)

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

with open("version.txt") as _f:
    _ver = _f.read().strip().replace(".", "")
_APP_NAME = f"PokerogueHelper_v{_ver}"

# Collect Qt6 data files but skip QML and QScintilla — not used by this app.
qt6_datas = [
    (src, dst) for src, dst in collect_data_files("PyQt6")
    if "\\qml\\" not in src.lower() and "/qml/" not in src.lower()
    and "\\qsci\\" not in src.lower() and "/qsci/" not in src.lower()
]
qt6_binaries = collect_dynamic_libs("PyQt6")

a = Analysis(
    ["main.py"],
    pathex=["src"],   # source modules live under src/ — let PyInstaller find them
    binaries=qt6_binaries,
    datas=[
        ("resources", "resources"),
        ("version.txt", "."),
        *qt6_datas,
    ],
    hiddenimports=[
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebChannel",
        "PyQt6.QtNetwork",
        "PyQt6.QtPrintSupport",
        "PyQt6.sip",
        "PIL",
        "PIL.Image",
        "PIL.ImageOps",
        "PIL.ImageEnhance",
        "mss",
        "mss.windows",
        "pytesseract",
        "requests",
        "difflib",
        "statistics",
    ],
    hookspath=[],
    hooksconfig={
        "PyQt6": {
            "qml": False,
            "translations": ["en-US", "en"],
        },
    },
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas",
        "tkinter", "_tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=_APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX corrupts Qt DLLs on foreign machines
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=_APP_NAME,
)
