"""
PhotoVault - services.py
Capa de lógica de negocio entre la UI y la base de datos.
"""

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from send2trash import send2trash

import database as db
import thumbnail_cache
from models import DuplicateGroup, GalleryPage, Photo, SortField, SortOrder, Stats, Tag

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]
StopCheck        = Callable[[], bool]

# ── Galería ───────────────────────────────────────────────────────────────────

def get_gallery_page(
    tag_ids:      list[int]  = None,
    search:       str        = None,
    limit:        int        = 100,
    offset:       int        = 0,
    sort_field:   SortField  = SortField.DATE,
    sort_order:   SortOrder  = SortOrder.DESC,
    folder:       str        = None,
    untagged_only: bool      = False,
) -> GalleryPage:
    hidden = db.get_hidden_tag_ids()
    total  = db.get_photo_count(
        tag_ids=tag_ids or None, hidden_tag_ids=hidden, search=search,
        folder=folder, untagged_only=untagged_only,
    )
    photos = db.get_photos(
        tag_ids=tag_ids or None, hidden_tag_ids=hidden, search=search,
        limit=limit, offset=offset, sort_field=sort_field, sort_order=sort_order,
        folder=folder, untagged_only=untagged_only,
    )
    return GalleryPage(
        photos=photos, total=total, offset=offset, limit=limit,
        sort_field=sort_field, sort_order=sort_order,
    )


def get_photos_for_tagging(
    folder:       str       = None,
    tag_ids:      list[int] = None,
    untagged_only: bool     = False,
) -> list[Photo]:
    """
    Devuelve la lista completa de fotos para el modo etiquetado rápido.
    Sin paginación — carga todos los IDs en memoria para permitir
    navegación libre sin consultas adicionales.
    """
    hidden = db.get_hidden_tag_ids()
    return db.get_photos(
        tag_ids=tag_ids or None,
        hidden_tag_ids=hidden,
        folder=folder,
        untagged_only=untagged_only,
        limit=99_999,
        offset=0,
        sort_field=SortField.DATE,
        sort_order=SortOrder.ASC,
    )


