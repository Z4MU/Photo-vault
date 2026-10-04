"""
PhotoVault - ui/images.py
Carga de imágenes grandes para mostrar (escaladas, orientación EXIF).
"""

import logging

from PyQt6.QtGui import QImageReader, QPixmap

import thumbnail_cache

logger = logging.getLogger(__name__)


# ─── Utilidades de imagen ─────────────────────────────────────────────────────


def load_preview_pixmap(path: str, max_side: int) -> QPixmap | None:
    """
    Carga una imagen grande para mostrarla, ya escalada y con la orientación
    EXIF aplicada. QImageReader decodifica directo al tamaño pedido (no carga
    el original completo en memoria). Si Qt no sabe leer el formato (p. ej.
    HEIC), usa la miniatura de Pillow.
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > max_side or size.height() > max_side):
        # Con autoTransform la imagen puede rotar 90°: calcular sobre el lado mayor
        scale = max_side / max(size.width(), size.height())
        reader.setScaledSize(size * scale)
    img = reader.read()
    if not img.isNull():
        return QPixmap.fromImage(img)

    jpeg = thumbnail_cache.get_thumbnail(path, size=max_side)
    if jpeg:
        pix = QPixmap()
        if pix.loadFromData(jpeg):
            return pix
    logger.warning("No se pudo cargar la imagen %s: %s", path, reader.errorString())
    return None
