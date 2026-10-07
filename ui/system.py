"""
PhotoVault - ui/system.py
Acciones con el sistema operativo: mostrar en el Explorador, abrir con la app
predeterminada, copiar al portapapeles.
"""

import logging
import os
import subprocess
import sys

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication

logger = logging.getLogger(__name__)


def reveal_in_explorer(path: str) -> None:
    """Abre la carpeta del archivo con el archivo seleccionado (en Windows)."""
    if sys.platform == "win32" and os.path.exists(path):
        # "/select," y la ruta van separados: así funciona con espacios en la ruta
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])  # noqa: S603, S607
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def open_external(path: str) -> bool:
    """Abre el archivo con la app predeterminada del sistema."""
    ok = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    if not ok:
        logger.warning("No se pudo abrir %s con la app predeterminada", path)
    return ok


def copy_paths(paths: list[str], as_files: bool = False) -> None:
    """
    Copia las rutas como texto (una por línea). Con `as_files`, además como
    archivos: pegar en el Explorador (o en un chat) copia los archivos.
    """
    clipboard = QGuiApplication.clipboard()
    if clipboard is None or not paths:
        return
    md = QMimeData()
    md.setText("\n".join(paths))
    if as_files:
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    clipboard.setMimeData(md)
