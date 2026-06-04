"""
PhotoVault - services.py
Capa de lógica de negocio entre la UI y la base de datos.

La UI solo llama funciones de este módulo; nunca importa `database` directamente.
Esto hace que:
  - Los tests puedan mockear servicios sin tocar SQLite.
  - La UI no sepa nada del esquema SQL.
  - La lógica compleja (filtros, paginación, etiquetado) viva en un solo lugar.
"""

from pathlib import Path
from typing import Optional

import database as db
from models import GalleryPage, Photo, Tag, Stats


# ── Galería ───────────────────────────────────────────────────────────────────

def get_gallery_page(
    tag_ids:    list[int] = None,
    search:     str       = None,
    limit:      int       = 100,
    offset:     int       = 0,
) -> GalleryPage:
    """
    Devuelve una página de la galería respetando las etiquetas ocultas.
    Encapsula la lógica de obtener hidden_tag_ids para que la UI
    no tenga que llamar dos funciones distintas.
    """
    hidden = db.get_hidden_tag_ids()

    total = db.get_photo_count(
        tag_ids        = tag_ids or None,
        hidden_tag_ids = hidden,
        search         = search,
    )
    photos = db.get_photos(
        tag_ids        = tag_ids or None,
        hidden_tag_ids = hidden,
        search         = search,
        limit          = limit,
        offset         = offset,
    )
    return GalleryPage(photos=photos, total=total, offset=offset, limit=limit)


def get_photo(photo_id: int) -> Optional[Photo]:
    return db.get_photo_by_id(photo_id)


# ── Etiquetas de una foto ─────────────────────────────────────────────────────

def get_photo_tags(photo_id: int) -> list[Tag]:
    return db.get_photo_tags(photo_id)


def add_tag(photo_id: int, tag_name: str) -> Tag:
    """
    Agrega una etiqueta a una foto, creándola si no existe.
    Devuelve el Tag resultante.
    """
    tag_name = tag_name.strip().lower()
    if not tag_name:
        raise ValueError("El nombre de etiqueta no puede estar vacío.")
    tag_id = db.create_tag(tag_name)
    db.add_tag_to_photo(photo_id, tag_id)
    # Recuperar el tag completo para devolverlo
    all_tags = db.get_all_tags()
    tag = next((t for t in all_tags if t.id == tag_id), None)
    return tag


def remove_tag(photo_id: int, tag_id: int):
    db.remove_tag_from_photo(photo_id, tag_id)


# ── Gestión de etiquetas ──────────────────────────────────────────────────────

def get_all_tags(include_sidebar_hidden: bool = True) -> list[Tag]:
    return db.get_all_tags(include_sidebar_hidden=include_sidebar_hidden)


def get_sidebar_tags() -> dict[str, list[Tag]]:
    """
    Devuelve las etiquetas visibles en el sidebar agrupadas por categoría.
    La UI solo necesita iterar este dict, sin conocer la DB.
    """
    tags = db.get_all_tags(include_sidebar_hidden=False)
    groups: dict[str, list[Tag]] = {}
    for tag in tags:
        cat = tag.category or "general"
        groups.setdefault(cat, []).append(tag)
    return groups


def create_tag(name: str, category: str = "general", color: str = "#4A9EFF") -> int:
    return db.create_tag(name, category, color)


def delete_tag(tag_id: int):
    db.delete_tag(tag_id)


def set_tag_hidden(tag_id: int, hidden: bool):
    db.set_tag_hidden(tag_id, hidden)


def set_tag_sidebar_hidden(tag_id: int, hidden: bool):
    db.set_tag_sidebar_hidden(tag_id, hidden)


# ── Gestión de categorías ─────────────────────────────────────────────────────

def get_all_categories() -> list[str]:
    return db.get_all_categories()


def get_tags_by_category(category: str) -> list[Tag]:
    return db.get_tags_by_category(category)


def create_category(name: str) -> bool:
    return db.create_category(name)


def rename_category(old_name: str, new_name: str):
    db.rename_category(old_name, new_name)


def delete_category(name: str):
    db.delete_category(name)


# ── Estadísticas ──────────────────────────────────────────────────────────────

def get_stats() -> Stats:
    return db.get_stats()


# ── Mantenimiento ─────────────────────────────────────────────────────────────

def remove_missing_files() -> int:
    """
    Elimina de la DB los registros de archivos que ya no existen en disco.
    Devuelve la cantidad de registros eliminados.
    """
    conn = db.get_connection()
    rows = conn.execute("SELECT id, path FROM photos").fetchall()
    missing_ids = [row[0] for row in rows if not Path(row[1]).exists()]
    if missing_ids:
        with db.transaction() as c:
            c.executemany(
                "DELETE FROM photos WHERE id = ?",
                [(i,) for i in missing_ids]
            )
    return len(missing_ids)


def deindex_folder(folder: str) -> int:
    """
    Elimina de la DB todos los registros cuya ruta empieza con `folder`.
    Devuelve la cantidad eliminada. Los archivos originales no se tocan.
    """
    with db.transaction() as conn:
        cur = conn.execute(
            "DELETE FROM photos WHERE path LIKE ?", (folder + "%",)
        )
        return cur.rowcount


def get_indexed_folders() -> list[tuple[str, int]]:
    """
    Devuelve lista de (carpeta, cantidad_de_archivos) para el diálogo
    de des-indexado, agrupando por directorio padre de cada archivo.
    """
    from collections import Counter
    conn = db.get_connection()
    rows = conn.execute("SELECT path FROM photos ORDER BY path").fetchall()
    counts: Counter = Counter()
    for row in rows:
        counts[str(Path(row[0]).parent)] += 1
    return sorted(counts.items(), key=lambda x: x[0])
