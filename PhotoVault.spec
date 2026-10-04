# PhotoVault.spec
# Archivo de configuración para PyInstaller.
#
# Genera un ejecutable único "PhotoVault.exe" que incluye Python,
# todas las dependencias y los recursos necesarios.
#
# USO:
#   pyinstaller PhotoVault.spec
#
# El resultado queda en:  dist/PhotoVault.exe

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# ── Archivos fuente del proyecto ──────────────────────────────────────────────
src_files = [
    'main.py',
    'database.py',
    'models.py',
    'services.py',
    'indexer.py',
    'thumbnail_cache.py',
    'backup.py',
    'logging_setup.py',
]

# ── Datos extra a empaquetar (recursos no-.py) ────────────────────────────────
# PyQt6 necesita sus archivos de plugins para funcionar correctamente.
# collect_data_files hace el trabajo pesado de localizarlos.
datas = []
datas += collect_data_files('PyQt6')

# Si pillow-heif está instalado, incluir sus datos también
try:
    datas += collect_data_files('pillow_heif')
except Exception:
    pass

# ── Imports ocultos ───────────────────────────────────────────────────────────
# Módulos que PyInstaller no detecta automáticamente porque se importan
# de forma dinámica (dentro de funciones, try/except, etc.)
hidden_imports = [
    # PyQt6
    'PyQt6.QtSvg',
    'PyQt6.QtSvgWidgets',
    'PyQt6.QtPrintSupport',
    'PyQt6.QtNetwork',
    # Pillow
    'PIL._tkinter_finder',
    'PIL.Image',
    'PIL.ExifTags',
    # OpenCV
    'cv2',
    # SQLite (incluido en stdlib pero a veces necesita ayuda)
    'sqlite3',
    '_sqlite3',
    # pillow-heif (archivos .heic/.heif)
    'pillow_heif',
    # send2trash: elige la implementación según la plataforma en tiempo de ejecución
    'send2trash.win',
    'send2trash.win.legacy',
]

# ── Análisis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=collect_dynamic_libs('cv2'),   # DLLs de OpenCV
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Excluir módulos pesados que no usamos
        'tkinter',
        'matplotlib',
        'numpy.distutils',
        'scipy',
        'pandas',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── Empaquetar archivos Python ─────────────────────────────────────────────────
pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

# ── Ejecutable final (archivo único) ─────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PhotoVault',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # Compresión UPX — reduce tamaño ~30%
    upx_exclude=[
        'vcruntime140.dll', # No comprimir estas DLLs de Windows
        'python3*.dll',
    ],
    runtime_tmpdir=None,
    console=False,          # Sin ventana de consola negra al abrir
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='assets/icon.ico',  # Descomenta si tienes un ícono .ico
)
