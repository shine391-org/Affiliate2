# PyInstaller spec for Affiliate2 one-click launcher (Windows).
# Single binary, console=False (no console window), tray-free:
#   - resolves release root
#   - spawns release/.venv/Scripts/python.exe -m uvicorn backend.main:app
#   - polls /api/health
#   - opens browser
#   - single-instance sentinel on PORT-1
#   - Windows MessageBoxW on failure

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH is a PyInstaller built-in = directory of this spec file
# (app/desktop/scripts/).  PROJECT_ROOT = app/ = one level up.
PROJECT_ROOT = os.path.normpath(os.path.join(SPECPATH, "..", ".."))
IS_WINDOWS = sys.platform.startswith("win")

datas = []
binaries = []
hiddenimports = []

# Frontend dist — needed so the backend can serve the React SPA.
_frontend_dist = os.path.join(PROJECT_ROOT, "frontend", "dist")
if os.path.isdir(_frontend_dist):
    datas.append((_frontend_dist, "frontend/dist"))
else:
    print(f"WARN: frontend dist not found at {_frontend_dist}")

block_cipher = None

a = Analysis(
    [os.path.join(PROJECT_ROOT, "launcher_affiliate2.py")],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pystray", "pydoc_data", "PIL", "pystray._darwin",
              "pystray._xorg", "pystray._appindicator"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Affiliate2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Affiliate2",
)
