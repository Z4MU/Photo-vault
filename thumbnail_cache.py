"""
PhotoVault - thumbnail_cache.py
Caché persistente de miniaturas en disco.

Guarda cada miniatura como JPEG en ~/.photovault/thumbs/<hash>.jpg
El hash se calcula a partir de la ruta del archivo + su fecha de modificación,
por lo que si el archivo original cambia, la miniatura se regenera automáticamente.

Uso:
    from thumbnail_cache import get_thumbnail   # devuelve bytes o None
"""

import hashlib
import io
import os
from pathlib import Path

from PIL import Image

# Directorio donde se guardan las miniaturas
CACHE_DIR = Path.home() / ".photovault" / "thumbs"
THUMB_SIZE = 200          # píxeles (lado máximo)
THUMB_QUALITY = 85        # calidad JPEG


def _cache_key(filepath: str) -> str:
    """
    Genera un nombre de archivo único para la miniatura.
    Incluye la ruta y el mtime del archivo para invalidar automáticamente
    cuando el original cambia.
    """
    try:
        mtime = str(os.path.getmtime(filepath))
    except OSError:
        mtime = "0"
    raw = f"{filepath}::{mtime}".encode()
    return hashlib.sha1(raw).hexdigest() + ".jpg"


def _cache_path(key: str) -> Path:
    # Subdirectorios de 2 caracteres para no saturar un solo directorio
    return CACHE_DIR / key[:2] / key


def _ensure_cache_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def get_thumbnail(filepath: str, size: int = THUMB_SIZE) -> bytes | None:
    """
    Devuelve la miniatura en bytes (JPEG).
    - Si existe en caché y el archivo no cambió, la lee del disco.
    - Si no existe o el original cambió, la genera y la guarda.
    - Devuelve None si no se puede procesar el archivo.
    """
    key  = _cache_key(filepath)
    path = _cache_path(key)

    # ── Caché hit ──────────────────────────────────────────────────────────
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            pass  # Si falla la lectura, regeneramos

    # ── Caché miss: generar miniatura ──────────────────────────────────────
    data = _generate(filepath, size)
    if data:
        try:
            _ensure_cache_dir(path)
            path.write_bytes(data)
        except OSError:
            pass  # Si no se puede guardar, igual devolvemos los bytes
    return data


def get_video_thumbnail(filepath: str, size: int = THUMB_SIZE) -> bytes | None:
    """
    Igual que get_thumbnail pero para videos (usa opencv).
    """
    key  = _cache_key(filepath)
    # Prefijo distinto para no colisionar con imágenes que tengan mismo hash
    key  = "v_" + key
    path = _cache_path(key)

    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            pass

    data = _generate_video(filepath, size)
    if data:
        try:
            _ensure_cache_dir(path)
            path.write_bytes(data)
        except OSError:
            pass
    return data


def _generate(filepath: str, size: int) -> bytes | None:
    """Genera miniatura de imagen con Pillow."""
    try:
        with Image.open(filepath) as img:
            # Convertir a RGB para evitar problemas con RGBA/P al guardar JPEG
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception:
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
        img = Image.fromarray(frame_rgb)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def purge_orphans(known_paths: list[str]) -> int:
    """
    Elimina del caché las miniaturas cuyos archivos originales ya no existen.
    Devuelve la cantidad de archivos eliminados.
    Útil para llamar periódicamente o desde la opción de mantenimiento.
    """
    if not CACHE_DIR.exists():
        return 0

    # Construir set de claves válidas
    valid_keys = set()
    for p in known_paths:
        valid_keys.add(_cache_key(p))
        valid_keys.add("v_" + _cache_key(p))

    removed = 0
    for thumb in CACHE_DIR.rglob("*.jpg"):
        if thumb.name not in valid_keys:
            try:
                thumb.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def cache_size_mb() -> float:
    """Devuelve el tamaño total del caché en MB."""
    if not CACHE_DIR.exists():
        return 0.0
    total = sum(f.stat().st_size for f in CACHE_DIR.rglob("*.jpg") if f.is_file())
    return round(total / 1_048_576, 2)
