"""
PhotoVault - services.py
Capa de lógica de negocio entre la UI y la base de datos.
"""

import json
import hashlib
from pathlib import Path
from typing import Optional

import database as db
import thumbnail_cache
from models import GalleryPage, Photo, Tag, Stats, SortField, SortOrder, DuplicateGroup


# ── Galería ───────────────────────────────────────────────────────────────────

def get_gallery_page(
    tag_ids:    list[int]  = None,
    search:     str        = None,
    limit:      int        = 100,
    offset:     int        = 0,
    sort_field: SortField  = SortField.DATE,
    sort_order: SortOrder  = SortOrder.DESC,
) -> GalleryPage:
    hidden = db.get_hidden_tag_ids()
    total  = db.get_photo_count(
        tag_ids=tag_ids or None, hidden_tag_ids=hidden, search=search
    )
    photos = db.get_photos(
        tag_ids=tag_ids or None, hidden_tag_ids=hidden, search=search,
        limit=limit, offset=offset, sort_field=sort_field, sort_order=sort_order,
    )
    return GalleryPage(
        photos=photos, total=total, offset=offset, limit=limit,
        sort_field=sort_field, sort_order=sort_order,
    )


def get_photo(photo_id: int) -> Optional[Photo]:
    return db.get_photo_by_id(photo_id)


# ── Etiquetas de una foto ─────────────────────────────────────────────────────

def get_photo_tags(photo_id: int) -> list[Tag]:
    return db.get_photo_tags(photo_id)


def add_tag(photo_id: int, tag_name: str) -> Tag:
    tag_name = tag_name.strip().lower()
    if not tag_name:
        raise ValueError("El nombre de etiqueta no puede estar vacío.")
    tag_id = db.create_tag(tag_name)
    db.add_tag_to_photo(photo_id, tag_id)
    return next(t for t in db.get_all_tags() if t.id == tag_id)


def remove_tag(photo_id: int, tag_id: int):
    db.remove_tag_from_photo(photo_id, tag_id)


# ── Etiquetado en lote ────────────────────────────────────────────────────────

def bulk_add_tag(photo_ids: list[int], tag_name: str) -> int:
    """
    Agrega una etiqueta a varias fotos a la vez.
    Crea la etiqueta si no existe. Devuelve cuántas fotos fueron afectadas.
    """
    if not photo_ids or not tag_name.strip():
        return 0
    tag_id = db.create_tag(tag_name.strip().lower())
    db.add_tag_to_photos(photo_ids, tag_id)
    return len(photo_ids)


def bulk_remove_tag(photo_ids: list[int], tag_id: int) -> int:
    """Quita una etiqueta de varias fotos. Devuelve cuántas fotos fueron afectadas."""
    if not photo_ids:
        return 0
    db.remove_tag_from_photos(photo_ids, tag_id)
    return len(photo_ids)


# ── Gestión de etiquetas ──────────────────────────────────────────────────────

def get_all_tags(include_sidebar_hidden: bool = True) -> list[Tag]:
    return db.get_all_tags(include_sidebar_hidden=include_sidebar_hidden)


def get_sidebar_tags() -> dict[str, list[Tag]]:
    tags   = db.get_all_tags(include_sidebar_hidden=False)
    groups: dict[str, list[Tag]] = {}
    for tag in tags:
        groups.setdefault(tag.category or "general", []).append(tag)
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


# ── Exportar / importar etiquetas ─────────────────────────────────────────────

