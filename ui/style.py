"""
PhotoVault - ui/style.py
Hoja de estilos (QSS) del tema oscuro.

El QSS vive en ui/dark.qss para poder editarlo sin tocar código. PyInstaller
lo empaqueta (ver `datas` en PhotoVault.spec).
"""

from pathlib import Path

QSS_PATH = Path(__file__).with_name("dark.qss")

DARK_STYLE = QSS_PATH.read_text(encoding="utf-8")
