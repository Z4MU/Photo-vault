"""
PhotoVault - ui/gallery.py
Galería virtualizada: QListView + modelo que carga las fotos por tramos y
las miniaturas solo de lo que se ve. Sin páginas: un solo scroll para toda
la colección (172k fotos) con barra de desplazamiento real.
"""

import logging
from collections import OrderedDict

from PyQt6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QPixmapCache, QPolygonF, QRegion
from PyQt6.QtWidgets import QAbstractItemView, QListView, QScrollBar, QStyle, QStyledItemDelegate

import config
import services
from models import Photo
from ui.workers import ImageLoadQueue

logger = logging.getLogger(__name__)

PhotoRole = Qt.ItemDataRole.UserRole + 1
CHUNK_SIZE = 500  # fotos por consulta a la DB
MAX_CHUNKS = 40  # tramos en memoria (20.000 fotos)
MAX_DRAG = 500  # arrastrar más archivos que esto a otra app no tiene sentido


def thumb_source_size(display: int) -> int:
    """Qué miniatura del caché usar para mostrarla a `display` píxeles."""
    return config.THUMB_SIZE_GALLERY if display <= config.THUMB_SIZE_GALLERY + 20 else config.THUMB_SIZE_LARGE


def thumb_key(photo: Photo, size: int) -> str:
    return f"thumb:{photo.id}:{photo.mtime}:{size}"


def format_size(n: int | None) -> str:
    if not n:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


MONTHS = ("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre").split()


def format_date(year: int | None, month: int | None) -> str:
    if not year:
        return "Sin fecha"
    return f"{MONTHS[month - 1]} {year}" if month and 1 <= month <= 12 else str(year)


# ─── Modelo ───────────────────────────────────────────────────────────────────


