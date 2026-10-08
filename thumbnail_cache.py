"""
PhotoVault - thumbnail_cache.py
Caché persistente de miniaturas en disco.

Guarda cada miniatura como JPEG en ~/.photovault/thumbs/<2 chars>/<nombre>.jpg

Nombre del archivo:  [v_]<sha1(ruta::mtime::versión)>_<tamaño>.jpg
  - mtime: si el original cambia, la miniatura se regenera sola.
  - tamaño: la de 200 px y la de 480 px de la misma foto son archivos distintos.
  - versión (THUMB_VERSION): subirla invalida todo el caché cuando cambia la
    forma de generar miniaturas (p. ej. al empezar a aplicar la orientación EXIF).
  - "v_": prefijo para videos.

Fotos ocultas (fase 9): `get_photo_thumbnail` de una foto con `hidden` nunca
escribe en CACHE_DIR. Con el contenido desbloqueado (privacy pone un "codec"
con set_private_codec) la miniatura va cifrada a PRIVATE_DIR con un nombre
HMAC; bloqueado, no se genera ni se lee (devuelve None).

Uso:
    from thumbnail_cache import get_thumbnail   # devuelve bytes o None
"""

import hashlib
import io
import logging
import os
import shutil
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps

import config

logger = logging.getLogger(__name__)

# Soporte HEIC/HEIF (fotos de iPhone). Se registra aquí también y no solo en
# indexer.py: este módulo no debe depender de que otro se haya importado antes.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    logger.warning("pillow-heif no está instalado: los .heic no tendrán miniatura")

# Directorio donde se guardan las miniaturas
CACHE_DIR = config.THUMBS_DIR
PRIVATE_DIR = config.PRIVATE_THUMBS_DIR
# Los tamaños que usa la app (para borrar todas las de una foto)
THUMB_SIZES = (config.THUMB_SIZE_SMALL, config.THUMB_SIZE_GALLERY, config.THUMB_SIZE_LARGE)
THUMB_SIZE = config.THUMB_SIZE_GALLERY  # píxeles (lado máximo)
THUMB_QUALITY = 85  # calidad JPEG
THUMB_VERSION = 2  # v2: orientación EXIF + tamaño en el nombre

_VIDEO_PREFIX = "v_"
_PRIVATE_EXT = ".pvt"


class PrivateCodec(Protocol):
    def name(self, text: str) -> str: ...
    def encrypt(self, data: bytes) -> bytes: ...
    def decrypt(self, blob: bytes) -> bytes | None: ...


# None = contenido oculto bloqueado (lo pone privacy)
_private_codec: PrivateCodec | None = None


def set_private_codec(codec: PrivateCodec | None) -> None:
    global _private_codec
    _private_codec = codec


def has_private_codec() -> bool:
    return _private_codec is not None


# Rutas cuyo fallo ya se registró en esta sesión (evita repetir el mismo
# warning cada vez que se vuelve a mostrar la página)
_logged_failures: set[str] = set()


def _log_failure(filepath: str, what: str, exc: BaseException) -> None:
    if filepath not in _logged_failures:
        _logged_failures.add(filepath)
        logger.warning("No se pudo %s %s: %s", what, filepath, exc)


def _source_hash(filepath: str, mtime: float | None = None) -> str:
    """
    Hash que identifica la versión del archivo original.
    `mtime`: el guardado en la DB al indexar. Pasarlo evita consultar el disco
    de la colección por cada miniatura (en un disco USB eso era casi todo el
    tiempo de una miniatura en caché). Si es None, se consulta el archivo.
    """
    raw = f"{filepath}::{_mtime_str(filepath, mtime)}::{THUMB_VERSION}".encode()
    return hashlib.sha1(raw).hexdigest()


def _mtime_str(filepath: str, mtime: float | None) -> str:
    if mtime is None:
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            mtime = 0
    return "0" if not mtime else str(mtime)


def _cache_name(filepath: str, size: int, video: bool, mtime: float | None = None) -> str:
    prefix = _VIDEO_PREFIX if video else ""
    return f"{prefix}{_source_hash(filepath, mtime)}_{size}.jpg"


