"""
PhotoVault - services.py
Capa de lógica de negocio entre la UI y la base de datos.
"""

import hashlib
import json
import logging
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

import database as db
import indexer
import thumbnail_cache
import xmp_sidecar
from models import (
    DuplicateGroup,
    GalleryPage,
    Photo,
    SavedSearch,
    SortField,
    SortOrder,
    Stats,
    Tag,
    TrashBatch,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]
StopCheck = Callable[[], bool]

# ── Galería ───────────────────────────────────────────────────────────────────


def get_gallery_photos(
    tag_ids: list[int] | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    sort_field: SortField = SortField.DATE,
    sort_order: SortOrder = SortOrder.DESC,
    folder: str | None = None,
    untagged_only: bool = False,
    hidden_tag_ids: set[int] | None = None,
) -> list[Photo]:
    """Solo las fotos de una página, sin contar el total (p. ej. para precargar la siguiente)."""
    return db.get_photos(
        tag_ids=tag_ids or None,
        hidden_tag_ids=hidden_tag_ids if hidden_tag_ids is not None else db.get_hidden_tag_ids(),
        search=search,
        limit=limit,
        offset=offset,
        sort_field=sort_field,
        sort_order=sort_order,
        folder=folder,
        untagged_only=untagged_only,
    )


def get_gallery_page(
    tag_ids: list[int] | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    sort_field: SortField = SortField.DATE,
    sort_order: SortOrder = SortOrder.DESC,
    folder: str | None = None,
    untagged_only: bool = False,
) -> GalleryPage:
    if limit <= 0:
        raise ValueError("limit debe ser positivo")
    hidden = db.get_hidden_tag_ids()
    total = db.get_photo_count(
        tag_ids=tag_ids or None,
        hidden_tag_ids=hidden,
        search=search,
        folder=folder,
        untagged_only=untagged_only,
    )
    photos = get_gallery_photos(
        tag_ids, search, limit, offset, sort_field, sort_order, folder, untagged_only, hidden
    )
    return GalleryPage(
        photos=photos,
        total=total,
        offset=offset,
        limit=limit,
        sort_field=sort_field,
        sort_order=sort_order,
    )


ORIENTATIONS = ("landscape", "portrait", "square")