def export_tags(path: str) -> int:
    """
    Exporta todas las etiquetas (con categoría y color) a un JSON.
    Devuelve la cantidad exportada.
    """
    tags = db.get_all_tags()
    data = {
        "version": 1,
        "tags": [
            {"name": t.name, "category": t.category, "color": t.color,
             "hidden": t.hidden, "sidebar_hidden": t.sidebar_hidden}
            for t in tags
        ],
        "categories": db.get_all_categories(),
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(tags)


def import_tags(path: str) -> tuple[int, int]:
    """
    Importa etiquetas desde un JSON exportado por export_tags.
    Devuelve (creadas, omitidas_ya_existian).
    """
    raw  = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)

    if data.get("version") != 1:
        raise ValueError("Formato de archivo no reconocido.")

    created = skipped = 0

    for cat in data.get("categories", []):
        db.create_category(cat)

    existing_names = {t.name for t in db.get_all_tags()}
    for tag_data in data.get("tags", []):
        name = tag_data.get("name", "").strip().lower()
        if not name:
            continue
        if name in existing_names:
            skipped += 1
            continue
        db.create_tag(name, tag_data.get("category", "general"), tag_data.get("color", "#4A9EFF"))
        created += 1

    return created, skipped


# ── Duplicados ────────────────────────────────────────────────────────────────

def _md5_of_file(path: str, chunk: int = 65536) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except OSError:
        return None


def compute_missing_md5s(progress_callback=None) -> int:
    """
    Calcula el MD5 de las fotos que aún no lo tienen en la DB.
    progress_callback(current, total) se llama por cada archivo procesado.
    Devuelve la cantidad de hashes calculados.
    """
    rows = db.get_all_photos_for_duplicates()
    pending = [(p, md5) for p, md5 in rows if not md5]
    total   = len(pending)

    for i, (photo, _) in enumerate(pending):
        if progress_callback:
            progress_callback(i + 1, total)
        md5 = _md5_of_file(photo.path)
        if md5:
            db.update_photo_md5(photo.id, md5)

    return total


def get_duplicate_groups() -> list[DuplicateGroup]:
    """
    Devuelve grupos de fotos que comparten el mismo MD5.
    Solo incluye grupos con 2+ fotos. Requiere que los MD5s estén calculados.
    """
    rows = db.get_all_photos_for_duplicates()

    from collections import defaultdict
    buckets: dict[str, list[Photo]] = defaultdict(list)
    for photo, md5 in rows:
        if md5:
            buckets[md5].append(photo)

    groups = [
        DuplicateGroup(md5=md5, photos=photos)
        for md5, photos in buckets.items()
        if len(photos) >= 2
    ]
    # Ordenar: grupos con más copias primero
    groups.sort(key=lambda g: g.size, reverse=True)
    return groups


def delete_photo_file(photo_id: int) -> bool:
    """
    Elimina el archivo físico y el registro de la DB.
    Devuelve True si tuvo éxito.
    """
    photo = db.get_photo_by_id(photo_id)
    if not photo:
        return False
    try:
        Path(photo.path).unlink(missing_ok=True)
    except OSError:
        return False
    with db.transaction() as conn:
        conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    return True


# ── Mantenimiento ─────────────────────────────────────────────────────────────

def remove_missing_files() -> int:
    conn = db.get_connection()
    rows = conn.execute("SELECT id, path FROM photos").fetchall()
    missing_ids = [row[0] for row in rows if not Path(row[1]).exists()]
    if missing_ids:
        with db.transaction() as c:
            c.executemany("DELETE FROM photos WHERE id = ?", [(i,) for i in missing_ids])
    return len(missing_ids)


def deindex_folder(folder: str) -> int:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM photos WHERE path LIKE ?", (folder + "%",))
        return cur.rowcount


def get_indexed_folders() -> list[tuple[str, int]]:
    from collections import Counter
    conn   = db.get_connection()
    rows   = conn.execute("SELECT path FROM photos ORDER BY path").fetchall()
    counts: Counter = Counter(str(Path(r[0]).parent) for r in rows)
    return sorted(counts.items(), key=lambda x: x[0])


def purge_cache_orphans() -> int:
    """
    Elimina del caché de miniaturas los archivos que ya no tienen registro en la DB.
    Devuelve la cantidad de archivos eliminados.
    """
    conn  = db.get_connection()
    paths = [r[0] for r in conn.execute("SELECT path FROM photos").fetchall()]
    return thumbnail_cache.purge_orphans(paths)