def _cache_path(name: str) -> Path:
    # Subdirectorios por los 2 primeros caracteres del hash (sin el prefijo de
    # video) para no saturar un solo directorio
    h = name.removeprefix(_VIDEO_PREFIX)
    return CACHE_DIR / h[:2] / name


def is_cached(filepath: str, size: int = THUMB_SIZE, video: bool = False, mtime: float | None = None) -> bool:
    return _cache_path(_cache_name(filepath, size, video, mtime)).exists()


def _get_cached(filepath: str, size: int, video: bool, mtime: float | None = None) -> bytes | None:
    path = _cache_path(_cache_name(filepath, size, video, mtime))

    # ── Caché hit ──────────────────────────────────────────────────────────
    if path.exists():
        try:
            return path.read_bytes()
        except OSError as e:
            # Si falla la lectura, regeneramos
            logger.warning("No se pudo leer la miniatura en caché %s: %s", path, e)

    # ── Caché miss: generar miniatura ──────────────────────────────────────
    data = _generate_video(filepath, size) if video else _generate(filepath, size)
    if data:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as e:
            # Si no se puede guardar, igual devolvemos los bytes
            logger.warning("No se pudo guardar la miniatura %s: %s", path, e)
    return data


def get_thumbnail(filepath: str, size: int = THUMB_SIZE, mtime: float | None = None) -> bytes | None:
    """
    Devuelve la miniatura en bytes (JPEG), con la orientación EXIF aplicada.
    - Si existe en caché y el archivo no cambió, la lee del disco.
    - Si no existe o el original cambió, la genera y la guarda.
    - Devuelve None si no se puede procesar el archivo.
    `mtime`: pasar `photo.mtime` (de la DB) siempre que se tenga.
    """
    return _get_cached(filepath, size, video=False, mtime=mtime)


def get_video_thumbnail(filepath: str, size: int = THUMB_SIZE, mtime: float | None = None) -> bytes | None:
    """Igual que get_thumbnail pero para videos (usa opencv)."""
    return _get_cached(filepath, size, video=True, mtime=mtime)


def get_photo_thumbnail(photo, size: int = THUMB_SIZE) -> bytes | None:
    """
    Atajo para un models.Photo: elige imagen/video y usa su mtime de la DB.
    Una foto oculta va al caché cifrado (None si está bloqueado).
    """
    if getattr(photo, "hidden", False):
        return _get_private(photo.path, size, photo.is_video, photo.mtime)
    getter = get_video_thumbnail if photo.is_video else get_thumbnail
    return getter(photo.path, size=size, mtime=photo.mtime)


def is_photo_cached(photo, size: int = THUMB_SIZE) -> bool:
    """¿Ya está la miniatura de esta foto? (oculta y bloqueado: False)"""
    if getattr(photo, "hidden", False):
        path = _private_path(photo.path, size, photo.is_video, photo.mtime)
        return path is not None and path.exists()
    return is_cached(photo.path, size, photo.is_video, photo.mtime)


# ── Caché privado (fotos ocultas) ─────────────────────────────────────────────


def _private_name(codec: PrivateCodec, filepath: str, size: int, video: bool, mtime: float | None) -> str:
    return codec.name(f"{filepath}::{_mtime_str(filepath, mtime)}::{THUMB_VERSION}::{int(video)}::{size}")


def _private_path(filepath: str, size: int, video: bool, mtime: float | None) -> Path | None:
    codec = _private_codec
    if codec is None:
        return None
    name = _private_name(codec, filepath, size, video, mtime)
    return PRIVATE_DIR / name[:2] / f"{name}{_PRIVATE_EXT}"


def _get_private(filepath: str, size: int, video: bool, mtime: float | None) -> bytes | None:
    codec = _private_codec
    path = _private_path(filepath, size, video, mtime)
    if codec is None or path is None:
        return None  # bloqueado: ni leer ni generar
    if path.exists():
        try:
            data = codec.decrypt(path.read_bytes())
            if data is not None:
                return data
            logger.warning("Miniatura privada ilegible, se regenera: %s", path.name)
        except OSError as e:
            logger.warning("No se pudo leer la miniatura privada %s: %s", path.name, e)
    data = _generate_video(filepath, size) if video else _generate(filepath, size)
    if data:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(codec.encrypt(data))
        except OSError as e:
            logger.warning("No se pudo guardar la miniatura privada %s: %s", path.name, e)
    return data