@dataclass(frozen=True)
class GalleryQuery:
    """
    Lo que muestra la galería: filtros + orden. Las etiquetas ocultas se
    excluyen siempre. Se puede guardar (to_dict/from_dict) como búsqueda.

    Etiquetas: `tag_ids` = incluir; con `match_any` basta con una (OR), si no,
    todas (AND); `exclude_tag_ids` = ninguna (NOT). Una etiqueta incluye a sus
    descendientes (filtrar por "viajes" muestra también "viajes › playa").
    """

    tag_ids: tuple[int, ...] = ()
    exclude_tag_ids: tuple[int, ...] = ()
    match_any: bool = False
    search: str | None = None
    folder: str | None = None
    sort_field: SortField = SortField.DATE
    sort_order: SortOrder = SortOrder.DESC
    # Atributos (fase 6)
    media_type: str | None = None  # "image" | "video"
    year_from: int | None = None
    year_to: int | None = None
    min_megapixels: float | None = None
    orientation: str | None = None  # ORIENTATIONS
    min_duration: float | None = None
    max_duration: float | None = None
    min_rating: int | None = None
    favorites_only: bool = False
    untagged_only: bool = False
    has_note: bool = False

    def photo_filter(self) -> db.PhotoFilter:
        desc = tag_descendants()

        def expand(tid: int) -> list[int]:
            return sorted({tid} | desc.get(tid, set()))

        included = [t for t in self.tag_ids if t in desc]
        excluded = [t for t in self.exclude_tag_ids if t in desc]  # las borradas se ignoran
        groups = None if self.match_any else [expand(t) for t in included] or None
        any_ids = sorted({x for t in included for x in expand(t)}) if self.match_any and included else None
        return db.PhotoFilter(
            hidden_tag_ids=db.get_hidden_tag_ids(),
            search=self.search or None,
            folder=self.folder or None,
            untagged_only=self.untagged_only,
            tag_groups=groups,
            any_tag_ids=any_ids,
            exclude_tag_ids=sorted({x for t in excluded for x in expand(t)}) or None,
            media_type=self.media_type,
            year_from=self.year_from,
            year_to=self.year_to,
            min_pixels=int(self.min_megapixels * 1_000_000) if self.min_megapixels else None,
            orientation=self.orientation if self.orientation in ORIENTATIONS else None,
            min_duration=self.min_duration,
            max_duration=self.max_duration,
            min_rating=self.min_rating or None,
            favorites_only=self.favorites_only,
            has_note=self.has_note,
        )

    def attribute_filter_count(self) -> int:
        """Cuántos filtros de atributos (barra de filtros) están activos."""
        return sum(
            [
                self.media_type is not None,
                self.year_from is not None or self.year_to is not None,
                bool(self.min_megapixels),
                self.orientation is not None,
                self.min_duration is not None or self.max_duration is not None,
                bool(self.min_rating),
                self.favorites_only,
                self.untagged_only,
                self.has_note,
            ]
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sort_field"] = self.sort_field.name
        d["sort_order"] = self.sort_order.name
        d["tag_ids"] = list(self.tag_ids)
        d["exclude_tag_ids"] = list(self.exclude_tag_ids)
        return {
            k: v for k, v in d.items() if v not in (None, False, [], ()) or k in ("sort_field", "sort_order")
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GalleryQuery":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        data["tag_ids"] = tuple(int(t) for t in data.get("tag_ids", ()))
        data["exclude_tag_ids"] = tuple(int(t) for t in data.get("exclude_tag_ids", ()))
        data["sort_field"] = SortField.__members__.get(str(data.get("sort_field")), SortField.DATE)
        data["sort_order"] = SortOrder.__members__.get(str(data.get("sort_order")), SortOrder.DESC)
        return cls(**data)


def count_gallery(q: GalleryQuery) -> int:
    return db.count_filtered(q.photo_filter())


def get_gallery_chunk(q: GalleryQuery, offset: int, limit: int) -> list[Photo]:
    """Un tramo de la galería (la vista virtualizada pide los que se ven)."""
    return db.query_photos(q.photo_filter(), q.sort_field, q.sort_order, limit=limit, offset=offset)


def get_gallery_ids(q: GalleryQuery, offset: int = 0, limit: int = -1) -> list[int]:
    """Ids de un tramo (o de todo) sin cargar las fotos: selecciones de miles de fotos."""
    return db.get_photo_ids(q.photo_filter(), q.sort_field, q.sort_order, limit=limit, offset=offset)


NO_MONTH = db.NO_MONTH


def gallery_row_of_date(q: GalleryQuery, year: int | None, month: int | None = None) -> int:
    """
    Fila de la primera foto de ese año/mes (solo con orden por fecha).
    month=None: el año completo; month=NO_MONTH: las de ese año sin mes.
    """
    if q.sort_field != SortField.DATE:
        raise ValueError("Saltar a una fecha requiere ordenar por fecha")
    return db.count_before_date(q.photo_filter(), year, month, q.sort_order)


@dataclass(frozen=True)
class DateBucket:
    year: int | None
    month: int | None
    count: int


def get_date_histogram(q: GalleryQuery) -> list[DateBucket]:
    """Fotos por año/mes con los filtros de la galería; lo más nuevo primero, sin fecha al final."""
    rows = db.get_date_histogram(q.photo_filter())
    rows.sort(key=lambda r: (r[0] is None, -(r[0] or 0), r[1] is None, -(r[1] or 0)))
    return [DateBucket(y, m, n) for y, m, n in rows]


@dataclass
class FolderNode:
    """Carpeta del árbol: `count` = archivos directamente en ella, `total` = con subcarpetas."""

    name: str
    path: str
    count: int = 0
    total: int = 0
    children: list["FolderNode"] = field(default_factory=list)


def build_folder_tree(folder_counts: list[tuple[str, int]]) -> list[FolderNode]:
    """Árbol de carpetas a partir de [(carpeta, n)]. Devuelve las raíces (unidades)."""
    roots: dict[str, FolderNode] = {}
    index: dict[str, FolderNode] = {}
    for folder, n in folder_counts:
        parts = Path(folder).parts
        if not parts:
            continue
        node = None
        for i in range(len(parts)):
            path = os.path.join(*parts[: i + 1])
            child = index.get(path)
            if child is None:
                child = FolderNode(name=parts[i].rstrip("\\/") or parts[i], path=path)
                index[path] = child
                if node is None:
                    roots[path] = child
                else:
                    node.children.append(child)
            child.total += n
            node = child
        assert node is not None
        node.count += n
    for node in index.values():
        node.children.sort(key=lambda c: c.name.lower())
    return sorted(roots.values(), key=lambda c: c.name.lower())


def get_folder_tree() -> list[FolderNode]:
    return build_folder_tree(db.get_folder_counts())


def get_photo_details(photo: Photo) -> dict[str, str]:
    """Datos de cámara (EXIF) para el panel de información. Lee el archivo: llamar desde un hilo."""
    if photo.is_video:
        return {}
    return indexer.read_exif_details(photo.path)


def get_photos_for_tagging(
    folder: str | None = None,
    tag_ids: list[int] | None = None,
    untagged_only: bool = False,
) -> list[Photo]:
    """
    Devuelve la lista COMPLETA de fotos para el modo etiquetado rápido
    (sin paginación, para navegar libremente). Antes tenía limit=99_999 y con
    la colección real (171k sin etiquetar) se perdían 71k fotos.
    """
    hidden = db.get_hidden_tag_ids()
    return db.get_photos(
        tag_ids=tag_ids or None,
        hidden_tag_ids=hidden,
        folder=folder,
        untagged_only=untagged_only,
        limit=-1,  # SQLite: LIMIT -1 = sin límite
        offset=0,
        sort_field=SortField.DATE,
        sort_order=SortOrder.ASC,
    )


def count_photos_for_tagging(
    folder: str | None = None,
    tag_ids: list[int] | None = None,
    untagged_only: bool = False,
) -> int:
    """Cuántas fotos devolvería get_photos_for_tagging (sin cargarlas)."""
    return db.get_photo_count(
        tag_ids=tag_ids or None,
        hidden_tag_ids=db.get_hidden_tag_ids(),
        folder=folder,
        untagged_only=untagged_only,
    )


def get_photo(photo_id: int) -> Photo | None:
    return db.get_photo_by_id(photo_id)


# ── Etiquetas de una foto ─────────────────────────────────────────────────────


def get_photo_tags(photo_id: int) -> list[Tag]:
    return db.get_photo_tags(photo_id)


def _tag_id_for_name(name: str) -> int:
    """Etiqueta con ese nombre o alias; si no hay ninguna, la crea."""
    return db.resolve_tag_name(name) or db.create_tag(name)


def add_tag(photo_id: int, tag_name: str) -> Tag:
    """Agrega una etiqueta por nombre. Un alias agrega la etiqueta a la que apunta."""
    tag_name = tag_name.strip().lower()
    if not tag_name:
        raise ValueError("El nombre de etiqueta no puede estar vacío.")
    tag_id = _tag_id_for_name(tag_name)
    db.add_tag_to_photo(photo_id, tag_id)
    _sync_sidecars([photo_id])
    tag = db.get_tag(tag_id)
    assert tag is not None
    return tag


def add_tag_by_id(photo_id: int, tag_id: int) -> None:
    """Asigna una etiqueta existente (sin buscarla por nombre)."""
    db.add_tag_to_photo(photo_id, tag_id)
    _sync_sidecars([photo_id])


def remove_tag(photo_id: int, tag_id: int):
    db.remove_tag_from_photo(photo_id, tag_id)
    _sync_sidecars([photo_id])


# ── Etiquetado en lote ────────────────────────────────────────────────────────


def bulk_add_tag(photo_ids: list[int], tag_name: str) -> int:
    """
    Agrega una etiqueta a varias fotos a la vez.
    Crea la etiqueta si no existe. Devuelve cuántas fotos fueron afectadas.
    """
    if not photo_ids or not tag_name.strip():
        return 0
    tag_id = _tag_id_for_name(tag_name.strip().lower())
    db.add_tag_to_photos(photo_ids, tag_id)
    _sync_sidecars(photo_ids)
    return len(photo_ids)


def bulk_remove_tag(photo_ids: list[int], tag_id: int) -> int:
    """Quita una etiqueta de varias fotos. Devuelve cuántas fotos fueron afectadas."""
    if not photo_ids:
        return 0
    db.remove_tag_from_photos(photo_ids, tag_id)
    _sync_sidecars(photo_ids)
    return len(photo_ids)


# ── Gestión de etiquetas ──────────────────────────────────────────────────────


def get_all_tags(include_sidebar_hidden: bool = True) -> list[Tag]:
    return db.get_all_tags(include_sidebar_hidden=include_sidebar_hidden)


def get_sidebar_tags(include_hidden: bool = False) -> dict[str, list[Tag]]:
    tags = db.get_all_tags(include_sidebar_hidden=include_hidden)
    groups: dict[str, list[Tag]] = {}
    for tag in tags:
        groups.setdefault(tag.category or "general", []).append(tag)
    return groups


def count_sidebar_hidden_tags() -> int:
    return sum(1 for t in db.get_all_tags() if t.sidebar_hidden)


def create_tag(name: str, category: str = "general", color: str = "#4A9EFF") -> int:
    return db.create_tag(name, category, color)


_KEEP_PARENT = db._KEEP


def update_tag(
    tag_id: int,
    name: str,
    category: str,
    color: str,
    parent_id: int | None | object = _KEEP_PARENT,
    aliases: list[str] | None = None,
) -> None:
    """
    Cambia la etiqueta en una sola transacción. Lanza db.TagNameConflictError
    si el nombre o un alias ya lo usa otra etiqueta, y ValueError si el padre
    formaría un ciclo.
    """
    db.update_tag(tag_id, name, category, color, parent_id=parent_id, aliases=aliases)
    affected = set(db.get_photo_ids_with_tag(tag_id))
    for child in tag_descendants().get(tag_id, set()):  # su ruta en el .xmp cambia
        affected.update(db.get_photo_ids_with_tag(child))
    _sync_sidecars(sorted(affected))


def tag_descendants() -> dict[int, set[int]]:
    """{tag_id: ids de todas sus descendientes} (vacío si no tiene hijas)."""
    parents = db.get_tag_parents()
    children: dict[int, list[int]] = defaultdict(list)
    for tid, parent in parents.items():
        if parent is not None:
            children[parent].append(tid)
    out: dict[int, set[int]] = {}
    for tid in parents:
        seen: set[int] = set()
        stack = list(children.get(tid, []))
        while stack:
            c = stack.pop()
            if c not in seen and c != tid:
                seen.add(c)
                stack.extend(children.get(c, []))
        out[tid] = seen
    return out


def tag_path_names() -> dict[int, list[str]]:
    """{tag_id: [raíz, …, nombre]} (para mostrar "padre › hija" y para el .xmp)."""
    tags = {t.id: t for t in db.get_all_tags()}
    out: dict[int, list[str]] = {}
    for tid, tag in tags.items():
        path = [tag.name]
        seen = {tid}
        parent = tag.parent_id
        while parent is not None and parent in tags and parent not in seen:
            seen.add(parent)
            path.insert(0, tags[parent].name)
            parent = tags[parent].parent_id
        out[tid] = path
    return out


def get_tag_aliases() -> dict[int, list[str]]:
    return db.get_tag_aliases()


def get_tag_photo_counts() -> dict[int, int]:
    return db.get_tag_photo_counts()


def merge_tags(source_id: int, target_id: int) -> int:
    """
    Fusiona `source` en `target` (ver db.merge_tags): el nombre viejo queda
    como alias. Las búsquedas guardadas que usaban `source` pasan a `target`.
    Devuelve cuántas fotos tenían `source`.
    """
    affected = db.get_photo_ids_with_tag(source_id)
    n = db.merge_tags(source_id, target_id)
    for saved in list_saved_searches():
        q = GalleryQuery.from_dict(saved.query)

        def swap(ids: tuple[int, ...]) -> tuple[int, ...]:
            return tuple(dict.fromkeys(target_id if t == source_id else t for t in ids))

        new = replace(q, tag_ids=swap(q.tag_ids), exclude_tag_ids=swap(q.exclude_tag_ids))
        if new != q:
            db.update_saved_search_json(saved.id, json.dumps(new.to_dict(), ensure_ascii=False))
    _sync_sidecars(affected)
    return n


def get_tag(tag_id: int) -> Tag | None:
    return db.get_tag(tag_id)


def count_photos_with_tag(tag_id: int) -> int:
    return db.count_photos_with_tag(tag_id)


def delete_tag(tag_id: int):
    affected = db.get_photo_ids_with_tag(tag_id)
    db.delete_tag(tag_id)
    _sync_sidecars(affected)


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


def _photo_ids_in_category(category: str) -> list[int]:
    ids: set[int] = set()
    for t in db.get_tags_by_category(category):
        ids.update(db.get_photo_ids_with_tag(t.id))
    return sorted(ids)


def rename_category(old_name: str, new_name: str):
    affected = _photo_ids_in_category(old_name) if is_xmp_enabled() else []
    db.rename_category(old_name, new_name)
    _sync_sidecars(affected)


def delete_category(name: str):
    affected = _photo_ids_in_category(name) if is_xmp_enabled() else []
    db.delete_category(name)
    _sync_sidecars(affected)


# ── Valoración, favoritas y notas ─────────────────────────────────────────────


def set_rating(photo_ids: list[int], rating: int) -> None:
    """0 = quitar la valoración; 1–5 estrellas. Se escribe en el .xmp (xmp:Rating)."""
    if not photo_ids:
        return
    db.set_rating(photo_ids, rating)
    _sync_sidecars(photo_ids)


def toggle_favorite(photo_ids: list[int]) -> bool:
    """Si todas eran favoritas, las quita; si no, las marca todas. Devuelve el estado nuevo."""
    if not photo_ids:
        return False
    new_state = db.count_favorites(photo_ids) < len(photo_ids)
    db.set_favorite(photo_ids, new_state)
    return new_state


def set_note(photo_id: int, note: str | None) -> None:
    db.set_note(photo_id, note)


# ── Búsquedas guardadas y álbumes inteligentes ────────────────────────────────

# Álbumes que siempre están (no se pueden borrar): nombre → consulta
BUILTIN_ALBUMS: list[tuple[str, GalleryQuery]] = [
    ("♥ Favoritas", GalleryQuery(favorites_only=True)),
    ("★★★★ o más", GalleryQuery(min_rating=4, sort_field=SortField.RATING)),
    ("🏷 Sin etiquetar", GalleryQuery(untagged_only=True)),
    ("🎬 Videos", GalleryQuery(media_type="video")),
    ("📝 Con notas", GalleryQuery(has_note=True)),
]


def list_saved_searches() -> list[SavedSearch]:
    out = []
    for sid, name, raw in db.list_saved_searches():
        try:
            query = json.loads(raw)
        except ValueError:
            logger.warning("Búsqueda guardada %r con JSON inválido; se ignora", name)
            continue
        out.append(SavedSearch(id=sid, name=name, query=query if isinstance(query, dict) else {}))
    return out


def save_search(name: str, q: GalleryQuery) -> int:
    """Guarda (o reemplaza, si ya hay una con ese nombre) la búsqueda actual."""
    return db.save_search(name, json.dumps(q.to_dict(), ensure_ascii=False))


def saved_search_exists(name: str) -> bool:
    return any(s.name.casefold() == name.strip().casefold() for s in list_saved_searches())


def rename_saved_search(search_id: int, name: str) -> None:
    db.rename_saved_search(search_id, name)


def delete_saved_search(search_id: int) -> None:
    db.delete_saved_search(search_id)


# ── Estadísticas ──────────────────────────────────────────────────────────────


def get_stats() -> Stats:
    return db.get_stats()


def get_totals() -> tuple[int, int]:
    """(fotos, etiquetas): lo que muestra el sidebar, sin calcular todas las estadísticas."""
    return db.get_totals()


# ── Preferencias ──────────────────────────────────────────────────────────────

THUMB_DISPLAY_DEFAULT = 200
THUMB_DISPLAY_MIN = 100
THUMB_DISPLAY_MAX = 400


def get_thumb_display_size() -> int:
    """Tamaño de las miniaturas en la galería (slider), en píxeles."""
    raw = db.get_setting("thumb_size")
    try:
        value = int(raw) if raw is not None else THUMB_DISPLAY_DEFAULT
    except ValueError:
        value = THUMB_DISPLAY_DEFAULT
    return max(THUMB_DISPLAY_MIN, min(THUMB_DISPLAY_MAX, value))


def set_thumb_display_size(value: int) -> None:
    db.set_setting("thumb_size", str(max(THUMB_DISPLAY_MIN, min(THUMB_DISPLAY_MAX, int(value)))))


# ── Exportar / importar etiquetas ─────────────────────────────────────────────

EXPORT_VERSION = 3


@dataclass
class ExportSummary:
    tags: int
    assignments: int  # fotos con etiquetas


@dataclass
class ImportResult:
    created: int = 0  # etiquetas nuevas
    skipped: int = 0  # etiquetas que ya existían
    photos_matched: int = 0  # fotos encontradas por ruta exacta
    photos_by_name: int = 0  # encontradas por nombre + tamaño (ruta distinta)
    photos_missing: int = 0  # no encontradas en la DB
    pairs_added: int = 0  # asignaciones foto↔etiqueta nuevas
    extras_added: int = 0  # fotos a las que se les agregó valoración/favorita/nota (v3)


def export_tags(path: str) -> ExportSummary:
    """
    Exporta etiquetas (con padre y alias), categorías y, por foto, sus
    etiquetas, valoración, favorita y nota a un JSON (formato versión 3).
    Es un respaldo completo del trabajo de organización.
    """
    tags = db.get_all_tags()
    names = {t.id: t.name for t in tags}
    aliases = db.get_tag_aliases()
    by_path: dict[str, dict] = {
        p: {"path": p, "filename": fn, "filesize": size, "tags": tag_names}
        for p, fn, size, tag_names in db.get_all_assignments()
    }
    for p, (fn, size, rating, favorite, note) in db.get_photo_extras().items():
        entry = by_path.setdefault(p, {"path": p, "filename": fn, "filesize": size, "tags": []})
        if rating:
            entry["rating"] = rating
        if favorite:
            entry["favorite"] = True
        if note:
            entry["note"] = note
    assignments = [by_path[p] for p in sorted(by_path)]
    data = {
        "version": EXPORT_VERSION,
        "app": "PhotoVault",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tags": [
            {
                "name": t.name,
                "category": t.category,
                "color": t.color,
                "hidden": t.hidden,
                "sidebar_hidden": t.sidebar_hidden,
                **({"parent": names[t.parent_id]} if t.parent_id in names else {}),
                **({"aliases": aliases[t.id]} if aliases.get(t.id) else {}),
            }
            for t in tags
        ],
        "categories": db.get_all_categories(),
        "assignments": assignments,
    }
    # Escribir a .tmp y renombrar: nunca queda un archivo a medias
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    logger.info("Exportadas %d etiquetas y %d fotos etiquetadas a %s", len(tags), len(assignments), path)
    return ExportSummary(tags=len(tags), assignments=len(assignments))


def _read_export(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") not in (1, 2, 3):
        raise ValueError("Formato de archivo no reconocido.")
    return data


def read_export_summary(path: str) -> ExportSummary:
    """Qué contiene un archivo exportado (para preguntar antes de importar)."""
    data = _read_export(path)
    return ExportSummary(tags=len(data.get("tags", [])), assignments=len(data.get("assignments", [])))


def import_tags(path: str, include_assignments: bool = True) -> ImportResult:
    """
    Importa un JSON de export_tags (versión 1, 2 o 3).
    - Etiquetas: solo crea las que no existen; nunca sobrescribe. Padre y
      alias (v3) solo se ponen si la etiqueta no tenía y no chocan con nada.
    - Valoración/favorita/nota (v3): solo se agregan (la valoración más alta,
      la nota si la foto no tenía).
    - Asignaciones (v2): empareja cada foto por ruta exacta; si no está, por
      nombre + tamaño cuando hay UNA sola coincidencia (sirve si cambió la
      letra de la unidad o se movió la carpeta). Solo agrega, nunca quita.
    """
    data = _read_export(path)
    result = ImportResult()

    for cat in data.get("categories", []):
        db.create_category(cat)

    existing_names = {t.name for t in db.get_all_tags()}
    for tag_data in data.get("tags", []):
        name = tag_data.get("name", "").strip().lower()
        if not name:
            continue
        if name in existing_names:
            result.skipped += 1
            continue
        db.create_tag(name, tag_data.get("category", "general"), tag_data.get("color", "#4A9EFF"))
        existing_names.add(name)
        result.created += 1
    _import_tag_tree(data.get("tags", []))

    if include_assignments and data.get("assignments"):
        by_path, by_name = db.get_photo_lookup()
        tag_ids = db.get_tag_ids_by_name()
        pairs: list[tuple[int, int]] = []
        extras: list[tuple[int, int, bool, str | None]] = []
        touched: list[int] = []
        for a in data["assignments"]:
            pid = by_path.get(a.get("path", ""))
            if pid is not None:
                result.photos_matched += 1
            else:
                candidates = by_name.get((str(a.get("filename", "")).lower(), a.get("filesize")), [])
                if len(candidates) == 1:
                    pid = candidates[0]
                    result.photos_by_name += 1
                else:
                    result.photos_missing += 1
                    continue
            touched.append(pid)
            for name in a.get("tags", []):
                name = str(name).strip().lower()
                if name not in tag_ids:
                    alias_of = db.resolve_tag_name(name)
                    tag_ids[name] = alias_of if alias_of is not None else db.create_tag(name)
                    result.created += alias_of is None
                pairs.append((pid, tag_ids[name]))
            rating = a.get("rating") or 0
            if rating or a.get("favorite") or a.get("note"):
                extras.append(
                    (pid, max(0, min(5, int(rating))), bool(a.get("favorite")), a.get("note") or None)
                )
        result.pairs_added = db.add_assignments(pairs)
        result.extras_added = db.merge_photo_extras(extras)
        _sync_sidecars(touched)

    logger.info("Importación desde %s: %s", path, result)
    return result


def _import_tag_tree(tags_data: list[dict]) -> None:
    """Padre y alias del JSON v3, sin pisar lo que ya haya en la DB."""
    current = {t.name: t for t in db.get_all_tags()}
    aliases = db.get_tag_aliases()
    for tag_data in tags_data:
        name = str(tag_data.get("name", "")).strip().lower()
        tag = current.get(name)
        if tag is None:
            continue
        parent = current.get(str(tag_data.get("parent", "")).strip().lower())
        if parent is not None and tag.parent_id is None:
            try:
                db.set_tag_parent(tag.id, parent.id)
            except ValueError as e:
                logger.warning("No se importó el padre de %s: %s", name, e)
        new_aliases = [a for a in tag_data.get("aliases", []) if isinstance(a, str)]
        if new_aliases and not aliases.get(tag.id):
            try:
                db.set_tag_aliases(tag.id, new_aliases)
            except ValueError as e:
                logger.warning("No se importaron los alias de %s: %s", name, e)


# ── Sidecars XMP ──────────────────────────────────────────────────────────────

XMP_SETTING = "xmp_sidecars"


def is_xmp_enabled() -> bool:
    return db.get_setting(XMP_SETTING) == "1"


def set_xmp_enabled(enabled: bool) -> None:
    db.set_setting(XMP_SETTING, "1" if enabled else "0")


def _write_photo_sidecar(photo_id: int) -> xmp_sidecar.WriteResult | None:
    photo = db.get_photo_by_id(photo_id)
    if photo is None:
        return None
    photo_tags = db.get_photo_tags(photo_id)
    paths = tag_path_names() if any(t.parent_id for t in photo_tags) else {}
    tags: list[tuple[str, str | None]] = []
    for t in photo_tags:
        parents = paths.get(t.id, [t.name])[:-1]
        tags.append((t.name, "|".join([t.category, *parents]) if t.category else None))
    return xmp_sidecar.write_sidecar(photo.path, tags, photo.rating)


def _sync_sidecars(photo_ids: list[int]) -> None:
    """Actualiza los sidecars de estas fotos si la opción está activada."""
    if not photo_ids or not is_xmp_enabled():
        return
    for pid in photo_ids:
        _write_photo_sidecar(pid)


@dataclass
class SidecarSyncResult:
    written: int = 0
    unchanged: int = 0
    foreign: int = 0  # había un .xmp de otro programa: no se tocó
    errors: int = 0


def sync_all_sidecars(
    progress_callback: ProgressCallback | None = None, should_stop: StopCheck | None = None
) -> SidecarSyncResult:
    """Escribe el sidecar de todas las fotos con etiquetas o valoración."""
    ids = sorted(set(db.get_tagged_photo_ids()) | set(db.get_rated_photo_ids()))
    result = SidecarSyncResult()
    for i, pid in enumerate(ids):
        if should_stop and should_stop():
            break
        if progress_callback:
            progress_callback(i + 1, len(ids))
        r = _write_photo_sidecar(pid)
        if r == xmp_sidecar.WriteResult.WRITTEN:
            result.written += 1
        elif r == xmp_sidecar.WriteResult.SKIPPED_FOREIGN:
            result.foreign += 1
        elif r == xmp_sidecar.WriteResult.ERROR:
            result.errors += 1
        else:
            result.unchanged += 1
    logger.info("Sincronización de sidecars: %s", result)
    return result


@dataclass
class SidecarImportResult:
    checked: int = 0
    with_xmp: int = 0
    pairs_added: int = 0
    tags_created: int = 0
    ratings_added: int = 0


def import_from_sidecars(
    progress_callback: ProgressCallback | None = None, should_stop: StopCheck | None = None
) -> SidecarImportResult:
    """
    Lee los .xmp junto a cada foto indexada (de PhotoVault o de otros
    programas) y agrega esas etiquetas. Solo agrega, nunca quita.
    """
    rows = db.get_all_photo_paths()
    tag_ids = db.get_tag_ids_by_name()
    result = SidecarImportResult()
    pairs: list[tuple[int, int]] = []
    ratings: list[tuple[int, int, bool, str | None]] = []
    for i, (pid, path) in enumerate(rows):
        if should_stop and should_stop():
            break
        result.checked += 1
        if progress_callback and (i % 200 == 0 or i + 1 == len(rows)):
            progress_callback(i + 1, len(rows))
        full = xmp_sidecar.read_sidecar_full(path)
        if not full or not (full[0] or full[1]):
            continue
        tags, rating = full
        result.with_xmp += 1
        if rating:
            ratings.append((pid, rating, False, None))
        for name, category in tags:
            if name not in tag_ids:
                alias_of = db.resolve_tag_name(name)
                if alias_of is not None:
                    tag_ids[name] = alias_of
                else:
                    tag_ids[name] = db.create_tag(name, category or "general")
                    result.tags_created += 1
            pairs.append((pid, tag_ids[name]))
    result.pairs_added = db.add_assignments(pairs)
    result.ratings_added = db.merge_photo_extras(ratings)
    logger.info("Importación desde sidecars: %s", result)
    return result


# ── Papelera interna ──────────────────────────────────────────────────────────

TRASH_KEEP_DAYS = 30


def list_trash() -> list[TrashBatch]:
    return db.list_trash_batches()


def count_trash() -> int:
    return db.count_trash()


def restore_trash_batch(batch_id: str) -> tuple[int, int]:
    """Devuelve (restaurados, fusionados con un registro que ya existía)."""
    restored, merged = db.restore_trash_batch(batch_id)
    return restored, merged


def delete_trash_batch(batch_id: str) -> int:
    return db.delete_trash_batch(batch_id)


def empty_trash() -> int:
    return db.purge_trash()


def purge_old_trash() -> int:
    """Al arrancar: borra de la papelera lo que tenga más de TRASH_KEEP_DAYS días."""
    n = db.purge_trash(older_than_days=TRASH_KEEP_DAYS)
    if n:
        logger.info("Papelera interna: %d registros con más de %d días eliminados", n, TRASH_KEEP_DAYS)
    return n


# ── Reubicar carpeta / unidad ─────────────────────────────────────────────────


@dataclass
class RelocationPreview:
    old_folder: str
    new_folder: str
    count: int  # registros que cambian de ruta
    conflicts: int  # ya existe un registro con la ruta nueva → se fusionan
    sample_size: int  # cuántas rutas nuevas se revisaron en disco
    sample_found: int  # cuántas de ellas existen
    plan: list = field(default_factory=list, repr=False)

    @property
    def looks_right(self) -> bool:
        """Al menos la mitad de la muestra existe en la ubicación nueva."""
        return self.sample_size > 0 and self.sample_found * 2 >= self.sample_size


def preview_relocation(old_folder: str, new_folder: str, sample: int = 25) -> RelocationPreview:
    """
    Calcula qué pasaría al reubicar old_folder → new_folder (no cambia nada).
    Revisa una muestra de rutas nuevas en disco para detectar errores de tipeo.
    """
    # Validar antes de normpath: normpath("") devuelve "."
    if not old_folder.strip() or not new_folder.strip():
        raise ValueError("Indica la carpeta vieja y la nueva.")
    old_n = os.path.normpath(old_folder.strip()).rstrip("/\\")
    new_n = os.path.normpath(new_folder.strip()).rstrip("/\\")
    if old_n.lower() == new_n.lower():
        raise ValueError("La carpeta nueva es igual a la vieja.")

    plan = db.get_relocation_plan(old_folder, new_folder)
    step = max(1, len(plan) // sample) if plan else 1
    checked = plan[::step][:sample]
    found = sum(1 for _pid, new_path, _e in checked if Path(new_path).exists())
    return RelocationPreview(
        old_folder=old_n,
        new_folder=new_n,
        count=len(plan),
        conflicts=sum(1 for _p, _n, e in plan if e is not None),
        sample_size=len(checked),
        sample_found=found,
        plan=plan,
    )


def apply_relocation(preview: RelocationPreview) -> tuple[int, int]:
    """Aplica una vista previa de preview_relocation. Devuelve (movidos, fusionados)."""
    moved, merged = db.apply_relocation(preview.plan)
    logger.info(
        "Reubicado %s → %s: %d movidos, %d fusionados", preview.old_folder, preview.new_folder, moved, merged
    )
    return moved, merged


def get_indexed_roots() -> list[tuple[str, int, bool]]:
    """[(unidad, registros, disponible)] para sugerir qué reubicar."""
    return [(root, n, Path(root).exists()) for root, n in db.get_root_counts()]


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


def compute_missing_md5s(
    progress_callback: ProgressCallback | None = None, should_stop: StopCheck | None = None
) -> int:
    """
    Calcula el MD5 de las fotos que aún no lo tienen en la DB, pero SOLO de
    las que comparten tamaño con otra: un archivo de tamaño único no puede
    tener duplicado exacto (en la colección real: 38 % de los archivos).
    progress_callback(current, total) se llama por cada archivo procesado.
    should_stop() se consulta antes de cada archivo para poder cancelar.
    Devuelve la cantidad de archivos procesados.
    """
    pending = db.get_duplicate_candidates_without_md5()
    total = len(pending)

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

    groups = [DuplicateGroup(md5=md5, photos=photos) for md5, photos in buckets.items() if len(photos) >= 2]
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
    db.delete_photos([photo_id], reason=f"Duplicado enviado a la Papelera de Windows: {photo.filename}")
    return True


# ── Mantenimiento ─────────────────────────────────────────────────────────────

# Si en una unidad disponible faltan TODOS los archivos y son al menos esta
# cantidad, se asume que es otro disco con la misma letra (o una carpeta
# desmontada) y no se borra nada de esa unidad.
SUSPICIOUS_ROOT_MIN = 20


@dataclass
class SkippedRoot:
    root: str  # p. ej. "G:\\"
    count: int  # registros en esa unidad
    reason: str


@dataclass
class MissingReport:
    """Resultado de buscar registros cuyo archivo ya no existe."""

    missing_ids: list[int] = field(default_factory=list)
    tagged: int = 0  # cuántas de ellas tienen etiquetas
    checked: int = 0
    skipped: list[SkippedRoot] = field(default_factory=list)
    cancelled: bool = False

    @property
    def count(self) -> int:
        return len(self.missing_ids)


def _root_of(path: str) -> str:
    return Path(path).anchor or "(sin unidad)"


def find_missing_files(
    progress_callback: ProgressCallback | None = None, should_stop: StopCheck | None = None
) -> MissingReport:
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
    total = len(rows)
    done = 0

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
            report.skipped.append(
                SkippedRoot(
                    root,
                    len(items),
                    "faltan todos sus archivos (¿es otro disco con la misma letra?)",
                )
            )
        else:
            report.missing_ids.extend(missing_here)

    report.checked = done
    report.tagged = db.count_tagged(report.missing_ids)
    logger.info(
        "Búsqueda de faltantes: %d de %d; unidades omitidas: %s",
        report.count,
        total,
        [(s.root, s.count) for s in report.skipped],
    )
    return report


def delete_missing(report: MissingReport) -> int:
    """Borra los registros encontrados por find_missing_files()."""
    if report.cancelled:
        return 0
    n = db.delete_photos(report.missing_ids, reason="Archivos que ya no existían en disco")
    logger.info("Eliminados %d registros de archivos faltantes", n)
    return n


def preview_deindex_folder(folder: str) -> tuple[int, int]:
    """(cantidad de registros, cuántos tienen etiquetas) que borraría deindex_folder."""
    ids = db.get_folder_photo_ids(folder)
    return len(ids), db.count_tagged(ids)


def deindex_folder(folder: str) -> int:
    """Quita los registros de `folder` y sus subcarpetas. No toca el disco."""
    n = db.delete_photos(db.get_folder_photo_ids(folder), reason=f"Carpeta des-indexada: {folder}")
    logger.info("Des-indexada %s: %d registros", folder, n)
    return n


def get_indexed_folders() -> list[tuple[str, int]]:
    """[(carpeta, n.º de archivos directamente en ella)] ordenado por carpeta."""
    return db.get_folder_counts()


def purge_cache_orphans() -> int:
    """
    Elimina del caché de miniaturas los archivos que ya no tienen registro en la DB.
    Devuelve la cantidad de archivos eliminados.
    """
    return thumbnail_cache.purge_orphans(db.get_paths_with_mtime())


@dataclass
class ThumbnailBatchResult:
    generated: int = 0
    already_cached: int = 0
    failed: int = 0


THUMB_WORKERS = max(1, min(4, (os.cpu_count() or 2) - 1))


def pregenerate_thumbnails(
    photo_ids: list[int] | None = None,
    progress_callback: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> ThumbnailBatchResult:
    """
    Genera en segundo plano las miniaturas de galería que falten (de esas fotos,
    o de toda la colección si photo_ids es None). Usa varios hilos: Pillow
    suelta el GIL al decodificar, así que de verdad corre en paralelo.
    """
    from concurrent.futures import ThreadPoolExecutor

    photos = db.get_photos_by_ids(photo_ids) if photo_ids is not None else db.get_photos(limit=-1)
    result = ThumbnailBatchResult()
    total = len(photos)
    if progress_callback:
        progress_callback(0, total)

    def one(p: Photo) -> str:
        # Comprobar el caché dentro del hilo: con registros sin mtime en la DB
        # eso consulta el disco de la colección, y hacerlo antes para 170k fotos
        # tardaba minutos sin poder cancelarse.
        if thumbnail_cache.is_cached(p.path, video=p.is_video, mtime=p.mtime):
            return "cached"
        return "ok" if thumbnail_cache.get_photo_thumbnail(p) is not None else "failed"

    chunk_size = THUMB_WORKERS * 8
    with ThreadPoolExecutor(max_workers=THUMB_WORKERS) as pool:
        # Tandas pequeñas: se puede cancelar en cualquier momento
        for start in range(0, total, chunk_size):
            if should_stop and should_stop():
                break
            chunk = photos[start : start + chunk_size]
            for status in pool.map(one, chunk):
                if status == "ok":
                    result.generated += 1
                elif status == "cached":
                    result.already_cached += 1
                else:
                    result.failed += 1
            if progress_callback:
                progress_callback(min(start + len(chunk), total), total)
    logger.info("Miniaturas pre-generadas: %s", result)
    return result
