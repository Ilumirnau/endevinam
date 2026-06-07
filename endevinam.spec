# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PC build of Endevina'm.

Build a single windowed exe with:

    pyinstaller endevinam.spec

Output: dist/Endevinam.exe (Windows) / dist/Endevinam (macOS, Linux).

The Kivy helpers below pull in every Kivy/SDL2 dependency automatically, so the
whole build is described here in one file with no extra --add-data or
--hidden-import flags required.
"""
from kivy.tools.packaging.pyinstaller_hooks import (
    get_deps_all,
    hookspath,
    runtime_hooks,
)

deps = get_deps_all()

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=deps["binaries"],
    # Runtime assets bundled into the exe (loaded via BASE_DIR/media, i.e.
    # _MEIPASS/media when frozen). Kept in a media/ folder to keep the repo tidy.
    datas=[
        ("media/endevinam.png", "media"),
        ("media/endevinam_icon.png", "media"),
        ("media/Spotify_Full_Logo_RGB_White.png", "media"),
        ("media/Spotify_Primary_Logo_RGB_White.png", "media"),
    ],
    hiddenimports=deps["hiddenimports"],
    hookspath=hookspath(),
    runtime_hooks=runtime_hooks(),
    excludes=deps["excludes"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Endevinam",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="media/endevinam_icon.ico",
)