class GalleryModel(QAbstractListModel):
    """
    Una fila por foto de la consulta actual. Solo conoce el total; las fotos se
    leen de la DB por tramos de CHUNK_SIZE cuando la vista pide una fila, y se
    guardan los últimos MAX_CHUNKS. Las miniaturas se piden a la cola cuando
    la vista las pinta (es decir, solo las visibles).
    """

    def __init__(self, queue: ImageLoadQueue | None = None, parent=None):
        super().__init__(parent)
        self._query = services.GalleryQuery()
        self._total = 0
        self._chunks: OrderedDict[int, list[Photo]] = OrderedDict()
        self._rows: dict[int, int] = {}  # photo_id → fila (de los tramos cargados)
        self._requested: set[str] = set()
        self._failed: set[str] = set()
        self._thumb_size = config.THUMB_SIZE_GALLERY
        self._queue = queue
        if queue is not None:
            queue.loaded.connect(self._on_thumb_loaded)

    # ── Consulta ──────────────────────────────────────────────────────────────

    def query(self) -> services.GalleryQuery:
        return self._query

    def set_query(self, q: services.GalleryQuery) -> None:
        self.beginResetModel()
        self._query = q
        self._chunks.clear()
        self._rows.clear()
        self._requested.clear()
        self._failed.clear()
        if self._queue is not None:
            self._queue.clear()
        self._total = services.count_gallery(q)
        self.endResetModel()

    def refresh(self) -> None:
        self.set_query(self._query)

    def count(self) -> int:
        return self._total

    # ── Filas ─────────────────────────────────────────────────────────────────

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else self._total

    def photo_at(self, row: int) -> Photo | None:
        if not 0 <= row < self._total:
            return None
        ci = row // CHUNK_SIZE
        chunk = self._chunks.get(ci)
        if chunk is None:
            chunk = self._load_chunk(ci)
        else:
            self._chunks.move_to_end(ci)
        i = row - ci * CHUNK_SIZE
        return chunk[i] if i < len(chunk) else None

    def _load_chunk(self, ci: int) -> list[Photo]:
        photos = services.get_gallery_chunk(self._query, ci * CHUNK_SIZE, CHUNK_SIZE)
        self._chunks[ci] = photos
        for i, p in enumerate(photos):
            self._rows[p.id] = ci * CHUNK_SIZE + i
        while len(self._chunks) > MAX_CHUNKS:
            _old, evicted = self._chunks.popitem(last=False)
            for p in evicted:
                self._rows.pop(p.id, None)
        return photos

    def row_of(self, photo_id: int) -> int | None:
        """Fila de una foto si está en un tramo cargado."""
        return self._rows.get(photo_id)

    def ids_in_ranges(self, ranges: list[tuple[int, int]]) -> list[int]:
        """Ids de las filas [inicio, fin] (incluido); consulta la DB para lo que no está cargado."""
        ids: list[int] = []
        for start, end in ranges:
            n = end - start + 1
            if n <= CHUNK_SIZE:
                ids.extend(p.id for r in range(start, end + 1) if (p := self.photo_at(r)) is not None)
            else:
                ids.extend(services.get_gallery_ids(self._query, offset=start, limit=n))
        return ids

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        photo = self.photo_at(index.row())
        if photo is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return photo.filename
        if role == PhotoRole:
            return photo
        if role == Qt.ItemDataRole.DecorationRole:
            return self.thumbnail(photo)
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = [photo.filename, format_date(photo.year, photo.month)]
            if photo.width and photo.height:
                parts.append(f"{photo.width}×{photo.height}")
            parts.append(format_size(photo.filesize))
            if photo.rating or photo.favorite:
                parts.append(" ".join(x for x in (photo.stars, "♥ favorita" if photo.favorite else "") if x))
            if photo.note:
                parts.append(f"📝 {photo.note[:120]}")
            return "\n".join(parts)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled

    # ── Arrastrar a otras apps ────────────────────────────────────────────────

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:
        md = QMimeData()
        photos = [p for i in indexes if (p := self.photo_at(i.row())) is not None]
        md.setUrls([QUrl.fromLocalFile(p.path) for p in photos])
        md.setText("\n".join(p.path for p in photos))
        return md

    # ── Miniaturas ────────────────────────────────────────────────────────────

    def set_thumb_size(self, size: int) -> None:
        """Tamaño de miniatura del caché que se usa (200 o 480)."""
        if size == self._thumb_size:
            return
        self._thumb_size = size
        self._requested.clear()
        if self._queue is not None:
            self._queue.clear()
        if self._total:
            self.dataChanged.emit(
                self.index(0), self.index(self._total - 1), [Qt.ItemDataRole.DecorationRole]
            )

    def thumbnail(self, photo: Photo) -> QPixmap | None:
        key = thumb_key(photo, self._thumb_size)
        pix = QPixmapCache.find(key)
        if pix is not None:
            return pix
        if self._queue is not None and key not in self._requested and key not in self._failed:
            self._requested.add(key)
            self._requested.difference_update(self._queue.request(key, (photo, self._thumb_size)))
        return None

    def thumbnail_failed(self, photo: Photo) -> bool:
        return thumb_key(photo, self._thumb_size) in self._failed

    def _on_thumb_loaded(self, key: str, img: QImage) -> None:
        if key not in self._requested:
            return  # De una consulta o tamaño anterior
        self._requested.discard(key)
        if img.isNull():
            self._failed.add(key)
        else:
            QPixmapCache.insert(key, QPixmap.fromImage(img))  # QPixmap solo en el hilo de la UI
        row = self._rows.get(int(key.split(":")[1]))
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


# ─── Pintado de cada celda ────────────────────────────────────────────────────

PAD = 6
LABEL_H = 20


