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


class SortOrder(Enum):
    ASC = "asc"
    DESC = "desc"


# Mapa a SQL — usamos un allowlist fijo para evitar inyección
_SORT_SQL: dict[tuple, str] = {
    (SortField.DATE, SortOrder.DESC): "p.year DESC, p.month DESC, p.filename",
    (SortField.DATE, SortOrder.ASC): "p.year ASC,  p.month ASC,  p.filename",
    (SortField.FILENAME, SortOrder.ASC): "p.filename ASC",
    (SortField.FILENAME, SortOrder.DESC): "p.filename DESC",
    (SortField.FILESIZE, SortOrder.DESC): "p.filesize DESC",
    (SortField.FILESIZE, SortOrder.ASC): "p.filesize ASC",
    (SortField.ADDED_AT, SortOrder.DESC): "p.added_at DESC",
    (SortField.ADDED_AT, SortOrder.ASC): "p.added_at ASC",
}


def sort_to_sql(field: SortField, order: SortOrder) -> str:
    return _SORT_SQL.get((field, order), "p.year DESC, p.month DESC, p.filename")


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
