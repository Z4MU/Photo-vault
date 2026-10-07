"""
PhotoVault - ui/viewer.py
Visor a pantalla completa: recorre las fotos de la galería (←/→), zoom,
rotar (solo la vista), GIF animados, video integrado y panel de información.
"""

import logging
from collections import OrderedDict
from typing import Protocol

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QKeySequence, QMovie, QPainter, QPixmap, QPixmapCache, QShortcut, QTransform
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
from models import Photo
from ui import system
from ui.gallery import thumb_key
from ui.images import is_animated, load_full_image
from ui.photo_info import InfoPanel
from ui.style import DARK_STYLE
from ui.video_player import VideoPlayer
from ui.workers import ImageLoadQueue, disconnect_all, retire_thread

logger = logging.getLogger(__name__)

IMAGE_CACHE = 3  # imágenes decodificadas en memoria (actual + vecinas)
ZOOM_MIN, ZOOM_MAX = 0.02, 16.0


class PhotoSequence(Protocol):
    """Lo que el visor necesita para recorrer fotos (GalleryModel lo cumple)."""

    def count(self) -> int: ...

    def photo_at(self, row: int) -> Photo | None: ...


class ListSequence:
    def __init__(self, photos: list[Photo]):
        self._photos = photos

    def count(self) -> int:
        return len(self._photos)

    def photo_at(self, row: int) -> Photo | None:
        return self._photos[row] if 0 <= row < len(self._photos) else None


# ─── Imagen con zoom y rotación ───────────────────────────────────────────────


class ImageView(QGraphicsView):
    """Imagen con zoom (rueda, +/-), arrastre para moverse y rotación de 90°."""

    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setStyleSheet("background:#000;")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # las flechas son del visor, no del scroll
        self._fit = True
        self._zoom = 1.0
        self._rotation = 0
        self._preview = False  # mostrando la miniatura mientras carga la foto real

    def set_image(self, img: QImage | QPixmap | None, preview: bool = False) -> None:
        """Muestra una imagen ajustada a la ventana. `preview`: miniatura provisional (se agranda)."""
        pix = QPixmap.fromImage(img) if isinstance(img, QImage) else (img or QPixmap())
        self._preview = preview
        self._item.setPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))
        self._item.setTransformOriginPoint(QRectF(pix.rect()).center())
        self.fit()

    def has_image(self) -> bool:
        return not self._item.pixmap().isNull()

    def is_preview(self) -> bool:
        return self._preview

    def reset(self) -> None:
        self._rotation = 0
        self._fit = True

    def _apply(self) -> None:
        t = QTransform()
        t.rotate(self._rotation)
        t.scale(self._zoom, self._zoom)
        self.setTransform(t)
        self.zoom_changed.emit(-1.0 if self._preview else self._zoom)

    def fit(self) -> None:
        self._fit = True
        pix = self._item.pixmap()
        if pix.isNull():
            return
        w, h = pix.width(), pix.height()
        if self._rotation % 180:
            w, h = h, w
        vp = self.viewport()
        vw, vh = (vp.width(), vp.height()) if vp is not None else (w, h)
        z = min(vw / w, vh / h)
        # La foto real no se agranda más allá del 100 %; la vista previa sí (es chica)
        self._zoom = z if self._preview else min(1.0, z)
        self._apply()
        self.centerOn(self._item)

    def is_fit(self) -> bool:
        return self._fit

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, z: float) -> None:
        self._fit = False
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, z))
        self._apply()

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self._zoom * factor)

    def rotate_by(self, degrees: int) -> None:
        self._rotation = (self._rotation + degrees) % 360
        if self._fit:
            self.fit()
        else:
            self._apply()

    def rotation(self) -> int:
        return self._rotation

    def wheelEvent(self, event):
        if event is not None and self.has_image():
            self.zoom_by(1.25 ** (event.angleDelta().y() / 120))

    def mouseDoubleClickEvent(self, event):
        if self._fit:
            self.set_zoom(1.0)
        else:
            self.fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit()


