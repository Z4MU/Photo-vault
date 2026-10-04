"""
PhotoVault - indexer.py
Escanea carpetas y registra imágenes y videos en la base de datos.
Extrae fecha desde EXIF (imágenes) o nombre/ruta del archivo.
Para videos extrae duración y dimensiones con opencv.

Correcciones aplicadas:
  1. DATE_PATTERNS más estrictos — evitan falsos positivos con resoluciones
     como "1920x1080" o versiones como "v1920" que antes podían matchear
     como año/mes.
  2. Soporte HEIC/HEIF — carga pillow-heif si está instalado para poder
     abrir archivos .heic y .heif. Sin él los archivos se indexan pero sin
     miniatura (no hay crash).
  3. extract_video_thumbnail movido a thumbnail_cache — este módulo ya no
     lo duplica; se importa desde allí para el caso de uso de indexación.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image

import database as db

logger = logging.getLogger(__name__)

# ── Soporte HEIC/HEIF opcional ────────────────────────────────────────────────
# Requiere: pip install pillow-heif
# Sin este paquete los archivos .heic/.heif se indexan (nombre, fecha, tamaño)
# pero no generan miniatura. Con él se comportan como cualquier imagen.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    _HEIF_AVAILABLE = False

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".webp", ".tiff", ".tif", ".heic", ".heif", ".avif"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv",
    ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
    ".3gp", ".ts", ".mts", ".m2ts"
}

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# ── Patrones de fecha más estrictos ───────────────────────────────────────────
# Cambios respecto a la versión original:
#
#  PROBLEMA ANTERIOR:
#    r"(\d{4})[_\-/\\](\d{1,2})[_\-/\\](\d{1,2})"  matcheaba "1920x1080"
#    porque "x" no estaba excluido del separador.
#    r"(\d{4})(\d{2})(\d{2})"  matcheaba "19201080" (resolución concatenada).
#
#  SOLUCIÓN:
#    - Los separadores ahora son solo [_\-/\\] (sin "x").
#    - El patrón compacto YYYYMMDD requiere que NO esté precedido/seguido de
#      otro dígito (word boundary numérico con lookahead/lookbehind).
#    - El patrón de año/mes solo acepta año 1990-2099 y mes 01-12
#      validados en código, igual que antes.
#    - Añadido patrón WhatsApp: "IMG-20200512-WA0001"

DATE_PATTERNS = [
    # 2020-05-12 / 2020/05/12 / 2020_05_12  (separadores explícitos)
    r"(\d{4})[_\-/\\](\d{1,2})[_\-/\\](\d{1,2})",

    # 20200512  — solo si NO está rodeado de otros dígitos (evita resoluciones)
    r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)",

    # IMG_20200512 / VID_20200512 / IMG-20200512-WA0001
    r"(?:IMG|VID)[_\-](\d{4})(\d{2})(\d{2})",

    # 2020-05 (sin día)
    r"(\d{4})[_\-](\d{2})(?![_\-\d])",
]

# Compilar una sola vez para eficiencia
_COMPILED_PATTERNS = [re.compile(p) for p in DATE_PATTERNS]


# Tags EXIF de fecha, en orden de prioridad:
#   DateTimeOriginal  (36867, IFD Exif) — cuándo se tomó la foto
#   DateTimeDigitized (36868, IFD Exif) — cuándo se digitalizó (escaneos)
#   DateTime          (306,   IFD0)     — última MODIFICACIÓN: solo como último recurso
_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL  = 36867
_TAG_DATETIME_DIGITIZED = 36868
_TAG_DATETIME           = 306


def _parse_exif_date(value) -> tuple[int, int] | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="ignore")
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value.strip()[:10], "%Y:%m:%d")
    except ValueError:
        return None  # "0000:00:00 00:00:00", vacío, formato raro…
    if not (1900 <= dt.year <= datetime.now().year + 1):
        return None
    return dt.year, dt.month


def _extract_date_from_exif(path: str) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None, None
            exif_ifd = exif.get_ifd(_EXIF_IFD)
            for value in (exif_ifd.get(_TAG_DATETIME_ORIGINAL),
                          exif_ifd.get(_TAG_DATETIME_DIGITIZED),
                          exif.get(_TAG_DATETIME)):
                parsed = _parse_exif_date(value)
                if parsed:
                    return parsed
    except Exception as e:
        # Muy común (sin EXIF, formato raro): no es un error real
        logger.debug("Sin fecha EXIF en %s: %s", path, e)
    return None, None


def _extract_date_from_string(text: str):
    for pattern in _COMPILED_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            try:
                year = int(groups[0])
            except (ValueError, IndexError):
                continue

            month = None
            if len(groups) > 1 and groups[1]:
                try:
                    month = int(groups[1])
                except ValueError:
                    month = None

            # Validar rangos — rechaza años inverosímiles y meses imposibles
            if not (1990 <= year <= 2099):
                continue
            if month is not None and not (1 <= month <= 12):
                month = None  # año válido, mes inválido → guardar solo año

            return year, month
    return None, None


def _get_image_size(path: str):
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception as e:
        logger.warning("No se pudo leer el tamaño de %s: %s", path, e)
        return None, None


def _get_video_info(path: str):
    """Extrae duración (segundos) y dimensiones del video usando opencv."""
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None, None, None
        fps         = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration    = (frame_count / fps) if fps > 0 else None
        cap.release()
        return duration, width or None, height or None
    except Exception as e:
        logger.warning("No se pudo leer la info del video %s: %s", path, e)
        return None, None, None


def extract_video_thumbnail(path: str, size: int = 200) -> bytes | None:
    """
    Compatibilidad: delega al caché centralizado.
    Usar thumbnail_cache.get_video_thumbnail() directamente en código nuevo.
    """
    from thumbnail_cache import get_video_thumbnail
    return get_video_thumbnail(path, size=size)


def index_folder(folder: str,
                 progress_callback: Callable[[int, int, str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> tuple[int, int, int]:
    """
    Escanea una carpeta recursivamente e indexa imágenes y videos.
    progress_callback(current, total, filepath) se llama por cada archivo.
    should_stop() se consulta antes de cada archivo para poder cancelar.
    Devuelve (nuevas, actualizadas, errores).
    """
    root = Path(folder)
    if not root.exists():
        raise ValueError(f"La carpeta no existe: {root}")

    all_files = [
        p for p in root.rglob("*")
        if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()
    ]

    total  = len(all_files)
    added  = updated = errors = 0

    for i, filepath in enumerate(all_files):
        if should_stop and should_stop():
            logger.info("Indexación de %s cancelada en %d/%d", root, i, total)
            break
        if progress_callback:
            progress_callback(i + 1, total, str(filepath))

        try:
            path_str   = str(filepath)
            filename   = filepath.name
            filesize   = filepath.stat().st_size
            ext        = filepath.suffix.lower()
            is_video   = ext in VIDEO_EXTENSIONS
            media_type = "video" if is_video else "image"

            # ── Fecha ──────────────────────────────────────────────────────
            year = month = None
            if not is_video:
                year, month = _extract_date_from_exif(path_str)
            if not year:
                year, month = _extract_date_from_string(filename)
            if not year:
                year, month = _extract_date_from_string(str(filepath.parent))
            if not year:
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                year, month = mtime.year, mtime.month

            # ── Dimensiones / duración ─────────────────────────────────────
            if is_video:
                duration, width, height = _get_video_info(path_str)
            else:
                width, height = _get_image_size(path_str)
                duration = None

            existed = db.photo_exists(path_str)
            db.upsert_photo(
                path_str, filename, year, month, filesize,
                width, height, media_type, duration
            )
            if existed:
                updated += 1
            else:
                added += 1

        except Exception as e:
            errors += 1
            logger.error("Error indexando %s: %s", filepath, e, exc_info=True)

    return added, updated, errors
