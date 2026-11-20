# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for ATRO (--onedir default, --onefile via ATRO_ONEFILE=1)."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

spec_dir = Path(SPECPATH).resolve()
project_root = spec_dir.parent
onefile_mode = os.environ.get("ATRO_ONEFILE", "0") == "1"

datas = []
binaries = []
hiddenimports = [
    "google.genai",
    "google.genai.types",
    "pydantic",
    "pydantic_core",
    "dotenv",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "faster_whisper",
    "ctranslate2",
    "tokenizers",
    "huggingface_hub",
    "av",
    "cv2",
    "edge_tts",
    "aiohttp",
]

for pkg in ("faster_whisper", "certifi", "cv2"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

assets_dir = project_root / "atro" / "assets"
if assets_dir.is_dir():
    for asset_file in assets_dir.glob("*.onnx"):
        datas.append((str(asset_file), "atro/assets"))

for pkg in ("ctranslate2", "onnxruntime", "cv2"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

try:
    hiddenimports += [
        m for m in collect_submodules("google.genai") if ".tests" not in m
    ]
except Exception:
    pass

a = Analysis(
    [str(project_root / "run_atro.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "torch", "tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if onefile_mode:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="ATRO",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="ATRO",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="ATRO",
    )
