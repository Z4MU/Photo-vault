"""
PhotoVault - ui/style.py
Hoja de estilos (QSS) del tema oscuro.

El QSS vive en ui/dark.qss para poder editarlo sin tocar código. PyInstaller
lo empaqueta (ver `datas` en PhotoVault.spec).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

QSS_PATH = Path(__file__).with_name("dark.qss")


def _load() -> str:
    try:
        return QSS_PATH.read_text(encoding="utf-8")
    except OSError:
        # Sin estilos la app se ve fea pero funciona: no impedir que abra
        logger.exception("No se pudo leer la hoja de estilos %s", QSS_PATH)
        return ""


DARK_STYLE = _load()