# ─── Visor ────────────────────────────────────────────────────────────────────

_BAR_BTN = (
    "QPushButton{background:transparent;color:#D0D0E8;border:1px solid transparent;"
    "border-radius:6px;padding:4px 9px;font-size:14px;}"
    "QPushButton:hover{border-color:#3A3A5A;background:#1E1E2E;}"
    "QPushButton:checked{color:#4A9EFF;border-color:#4A9EFF;}"
)

SHORTCUTS_HELP = [
    ("← / →, RePág / AvPág", "Foto anterior / siguiente"),
    ("Inicio / Fin", "Primera / última"),
    ("Rueda, + / −", "Zoom"),
    ("0  ·  1", "Ajustar a la ventana  ·  Tamaño real"),
    ("R  ·  Shift+R", "Rotar a la derecha / izquierda (solo la vista)"),
    ("I", "Panel de información y etiquetas"),
    ("F  ·  F11", "Pantalla completa"),
    ("Espacio  ·  M", "Video: reproducir / pausa  ·  silencio"),
    ("Shift+← / →", "Video: −5 s / +5 s"),
    ("Ctrl+E  ·  Ctrl+C  ·  Ctrl+O", "Mostrar en Explorador · Copiar ruta · Abrir con app"),
    ("Esc", "Cerrar el visor"),
]