def count_photos_for_tagging(
    folder:        str | None       = None,
    tag_ids:       list[int] | None = None,
    untagged_only: bool             = False,
) -> int:
    """Cuántas fotos devolvería get_photos_for_tagging (sin cargarlas)."""
    return db.get_photo_count(
        tag_ids=tag_ids or None,
        hidden_tag_ids=db.get_hidden_tag_ids(),
        folder=folder,
        untagged_only=untagged_only,
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
    tag = db.get_tag(tag_id)
    assert tag is not None
    return tag


def add_tag_by_id(photo_id: int, tag_id: int) -> None:
    """Asigna una etiqueta existente (sin buscarla por nombre)."""
    db.add_tag_to_photo(photo_id, tag_id)


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


def get_sidebar_tags(include_hidden: bool = False) -> dict[str, list[Tag]]:
    tags   = db.get_all_tags(include_sidebar_hidden=include_hidden)
    groups: dict[str, list[Tag]] = {}
    for tag in tags:
        groups.setdefault(tag.category or "general", []).append(tag)
    return groups


def count_sidebar_hidden_tags() -> int:
    return sum(1 for t in db.get_all_tags() if t.sidebar_hidden)


def create_tag(name: str, category: str = "general", color: str = "#4A9EFF") -> int:
    return db.create_tag(name, category, color)


def update_tag(tag_id: int, name: str, category: str, color: str) -> None:
    """Lanza db.TagNameConflictError si el nombre ya lo usa otra etiqueta."""
    db.update_tag(tag_id, name, category, color)


def get_tag(tag_id: int) -> Optional[Tag]:
    return db.get_tag(tag_id)


def count_photos_with_tag(tag_id: int) -> int:
    return db.count_photos_with_tag(tag_id)


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


# ── Preferencias ──────────────────────────────────────────────────────────────

PAGE_SIZE_DEFAULT = 100
PAGE_SIZE_MIN     = 10
PAGE_SIZE_MAX     = 500


def get_page_size() -> int:
    raw = db.get_setting("page_size")
    try:
        value = int(raw) if raw is not None else PAGE_SIZE_DEFAULT
    except ValueError:
        value = PAGE_SIZE_DEFAULT
    return max(PAGE_SIZE_MIN, min(PAGE_SIZE_MAX, value))


def set_page_size(value: int) -> None:
    db.set_setting("page_size", str(max(PAGE_SIZE_MIN, min(PAGE_SIZE_MAX, int(value)))))


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


def compute_missing_md5s(progress_callback: ProgressCallback | None = None,
                         should_stop: StopCheck | None = None) -> int:
    """
    Calcula el MD5 de las fotos que aún no lo tienen en la DB.
    progress_callback(current, total) se llama por cada archivo procesado.
    should_stop() se consulta antes de cada archivo para poder cancelar.
    Devuelve la cantidad de archivos procesados.
    """
    pending = [p for p in db.get_all_photos_for_duplicates() if not p.md5]
    total   = len(pending)

    for i, photo in enumerate(pending):
        if should_stop and should_stop():
            logger.info("Cálculo de MD5 cancelado en %d/%d", i, total)
            return i
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
    buckets: dict[str, list[Photo]] = defaultdict(list)
    for photo in db.get_all_photos_for_duplicates():
        if photo.md5:
            buckets[photo.md5].append(photo)

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
    Manda el archivo a la Papelera de reciclaje y borra su registro.
    Si el archivo ya no existe, solo borra el registro.
    Devuelve True si tuvo éxito.
    """
    photo = db.get_photo_by_id(photo_id)
    if not photo:
        return False
    if Path(photo.path).exists():
        try:
            send2trash(photo.path)
        except OSError:
            logger.exception("No se pudo mandar a la Papelera: %s", photo.path)
            return False
        logger.info("Enviado a la Papelera: %s", photo.path)
    db.delete_photos([photo_id])
    return True


# ── Mantenimiento ─────────────────────────────────────────────────────────────

# Si en una unidad disponible faltan TODOS los archivos y son al menos esta
# cantidad, se asume que es otro disco con la misma letra (o una carpeta
# desmontada) y no se borra nada de esa unidad.
SUSPICIOUS_ROOT_MIN = 20


@dataclass
class SkippedRoot:
    root:   str   # p. ej. "G:\\"
    count:  int   # registros en esa unidad
    reason: str


@dataclass
class MissingReport:
    """Resultado de buscar registros cuyo archivo ya no existe."""
    missing_ids: list[int]          = field(default_factory=list)
    tagged:      int                = 0     # cuántas de ellas tienen etiquetas
    checked:     int                = 0
    skipped:     list[SkippedRoot]  = field(default_factory=list)
    cancelled:   bool               = False

    @property
    def count(self) -> int:
        return len(self.missing_ids)


def _root_of(path: str) -> str:
    return Path(path).anchor or "(sin unidad)"


def find_missing_files(progress_callback: ProgressCallback | None = None,
                       should_stop: StopCheck | None = None) -> MissingReport:
    """
    Busca registros cuyo archivo no existe. NO borra nada (ver delete_missing).

    Protección contra pérdida de datos: los registros de unidades no
    disponibles (disco desconectado) o donde faltan TODOS los archivos no se
    incluyen; se informan en `skipped`.
    """
    rows = db.get_all_photo_paths()
    by_root: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for pid, path in rows:
        by_root[_root_of(path)].append((pid, path))

    report = MissingReport()
    total  = len(rows)
    done   = 0

    for root, items in by_root.items():
        if not Path(root).exists():
            report.skipped.append(SkippedRoot(root, len(items), "la unidad no está disponible"))
            done += len(items)
            if progress_callback:
                progress_callback(done, total)
            continue

        missing_here: list[int] = []
        for pid, path in items:
            if should_stop and should_stop():
                report.cancelled = True
                report.missing_ids = []
                return report
            if not Path(path).exists():
                missing_here.append(pid)
            done += 1
            if progress_callback and (done % 200 == 0 or done == total):
                progress_callback(done, total)

        if len(missing_here) == len(items) and len(items) >= SUSPICIOUS_ROOT_MIN:
            report.skipped.append(SkippedRoot(
                root, len(items),
                "faltan todos sus archivos (¿es otro disco con la misma letra?)",
            ))
        else:
            report.missing_ids.extend(missing_here)

    report.checked = done
    report.tagged  = db.count_tagged(report.missing_ids)
    logger.info("Búsqueda de faltantes: %d de %d; unidades omitidas: %s",
                report.count, total, [(s.root, s.count) for s in report.skipped])
    return report


def delete_missing(report: MissingReport) -> int:
    """Borra los registros encontrados por find_missing_files()."""
    if report.cancelled:
        return 0
    n = db.delete_photos(report.missing_ids)
    logger.info("Eliminados %d registros de archivos faltantes", n)
    return n


def preview_deindex_folder(folder: str) -> tuple[int, int]:
    """(cantidad de registros, cuántos tienen etiquetas) que borraría deindex_folder."""
    ids = db.get_folder_photo_ids(folder)
    return len(ids), db.count_tagged(ids)


def deindex_folder(folder: str) -> int:
    """Quita los registros de `folder` y sus subcarpetas. No toca el disco."""
    n = db.delete_photos(db.get_folder_photo_ids(folder))
    logger.info("Des-indexada %s: %d registros", folder, n)
    return n


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
