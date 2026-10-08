# PhotoVault.spec
# Configuración de PyInstaller.
#
# Modo carpeta ("onedir", fase 11): dist/PhotoVault/PhotoVault.exe + _internal/.
# Arranca mucho más rápido que un .exe de un solo archivo (que se descomprimía
# en una carpeta temporal en cada arranque). El instalador (installer/PhotoVault.iss)
# empaqueta esa carpeta.
#
# USO:
#   py -m PyInstaller PhotoVault.spec --noconfirm
#   (o build.bat, que además genera el instalador si está Inno Setup)

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH)
# La versión sale de config.py (una sola fuente)
VERSION = re.search(r'APP_VERSION = "([^"]+)"', (ROOT / "config.py").read_text(encoding="utf-8")).group(1)
_v = tuple(int(x) for x in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))

# ── Datos que no son .py ──────────────────────────────────────────────────────
datas = [
    ("ui/dark.qss", "ui"),  # Hoja de estilos (ui/style.py la lee al importar)
    ("assets/icon.png", "assets"),  # Ícono de las ventanas
]
# Antes: collect_data_files('PyQt6') metía TODO Qt (QML, WebEngine, traducciones…).
# Los hooks de PyInstaller ya incluyen los plugins de los módulos que se usan.
try:
    datas += collect_data_files("pillow_heif")
except Exception:
    pass

# ── Imports que PyInstaller no ve (dinámicos o dentro de try) ─────────────────
hidden_imports = [
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
    # Video integrado en el visor (import con try en ui/video_player.py)
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "cv2",
    "pillow_heif",
    # send2trash elige la implementación según la plataforma al ejecutarse
    "send2trash.win",
    "send2trash.win.legacy",
]

# Partes de Qt y de Python que PhotoVault no usa (achican la carpeta)
excludes = [
    "tkinter",
    "matplotlib",
    "numpy.distutils",
    "scipy",
    "pandas",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets",
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtQuickWidgets",
    "PyQt6.Qt3DCore",
    "PyQt6.Qt3DRender",
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtRemoteObjects",
    "PyQt6.QtSql",
    "PyQt6.QtTest",
    "PyQt6.QtTextToSpeech",
    "PyQt6.QtSpatialAudio",
    "onnxruntime.transformers",
    "onnxruntime.quantization",
    "onnxruntime.tools",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=collect_dynamic_libs("cv2"),
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Traducciones de Qt (≈ 20 MB): la app no las usa (la UI está en español en el código)
a.datas = [d for d in a.datas if "translations" not in d[0].replace("\\", "/").split("/")]
# OpenGL por software (20 MB; una app de widgets no lo necesita) y el lector de PDF de Qt
_UNUSED_BINARIES = {"opengl32sw.dll", "qt6pdf.dll", "qpdf.dll"}
a.binaries = [b for b in a.binaries if Path(b[0]).name.lower() not in _UNUSED_BINARIES]

pyz = PYZ(a.pure, a.zipped_data)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_v, prodvers=_v),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040A04B0",  # español, Unicode
                    [
                        StringStruct("CompanyName", "Z4MU"),
                        StringStruct("FileDescription", "PhotoVault - gestor de fotos local"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "PhotoVault"),
                        StringStruct("OriginalFilename", "PhotoVault.exe"),
                        StringStruct("ProductName", "PhotoVault"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x040A, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # modo carpeta: las DLL van en _internal/
    name="PhotoVault",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX hace la carpeta más chica pero el arranque más lento
    console=False,  # Sin ventana de consola
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PhotoVault",
)
