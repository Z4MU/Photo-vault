"""
PhotoVault - ui/images.py
Carga de imágenes grandes para mostrar (escaladas, orientación EXIF).
"""

import logging

from PIL import Image, ImageOps
from PyQt6.QtGui import QImage, QImageReader, QPixmap

import thumbnail_cache

logger = logging.getLogger(__name__)


# ─── Utilidades de imagen ─────────────────────────────────────────────────────


def _read_scaled(path: str, max_side: int) -> tuple[QImage, QImageReader]:
    """
    QImageReader decodifica directo al tamaño pedido (no carga el original
    completo en memoria) y aplica la orientación EXIF.
    """
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > max_side or size.height() > max_side):
        # Con autoTransform la imagen puede rotar 90°: calcular sobre el lado mayor
        scale = max_side / max(size.width(), size.height())
        reader.setScaledSize(size * scale)
    return reader.read(), reader


def pil_to_qimage(img: Image.Image) -> QImage:
    """Imagen de Pillow → QImage independiente (se puede usar fuera del hilo de la UI)."""
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()  # copy(): que no dependa del buffer `data`


def load_full_image(path: str, max_side: int) -> QImage | None:
    """
    Imagen para el visor, a resolución completa (hasta `max_side`). Se puede
    llamar desde un hilo (devuelve QImage). Si Qt no sabe leer el formato
    (HEIC/AVIF), decodifica con Pillow.
    """
    img, reader = _read_scaled(path, max_side)
    if not img.isNull():
        return img
    try:
        with Image.open(path) as src:
            src.draft("RGB", (max_side, max_side))  # JPEG: decodifica ya reducido
            pil = ImageOps.exif_transpose(src)
            pil.thumbnail((max_side, max_side))
            return pil_to_qimage(pil)
    except Exception as e:
        logger.warning("No se pudo cargar la imagen %s: %s / %s", path, reader.errorString(), e)
        return None


def load_preview_pixmap(path: str, max_side: int) -> QPixmap | None:
    """
    Carga una imagen grande para mostrarla, ya escalada y con la orientación
    EXIF aplicada. Si Qt no sabe leer el formato (p. ej. HEIC), usa la
    miniatura de Pillow.
    """
    img, reader = _read_scaled(path, max_side)
    if not img.isNull():
        return QPixmap.fromImage(img)

    jpeg = thumbnail_cache.get_thumbnail(path, size=max_side)
    if jpeg:
        pix = QPixmap()
        if pix.loadFromData(jpeg):
            return pix
    logger.warning("No se pudo cargar la imagen %s: %s", path, reader.errorString())
    return None


def is_animated(path: str) -> bool:
    """GIF/WebP con más de un cuadro (se muestran con QMovie)."""
    reader = QImageReader(path)
    return reader.supportsAnimation() and reader.imageCount() > 1