class ViewerWindow(QDialog):
    """
    Muestra las fotos de `source` empezando en `row`. Al cerrar, `current_row`
    es la última vista (la galería la selecciona) y `tags_were_changed` dice
    si hay que recargar la galería.
    """

    def __init__(self, source: PhotoSequence, row: int, parent=None, fullscreen: bool = True):
        super().__init__(parent)
        self.setWindowTitle("PhotoVault — Visor")
        self.setStyleSheet(DARK_STYLE + "QDialog{background:#000;}")
        self.setMinimumSize(800, 560)
        self._source = source
        self.current_row = max(0, min(row, source.count() - 1))
        self.tags_were_changed = False
        self._photo: Photo | None = None
        self._image_key: str | None = None
        self._images: OrderedDict[str, QImage] = OrderedDict()
        self._movie: QMovie | None = None

        self._queue = ImageLoadQueue(lambda path: load_full_image(path, config.VIEWER_MAX_SIDE), workers=2)
        self._queue.loaded.connect(self._on_image_loaded)
        self._queue.start()

        self._build_ui()
        self._build_shortcuts()
        if fullscreen:
            self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.show_row(self.current_row)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        center = QVBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)

        self.stack = QStackedWidget()
        self.image_view = ImageView()
        self.image_view.zoom_changed.connect(self._update_zoom_label)
        self.video = VideoPlayer()
        self.movie_label = QLabel()
        self.movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.movie_label.setStyleSheet("background:#000;")
        self.message = QLabel("")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setStyleSheet("background:#000;color:#8888AA;font-size:14px;")
        for w in (self.image_view, self.video, self.movie_label, self.message):
            self.stack.addWidget(w)
        center.addWidget(self.stack, stretch=1)

        bar = QWidget()
        bar.setStyleSheet("background:#0D0D1A;border-top:1px solid #2D2D3F;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 8, 4)
        bl.setSpacing(4)

        def button(text: str, tip: str, slot, checkable: bool = False) -> QPushButton:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setStyleSheet(_BAR_BTN)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            bl.addWidget(b)
            return b

        button("◀", "Anterior (←)", self.prev)
        button("▶", "Siguiente (→)", self.next)
        self.counter_lbl = QLabel("")
        self.counter_lbl.setStyleSheet("color:#8888AA;font-size:12px;padding:0 8px;border:none;")
        bl.addWidget(self.counter_lbl)
        self.name_lbl = QLabel("")
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setStyleSheet("color:#D0D0E8;font-size:12px;border:none;")
        bl.addWidget(self.name_lbl, stretch=1)
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#8888AA;font-size:11px;border:none;padding:0 8px;")
        bl.addWidget(self.status_lbl)
        self.zoom_lbl = QLabel("")
        self.zoom_lbl.setStyleSheet("color:#8888AA;font-size:11px;border:none;min-width:44px;")
        bl.addWidget(self.zoom_lbl)
        button("⟲", "Rotar a la izquierda (Shift+R)", lambda: self.rotate(-90))
        button("⟳", "Rotar a la derecha (R)", lambda: self.rotate(90))
        button("⤢", "Ajustar a la ventana (0)", self.image_view.fit)
        button("1:1", "Tamaño real (1)", lambda: self.image_view.set_zoom(1.0))
        self.btn_info = button("ℹ", "Información y etiquetas (I)", self.toggle_info, checkable=True)
        button("📂", "Mostrar en el Explorador (Ctrl+E)", self.reveal)
        button("⛶", "Pantalla completa (F)", self.toggle_fullscreen)
        button("✕", "Cerrar (Esc)", self.close)
        center.addWidget(bar)
        root.addLayout(center, stretch=1)

        self.info = InfoPanel()
        self.info.tags_changed.connect(self._on_tags_changed)
        self.info.setVisible(False)
        root.addWidget(self.info)

    def _build_shortcuts(self) -> None:
        bindings = {
            "Left": self.prev,
            "PgUp": self.prev,
            "Right": self.next,
            "PgDown": self.next,
            "Home": lambda: self.show_row(0),
            "End": lambda: self.show_row(self._source.count() - 1),
            "+": lambda: self.image_view.zoom_by(1.25),
            "=": lambda: self.image_view.zoom_by(1.25),
            "-": lambda: self.image_view.zoom_by(0.8),
            "0": self.image_view.fit,
            "1": lambda: self.image_view.set_zoom(1.0),
            "R": lambda: self.rotate(90),
            "Shift+R": lambda: self.rotate(-90),
            "I": self.toggle_info,
            "F": self.toggle_fullscreen,
            "F11": self.toggle_fullscreen,
            "Space": self.video.toggle_play,
            "M": self.video.toggle_mute,
            "Shift+Left": lambda: self.video.seek_relative(-5000),
            "Shift+Right": lambda: self.video.seek_relative(5000),
            "Ctrl+E": self.reveal,
            "Ctrl+C": self.copy_path,
            "Ctrl+O": self.open_external,
        }
        for key, slot in bindings.items():
            QShortcut(QKeySequence(key), self, slot)

    # ── Navegación ────────────────────────────────────────────────────────────

    def photo(self) -> Photo | None:
        return self._photo

    def next(self) -> None:
        self.show_row(self.current_row + 1)

    def prev(self) -> None:
        self.show_row(self.current_row - 1)

    def show_row(self, row: int) -> None:
        total = self._source.count()
        if total == 0:
            self._show_message("No hay fotos para mostrar.")
            return
        row = max(0, min(row, total - 1))
        photo = self._source.photo_at(row)
        if photo is None:
            return
        if self._photo is not None and photo.id == self._photo.id and row == self.current_row:
            return
        self.current_row = row
        self._photo = photo
        self._stop_media()
        self.counter_lbl.setText(f"{row + 1:,} / {total:,}")
        self.name_lbl.setText(photo.filename)
        self.status_lbl.setText("")
        self.info.set_photo(photo)
        self.image_view.reset()

        if photo.is_video:
            self.stack.setCurrentWidget(self.video)
            self.video.load(photo.path)
            self.zoom_lbl.setText("")
        elif is_animated(photo.path):
            self._show_movie(photo.path)
        else:
            self._show_image(photo)
        self._preload_neighbors(row)

    def _image_key_of(self, photo: Photo) -> str:
        return f"img:{photo.id}:{photo.mtime}"

    def _show_image(self, photo: Photo) -> None:
        self.stack.setCurrentWidget(self.image_view)
        key = self._image_key_of(photo)
        self._image_key = key
        img = self._images.get(key)
        if img is not None:
            self._images.move_to_end(key)
            self.image_view.set_image(img)
            self.info.set_image_size(img.width(), img.height())
            return
        # Mientras carga: la miniatura que ya está en memoria (aparece al instante)
        preview = QPixmapCache.find(thumb_key(photo, config.THUMB_SIZE_LARGE)) or QPixmapCache.find(
            thumb_key(photo, config.THUMB_SIZE_GALLERY)
        )
        self.image_view.set_image(preview, preview=True)
        self.status_lbl.setText("Cargando…")
        self._queue.request(key, photo.path)

    def _preload_neighbors(self, row: int) -> None:
        # LIFO: se pide primero la anterior y luego la siguiente; la actual ya va adelante
        for r in (row - 1, row + 1):
            p = self._source.photo_at(r)
            if p is not None and not p.is_video and self._image_key_of(p) not in self._images:
                self._queue.request(self._image_key_of(p), p.path)
        if self._image_key is not None and self._image_key not in self._images and self._photo is not None:
            self._queue.request(self._image_key, self._photo.path)  # vuelve al frente de la cola

    def _on_image_loaded(self, key: str, img: QImage) -> None:
        if not img.isNull():
            self._images[key] = img
            while len(self._images) > IMAGE_CACHE:
                self._images.popitem(last=False)
        if key != self._image_key or self.stack.currentWidget() is not self.image_view:
            return
        self.status_lbl.setText("")
        if img.isNull():
            path = self._photo.path if self._photo else ""
            self._show_message(f"No se pudo abrir la imagen.\n\n{path}\n\n¿El disco está conectado?")
        else:
            self.image_view.set_image(img)
            self.info.set_image_size(img.width(), img.height())

    def _show_movie(self, path: str) -> None:
        self._movie = QMovie(path)
        self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self._movie.jumpToFrame(0)
        size = self._movie.currentImage().size()
        avail = self.stack.size()
        if size.width() > avail.width() or size.height() > avail.height():
            size.scale(avail, Qt.AspectRatioMode.KeepAspectRatio)
            self._movie.setScaledSize(size)
        self.movie_label.setMovie(self._movie)
        self.stack.setCurrentWidget(self.movie_label)
        self.zoom_lbl.setText("GIF")
        self._movie.start()

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)
        self.zoom_lbl.setText("")

    def _stop_media(self) -> None:
        self.video.stop()
        if self._movie is not None:
            self._movie.stop()
            self.movie_label.setMovie(None)
            self._movie = None
        self._image_key = None

    # ── Acciones ──────────────────────────────────────────────────────────────

    def rotate(self, degrees: int) -> None:
        if self.stack.currentWidget() is self.image_view:
            self.image_view.rotate_by(degrees)

    def _update_zoom_label(self, z: float) -> None:
        self.zoom_lbl.setText(f"{z * 100:.0f} %" if z >= 0 else "")

    def toggle_info(self) -> None:
        self.info.setVisible(not self.info.isVisible())
        self.btn_info.setChecked(self.info.isVisible())

    def toggle_fullscreen(self) -> None:
        self.setWindowState(self.windowState() ^ Qt.WindowState.WindowFullScreen)

    def reveal(self) -> None:
        if self._photo is not None:
            system.reveal_in_explorer(self._photo.path)

    def copy_path(self) -> None:
        if self._photo is not None:
            system.copy_paths([self._photo.path])
            self.status_lbl.setText("Ruta copiada")

    def open_external(self) -> None:
        if self._photo is not None:
            self.video.stop()
            system.open_external(self._photo.path)

    def _on_tags_changed(self) -> None:
        self.tags_were_changed = True

    def done(self, result: int) -> None:
        # Se llama al cerrar por cualquier vía (Esc, ✕, botón de la ventana)
        self._stop_media()
        self.info.shutdown()
        disconnect_all(self._queue.loaded)
        retire_thread(self._queue)
        super().done(result)
