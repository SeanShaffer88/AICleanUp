# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the AICleanUp desktop app.

One self-contained executable per platform. The web/ interface, the licence,
and the readme are bundled inside it, so the same output can be zipped as a
portable app (packaging/make_archive.py) or wrapped by the Windows installer
(packaging/aicleanup.iss).
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - PyInstaller injects SPECPATH
ICON = ROOT / "packaging" / "aicleanup.ico"

analysis = Analysis(  # noqa: F821 - PyInstaller injects Analysis
    [str(ROOT / "aicleanup.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "web"), "web"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "README.md"), "."),
    ],
    hiddenimports=["psutil", "send2trash"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821 - PyInstaller injects PYZ

exe = EXE(  # noqa: F821 - PyInstaller injects EXE
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="AICleanUp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The app prints the local URL and logs every request, so a console belongs
    # with it. Closing the console stops the server.
    console=True,
    disable_windowed_traceback=False,
    # A Windows .ico is only valid for the Windows bootloader; macOS and Linux
    # builds keep the default icon rather than fail on a format mismatch.
    icon=str(ICON) if sys.platform.startswith("win") and ICON.is_file() else None,
)