def remove_plain(photos) -> int:
    """
    Borra las miniaturas SIN cifrar de estas fotos (todas los tamaños): para
    las que acaban de quedar ocultas. Devuelve cuántos archivos borró.
    """
    removed = 0
    for photo in photos:
        for size in THUMB_SIZES:
            path = _cache_path(_cache_name(photo.path, size, photo.is_video, photo.mtime))
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning("No se pudo borrar la miniatura %s: %s", path, e)
    return removed


def purge_private_orphans(known: list[tuple[str, float | None, bool]]) -> int:
    """
    Borra del caché privado lo que no es de una foto oculta actual
    (`known`: [(ruta, mtime, es_video)]). Necesita estar desbloqueado (si no, 0).
    """
    codec = _private_codec
    if codec is None or not PRIVATE_DIR.exists():
        return 0
    valid = {_private_name(codec, p, size, v, m) for p, m, v in known for size in THUMB_SIZES}
    removed = 0
    for thumb in PRIVATE_DIR.rglob(f"*{_PRIVATE_EXT}"):
        if thumb.stem in valid:
            continue
        try:
            thumb.unlink()
            removed += 1
        except OSError as e:
            logger.warning("No se pudo eliminar la miniatura privada %s: %s", thumb.name, e)
    return removed


def clear_private_cache() -> None:
    if PRIVATE_DIR.exists():
        shutil.rmtree(PRIVATE_DIR, ignore_errors=True)


def _to_jpeg(img: Image.Image, size: int) -> bytes:
    # Convertir a RGB para evitar problemas con RGBA/P al guardar JPEG
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def _generate(filepath: str, size: int) -> bytes | None:
    """Genera miniatura de imagen con Pillow."""
    try:
        with Image.open(filepath) as img:
            # Fotos de celular: los píxeles vienen "acostados" y el EXIF
            # dice cómo rotarlos. Sin esto la miniatura sale girada.
            rotated = ImageOps.exif_transpose(img)
            return _to_jpeg(rotated if rotated is not None else img, size)
    except Exception as e:
        _log_failure(filepath, "generar la miniatura de", e)
        return None


def _generate_video(filepath: str, size: int) -> bytes | None:
    """Genera miniatura de video con opencv."""
    try:
        import cv2

        cap = cv2.VideoCapture(filepath)
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
        return _to_jpeg(Image.fromarray(frame_rgb), size)
    except Exception as e:
        _log_failure(filepath, "generar la miniatura del video", e)
        return None


def purge_orphans(known: list[tuple[str, float | None]]) -> int:
    """
    Elimina del caché las miniaturas que no corresponden a la versión actual
    de un archivo indexado (archivo des-indexado, modificado, o miniatura de
    un formato de caché anterior).
    `known`: [(ruta, mtime de la DB)]. Con mtime None se consulta el archivo.
    Devuelve la cantidad de archivos eliminados.
    """
    if not CACHE_DIR.exists():
        return 0

    valid_hashes = {_source_hash(p, m) for p, m in known}

    removed = 0
    for thumb in CACHE_DIR.rglob("*.jpg"):
        stem = thumb.stem.removeprefix(_VIDEO_PREFIX)
        h, sep, _size = stem.partition("_")
        if sep and h in valid_hashes:
            continue
        try:
            thumb.unlink()
            removed += 1
        except OSError as e:
            logger.warning("No se pudo eliminar la miniatura huérfana %s: %s", thumb, e)
    return removed


def clear_cache() -> None:
    """Borra todas las miniaturas (se regeneran solas). Lento con muchas: llamar desde un hilo."""
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
    clear_private_cache()


def cache_size_mb() -> float:
    """Devuelve el tamaño total del caché en MB."""
    total = 0
    if CACHE_DIR.exists():
        total += sum(f.stat().st_size for f in CACHE_DIR.rglob("*.jpg") if f.is_file())
    if PRIVATE_DIR.exists():
        total += sum(f.stat().st_size for f in PRIVATE_DIR.rglob(f"*{_PRIVATE_EXT}") if f.is_file())
    return round(total / 1_048_576, 2)
