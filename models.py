"""
PhotoVault - models.py
Dataclasses que representan las entidades del dominio.
"""

from dataclasses import dataclass
from enum import Enum

# ── Ordenamiento ──────────────────────────────────────────────────────────────


class SortField(Enum):
    DATE = "fecha"
    FILENAME = "nombre"
    FILESIZE = "tamaño"
    ADDED_AT = "agregado"
    RATING = "valoración"


class SortOrder(Enum):
    ASC = "asc"
    DESC = "desc"


# Mapa a SQL — usamos un allowlist fijo para evitar inyección.
# - Cada orden termina en p.id: con nombres o tamaños repetidos, la paginación
#   con OFFSET sería inestable (una foto podría salir en dos páginas o en ninguna).
# - ASC y DESC son exactamente inversos para que un mismo índice sirva a ambos
#   (idx_photos_date, idx_photos_filename, idx_photos_filesize, idx_photos_added).
_SORT_SQL: dict[tuple, str] = {
    (SortField.DATE, SortOrder.DESC): "p.year DESC, p.month DESC, p.filename DESC, p.id DESC",
    (SortField.DATE, SortOrder.ASC): "p.year ASC, p.month ASC, p.filename ASC, p.id ASC",
    (SortField.FILENAME, SortOrder.ASC): "p.filename ASC, p.id ASC",
    (SortField.FILENAME, SortOrder.DESC): "p.filename DESC, p.id DESC",
    (SortField.FILESIZE, SortOrder.DESC): "p.filesize DESC, p.id DESC",
    (SortField.FILESIZE, SortOrder.ASC): "p.filesize ASC, p.id ASC",
    (SortField.ADDED_AT, SortOrder.DESC): "p.added_at DESC, p.id DESC",
    (SortField.ADDED_AT, SortOrder.ASC): "p.added_at ASC, p.id ASC",
    # idx_photos_rating (v4); dentro de cada valoración, por fecha
    (
        SortField.RATING,
        SortOrder.DESC,
    ): "p.rating DESC, p.year DESC, p.month DESC, p.filename DESC, p.id DESC",
    (SortField.RATING, SortOrder.ASC): "p.rating ASC, p.year ASC, p.month ASC, p.filename ASC, p.id ASC",
}


def sort_to_sql(field: SortField, order: SortOrder) -> str:
    return _SORT_SQL.get((field, order), _SORT_SQL[(SortField.DATE, SortOrder.DESC)])


# ── Entidades ─────────────────────────────────────────────────────────────────


@dataclass
class Photo:
    id: int
    path: str
    filename: str
    year: int | None
    month: int | None
    media_type: str = "image"
    duration: float | None = None
    filesize: int | None = None
    width: int | None = None
    height: int | None = None
    added_at: str | None = None
    md5: str | None = None  # Solo se carga donde hace falta (duplicados)
    mtime: float | None = None  # mtime del archivo al indexarlo (None = antes de v3)
    rating: int = 0  # 0 = sin valorar, 1–5 estrellas (v4)
    favorite: bool = False
    note: str | None = None
    hidden: bool = False  # tiene alguna etiqueta oculta (calculado al leer; fase 9)

    @property
    def stars(self) -> str:
        return "★" * self.rating + "☆" * (5 - self.rating) if self.rating else ""

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    @property
    def duration_str(self) -> str:
        if not self.duration:
            return ""
        mins = int(self.duration) // 60
        secs = int(self.duration) % 60
        return f"{mins}:{secs:02d}"

    @property
    def short_name(self) -> str:
        max_len = 22
        return self.filename[:max_len] + "…" if len(self.filename) > max_len else self.filename


@dataclass
class Tag:
    id: int
    name: str
    category: str = "general"
    color: str = "#4A9EFF"
    hidden: bool = False
    sidebar_hidden: bool = False
    parent_id: int | None = None  # etiqueta padre (v4): filtrar por el padre incluye a los hijos


@dataclass
class SavedSearch:
    """Búsqueda guardada / álbum inteligente: `query` es GalleryQuery.to_dict()."""

    id: int
    name: str
    query: dict


@dataclass
class GalleryPage:
    photos: list[Photo]
    total: int
    offset: int
    limit: int
    sort_field: SortField = SortField.DATE
    sort_order: SortOrder = SortOrder.DESC

    @property
    def page_number(self) -> int:
        return self.offset // self.limit + 1

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.limit - 1) // self.limit)

    @property
    def has_prev(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.offset + self.limit < self.total


@dataclass
class Stats:
    total_photos: int
    total_tags: int
    years: list[tuple[int, int]]  # [(year, count), ...]
    by_month: list[tuple[int, int, int]]  # [(year, month, count), ...]
    by_type: dict[str, int]  # {"image": N, "video": M}
    top_tags: list[tuple[str, int]]  # [(tag_name, count), ...]  top 10


@dataclass
class DuplicateGroup:
    """Un grupo de fotos que comparten el mismo hash MD5."""

    md5: str
    photos: list[Photo]

    @property
    def size(self) -> int:
        return len(self.photos)

    @property
    def wasted_bytes(self) -> int:
        """Espacio que se liberaría conservando solo una copia."""
        if not self.photos:
            return 0
        sizes = [p.filesize or 0 for p in self.photos]
        return sum(sizes) - max(sizes)


@dataclass
class TrashBatch:
    """Una operación que quitó registros (des-indexar, faltantes, duplicado…)."""

    batch_id: str
    reason: str
    deleted_at: str  # UTC, formato SQLite 'YYYY-MM-DD HH:MM:SS'
    count: int
    tagged: int  # cuántos tenían etiquetas