class GalleryDelegate(QStyledItemDelegate):
    """Dibuja la celda (miniatura, nombre, marca de video, selección) sin crear widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumb = services.THUMB_DISPLAY_DEFAULT
        self._name_font = QFont()
        self._name_font.setPixelSize(10)
        self._badge_font = QFont()
        self._badge_font.setPixelSize(10)
        self._icon_font = QFont()
        self._icon_font.setPixelSize(26)

    def set_thumb_size(self, size: int) -> None:
        self.thumb = size

    def cell_size(self) -> QSize:
        return QSize(self.thumb + PAD * 2 + 6, self.thumb + PAD * 2 + LABEL_H + 6)

    def sizeHint(self, option, index) -> QSize:
        return self.cell_size()

    def _scaled(self, pix: QPixmap) -> QPixmap:
        if pix.width() <= self.thumb and pix.height() <= self.thumb:
            return pix
        key = f"scaled:{pix.cacheKey()}:{self.thumb}"
        scaled = QPixmapCache.find(key)
        if scaled is None:
            scaled = pix.scaled(
                self.thumb,
                self.thumb,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            QPixmapCache.insert(key, scaled)
        return scaled

    def paint(self, painter: QPainter | None, option, index: QModelIndex) -> None:
        if painter is None:
            return
        photo: Photo | None = index.data(PhotoRole)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        c = config.COLORS
        cell = option.rect.adjusted(3, 3, -3, -3)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = "#1E2E4E" if selected else "#252538" if hover else c["panel_alt"]
        border = c["accent"] if selected or hover else c["border"]
        painter.setPen(QPen(QColor(border), 2 if selected else 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(cell, 8, 8)

        img_rect = QRect(cell.x() + PAD, cell.y() + PAD, self.thumb, self.thumb)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c["panel"]))
        painter.drawRoundedRect(img_rect, 6, 6)

        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pix, QPixmap) and not pix.isNull():
            pix = self._scaled(pix)
            x = img_rect.x() + (img_rect.width() - pix.width()) // 2
            y = img_rect.y() + (img_rect.height() - pix.height()) // 2
            painter.drawPixmap(x, y, pix)
        else:
            painter.setFont(self._icon_font)
            painter.setPen(QColor(c["border_alt"]))
            model = index.model()
            failed = isinstance(model, GalleryModel) and photo is not None and model.thumbnail_failed(photo)
            painter.drawText(img_rect, Qt.AlignmentFlag.AlignCenter, "⚠" if failed else "🖼")

        if photo is not None and photo.is_video:
            self._paint_video_badge(painter, img_rect, photo)
        if photo is not None:
            self._paint_marks(painter, img_rect, photo)

        if photo is not None:
            painter.setFont(self._name_font)
            painter.setPen(QColor(c["text_dim"] if not selected else c["text"]))
            name_rect = QRect(cell.x() + PAD, img_rect.bottom() + 3, cell.width() - PAD * 2, LABEL_H - 2)
            name = painter.fontMetrics().elidedText(
                photo.filename, Qt.TextElideMode.ElideMiddle, name_rect.width()
            )
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, name)
        painter.restore()

    def _paint_marks(self, painter: QPainter, img_rect: QRect, photo: Photo) -> None:
        """
        🔒 oculta y ♥ favorita (arriba a la derecha), 📝 nota (arriba a la izquierda),
        ★ valoración (abajo a la izquierda).
        """
        painter.setFont(self._badge_font)
        fm = painter.fontMetrics()

        def pill(x: int, y: int, text: str, color: str, align_right: bool = False) -> int:
            w = fm.horizontalAdvance(text) + 10
            rect = QRect(x - w if align_right else x, y, w, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor(color))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            return w

        right = img_rect.right() - 4
        if photo.hidden:  # solo se ve con el contenido oculto desbloqueado
            right -= pill(right, img_rect.top() + 4, "🔒", config.COLORS["warning"], align_right=True) + 4
        if photo.favorite:
            pill(right, img_rect.top() + 4, "♥", "#FF4A6A", align_right=True)
        if photo.note:
            pill(img_rect.left() + 4, img_rect.top() + 4, "📝", "#D0D0E8")
        if photo.rating:
            stars = "★" * photo.rating if self.thumb >= 140 else f"★{photo.rating}"
            pill(img_rect.left() + 4, img_rect.bottom() - 20, stars, config.COLORS["warning"])

    def _paint_video_badge(self, painter: QPainter, img_rect: QRect, photo: Photo) -> None:
        r = 18 if self.thumb >= 140 else 12
        center = img_rect.center()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawEllipse(center, r, r)
        painter.setBrush(QColor("white"))
        s = r * 0.5
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center.x() - s * 0.7, center.y() - s),
                    QPointF(center.x() - s * 0.7, center.y() + s),
                    QPointF(center.x() + s, center.y()),
                ]
            )
        )
        if photo.duration_str:
            painter.setFont(self._badge_font)
            fm = painter.fontMetrics()
            w = fm.horizontalAdvance(photo.duration_str) + 10
            badge = QRect(img_rect.right() - w - 5, img_rect.bottom() - 20, w, 16)
            painter.setBrush(QColor(0, 0, 0, 180))
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, photo.duration_str)


# ─── Vista ────────────────────────────────────────────────────────────────────


class GalleryView(QListView):
    """
    Cuadrícula con scroll continuo. Selección como en el Explorador: clic,
    Ctrl+clic, Shift+clic (rangos), Ctrl+A; doble clic o Enter abre el visor.
    Arrastrar la selección a otra app copia los archivos.
    """

    open_requested = pyqtSignal(int)  # fila
    drag_refused = pyqtSignal(int)  # n.º de fotos seleccionadas (demasiadas)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)  # Clave: no pregunta el tamaño de cada una de las 172k filas
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setStyleSheet(f"QListView{{background:{config.COLORS['bg']};border:none;}}")
        self.doubleClicked.connect(lambda idx: self.open_requested.emit(idx.row()))

    def vbar(self) -> QScrollBar:
        bar = self.verticalScrollBar()
        assert bar is not None
        return bar

    def apply_cell_size(self, size: QSize) -> None:
        self.setGridSize(size)
        self.vbar().setSingleStep(max(20, size.height() // 3))

    # ── Selección ─────────────────────────────────────────────────────────────

    def selected_ranges(self) -> list[tuple[int, int]]:
        """Filas seleccionadas como rangos [inicio, fin], ordenados y sin solaparse."""
        sm = self.selectionModel()
        if sm is None:
            return []
        sel = sm.selection()
        rows = sorted((sel[i].top(), sel[i].bottom()) for i in range(len(sel)))
        merged: list[tuple[int, int]] = []
        for start, end in rows:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def visualRegionForSelection(self, selection):
        # Qt calcula el rectángulo de CADA fila seleccionada (1,2 s con Ctrl+A sobre
        # 172k fotos). Basta con repintar lo visible: son unas decenas de celdas.
        viewport = self.viewport()
        return QRegion(viewport.rect()) if viewport is not None else QRegion()

    def selected_count(self) -> int:
        return sum(end - start + 1 for start, end in self.selected_ranges())

    def select_row(self, row: int) -> None:
        model = self.model()
        sm = self.selectionModel()
        if model is None or sm is None or not 0 <= row < model.rowCount():
            return
        idx = model.index(row, 0)
        sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)

    def select_rows(self, start: int, end: int) -> None:
        model = self.model()
        sm = self.selectionModel()
        if model is None or sm is None:
            return
        sel = QItemSelection(model.index(start, 0), model.index(end, 0))
        sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def scroll_to_row(self, row: int) -> None:
        model = self.model()
        if model is not None and 0 <= row < model.rowCount():
            self.scrollTo(model.index(row, 0), QAbstractItemView.ScrollHint.PositionAtTop)

    def first_visible_row(self) -> int | None:
        idx = self.indexAt(QPoint(PAD + 4, PAD + 4))
        return idx.row() if idx.isValid() else None

    # ── Teclado y arrastre ────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            idx = self.currentIndex()
            if idx.isValid():
                self.open_requested.emit(idx.row())
                return
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.clearSelection()
            return
        super().keyPressEvent(event)

    def startDrag(self, supportedActions):
        n = self.selected_count()
        if n > MAX_DRAG:
            self.drag_refused.emit(n)
            return
        super().startDrag(supportedActions)
