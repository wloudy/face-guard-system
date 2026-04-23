# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, get_package_paths
import os
import glob

block_cipher = None

# 1) face_recognition models (*.dat)
# collect_data_files иногда не захватывает *.dat из wheel, поэтому добавляем их явно.
fr_models_datas = collect_data_files("face_recognition_models", include_py_files=False)
fr_models_extra = []
try:
    _pkg_base, _pkg_dir = get_package_paths("face_recognition_models")
    _models_dir = os.path.join(_pkg_dir, "models")
    if os.path.isdir(_models_dir):
        _dat_files = glob.glob(os.path.join(_models_dir, "*.dat"))
        fr_models_extra = [(_f, "face_recognition_models/models") for _f in _dat_files]
except Exception:
    fr_models_extra = []

# 2) Flask templates
app_datas = [
    ("templates", "templates"),
]

hiddenimports = []
hiddenimports += collect_submodules("face_recognition")
hiddenimports += collect_submodules("face_recognition_models")
hiddenimports += collect_submodules("flask_socketio")
hiddenimports += collect_submodules("socketio")
hiddenimports += collect_submodules("engineio")
# Engine.IO async driver for Flask-SocketIO async_mode='threading'
hiddenimports += ["engineio.async_drivers.threading"]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=fr_models_datas + fr_models_extra + app_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FaceGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
