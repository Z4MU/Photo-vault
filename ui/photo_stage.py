"""
PhotoVault - ui/photo_stage.py
Muestra una foto (o video, sin sonido) ajustada al espacio disponible, a la
resolución de la pantalla y con las siguientes ya precargadas en un hilo.
Lo usan los modos de etiquetado rápido.
"""

import logging
from collections import OrderedDict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap, QPixmapCache
from PyQt6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

import config
from models import Photo
from ui.gallery import thumb_key
from ui.images import load_full_image
from ui.video_player import VideoPlayer
from ui.workers import ImageLoadQueue, disconnect_all, retire_on_destroy, retire_thread

logger = logging.getLogger(__name__)

STAGE_CACHE = 8  # imágenes decodificadas en memoria (actual + precargadas)
MAX_STAGE_SIDE = 2560  # para etiquetar no hace falta más (fotos de 48 MP decodifican rápido igual)


def screen_side() -> int:
    """Lado mayor de la pantalla en píxeles reales (#54: imagen a resolución de pantalla)."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1600
    size = screen.size()
    return min(MAX_STAGE_SIDE, int(max(size.width(), size.height()) * screen.devicePixelRatio()))


class PhotoStage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._side = screen_side()
        self._images: OrderedDict[str, QImage] = OrderedDict()
        self._photo: Photo | None = None
        self._key: str | None = None
        self._pixmap: QPixmap | None = None
        self._full = False  # la imagen mostrada es la decodificada (no la miniatura provisional)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(200, 200)
        self.image_label.setStyleSheet("background:#000;")
        self.video = VideoPlayer()
        self.video.set_muted(True)  # para decidir alcanza con verlo; el sonido molesta
        self.message = QLabel("")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setStyleSheet("background:#000;color:#8888AA;font-size:15px;")
        for w in (self.image_label, self.video, self.message):
            self.stack.addWidget(w)
        lay.addWidget(self.stack)

        self._queue = ImageLoadQueue(lambda path: load_full_image(path, self._side), workers=2)
        self._queue.loaded.connect(self._on_loaded)
        self._queue.start()
        retire_on_destroy(self, self._queue)

    @staticmethod
    def _key_of(photo: Photo) -> str:
        return f"stage:{photo.id}:{photo.mtime}"

    def photo(self) -> Photo | None:
        return self._photo

    def is_loaded(self) -> bool:
        """La foto actual ya se ve a resolución completa (o es un video)."""
        return self._photo is not None and (self._photo.is_video or self._full)

    # ── Mostrar ───────────────────────────────────────────────────────────────

    def show_photo(self, photo: Photo | None, preload: list[Photo] | None = None) -> None:
        """Muestra `photo` y deja `preload` decodificándose (la primera de la lista, antes)."""
        self.video.stop()
        self._photo = photo
        self._full = False
        if photo is None:
            self.show_message("")
            return
        if photo.is_video:
            self._key = None
            self.stack.setCurrentWidget(self.video)
            self.video.load(photo.path, autoplay=True)
        else:
            key = self._key_of(photo)
            self._key = key
            self.stack.setCurrentWidget(self.image_label)
            img = self._images.get(key)
            if img is not None:
                self._images.move_to_end(key)
                self._set_image(QPixmap.fromImage(img), full=True)
            else:
                preview = QPixmapCache.find(thumb_key(photo, config.THUMB_SIZE_LARGE)) or QPixmapCache.find(
                    thumb_key(photo, config.THUMB_SIZE_GALLERY)
                )
                self._set_image(preview, full=False)
        # LIFO: se piden de la más lejana a la más cercana; la actual va última (= primera)
        for p in reversed(preload or []):
            if not p.is_video and self._key_of(p) not in self._images:
                self._queue.request(self._key_of(p), p.path)
        if self._key is not None and self._key not in self._images:
            self._queue.request(self._key, photo.path)

    def show_message(self, text: str) -> None:
        self.video.stop()
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    def _set_image(self, pix: QPixmap | None, full: bool) -> None:
        self._pixmap = pix
        self._full = full and pix is not None
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            self.image_label.clear()
            return
        size = self.image_label.size()
        pix = self._pixmap
        if pix.width() > size.width() or pix.height() > size.height() or not self._full:
            pix = pix.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        self.image_label.setPixmap(pix)

    def _on_loaded(self, key: str, img: QImage) -> None:
        if not img.isNull():
            self._images[key] = img
            while len(self._images) > STAGE_CACHE:
                self._images.popitem(last=False)
        if key != self._key:
            return
        if img.isNull():
            path = self._photo.path if self._photo else ""
            self.show_message(f"No se pudo abrir la imagen.\n\n{path}\n\n¿El disco está conectado?")
        else:
            self._set_image(QPixmap.fromImage(img), full=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def shutdown(self) -> None:
        """Al cerrar la ventana: parar el video y la cola sin bloquear."""
        self.video.stop()
        disconnect_all(self._queue.loaded)
        retire_thread(self._queue)
