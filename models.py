"""
PhotoVault - models.py
Dataclasses que representan las entidades del dominio.
La UI y los servicios usan estos tipos en lugar de sqlite3.Row o dicts crudos.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Photo:
    id:         int
    path:       str
    filename:   str
    year:       Optional[int]
    month:      Optional[int]
    media_type: str = "image"          # "image" | "video"
    duration:   Optional[float] = None # segundos, solo para videos
    filesize:   Optional[int]   = None
    width:      Optional[int]   = None
    height:     Optional[int]   = None
    added_at:   Optional[str]   = None

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    @property
    def duration_str(self) -> str:
        """Devuelve la duración formateada como M:SS, o '' si no aplica."""
        if not self.duration:
            return ""
        mins = int(self.duration) // 60
        secs = int(self.duration) % 60
        return f"{mins}:{secs:02d}"

    @property
    def short_name(self, max_len: int = 22) -> str:
        """Nombre truncado para mostrar en miniaturas."""
        return self.filename[:max_len] + "…" if len(self.filename) > max_len else self.filename


@dataclass
class Tag:
    id:             int
    name:           str
    category:       str  = "general"
    color:          str  = "#4A9EFF"
    hidden:         bool = False   # oculta las fotos que la tienen
    sidebar_hidden: bool = False   # no aparece en el panel de filtros


@dataclass
class GalleryPage:
    """Resultado de una consulta paginada a la galería."""
    photos:  list[Photo]
    total:   int
    offset:  int
    limit:   int

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
    total_tags:   int
    years:        list[tuple[int, int]]  # [(year, count), ...]
