"""
PhotoVault - indexer.py
Escanea carpetas y registra imágenes y videos en la base de datos.
Extrae fecha desde EXIF (imágenes) o nombre/ruta del archivo.
Para videos extrae duración y un frame de miniatura con opencv.
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Callable

from PIL import Image
from PIL.ExifTags import TAGS

import database as db

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

DATE_PATTERNS = [
    r"(\d{4})[_\-/\\](\d{1,2})[_\-/\\](\d{1,2})",  # 2020-05-12
    r"(\d{4})(\d{2})(\d{2})",                          # 20200512
    r"IMG[_\-](\d{4})(\d{2})(\d{2})",                 # IMG_20200512
    r"VID[_\-](\d{4})(\d{2})(\d{2})",                 # VID_20200512
    r"(\d{4})[_\-](\d{2})",                            # 2020-05 (sin día)
]


def _extract_date_from_exif(path: str):
    try:
        with Image.open(path) as img:
            exif_data = img._getexif()
            if not exif_data:
                return None, None
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag in ("DateTime", "DateTimeOriginal", "DateTimeDigitized"):
                    dt = datetime.strptime(value[:10], "%Y:%m:%d")
                    return dt.year, dt.month
    except Exception:
        pass
    return None, None


def _extract_date_from_string(text: str):
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            groups = m.groups()
            year = int(groups[0])
            month = int(groups[1]) if len(groups) > 1 and groups[1] else None
            if 1990 <= year <= 2100:
                if month and 1 <= month <= 12:
                    return year, month
                return year, None
    return None, None


def _get_image_size(path: str):
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _get_video_info(path: str):
    """
    Extrae duración (segundos) y dimensiones del video usando opencv.
    Devuelve (duration, width, height).
    """
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
    except Exception:
        return None, None, None


def extract_video_thumbnail(path: str, size: int = 200) -> bytes | None:
    """
    Extrae un frame del video y lo devuelve como JPEG en bytes.
    Salta al 5% del video para evitar pantallas negras iniciales.
    """
    try:
        import cv2
        import io
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if total > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * 0.05))
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame_rgb)
        img.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


def index_folder(folder: str, progress_callback: Callable[[int, int, str], None] = None):
    """
    Escanea una carpeta recursivamente e indexa imágenes y videos.
    progress_callback(current, total, filepath) se llama por cada archivo.
    Devuelve (added, skipped, errors).
    """
    folder = Path(folder)
    if not folder.exists():
        raise ValueError(f"La carpeta no existe: {folder}")

    all_files = [
        p for p in folder.rglob("*")
        if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()
    ]

    total  = len(all_files)
    added  = skipped = errors = 0

    for i, filepath in enumerate(all_files):
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

            db.upsert_photo(
                path_str, filename, year, month, filesize,
                width, height, media_type, duration
            )
            added += 1

        except Exception as e:
            errors += 1
            print(f"[ERROR] {filepath}: {e}")

    return added, skipped, errors
