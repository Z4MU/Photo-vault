"""
PhotoVault - ui/photo_info.py
Panel de información del visor: datos del archivo, cámara (EXIF) y etiquetas.
"""

import logging
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFormLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

import services
from models import Photo
from ui.dialogs.photo import TagEditor
from ui.gallery import format_date, format_size
from ui.workers import TaskWorker, disconnect_all, retire_thread

logger = logging.getLogger(__name__)


def _value_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)  # nombres y rutas pueden traer < o &
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setStyleSheet("color:#D0D0E8;font-size:12px;")
    return lbl


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#4A9EFF;font-size:11px;font-weight:bold;letter-spacing:1px;margin-top:8px;")
    return lbl


def basic_info(photo: Photo) -> list[tuple[str, str]]:
    """Lo que se sabe sin abrir el archivo (viene de la DB)."""
    rows = [("Nombre", photo.filename), ("Fecha", format_date(photo.year, photo.month))]
    if photo.width and photo.height:
        mp = photo.width * photo.height / 1_000_000
        rows.append(
            ("Resolución", f"{photo.width} × {photo.height}" + (f"  ({mp:.1f} MP)" if mp >= 0.1 else ""))
        )
    rows.append(("Tamaño", format_size(photo.filesize)))
    if photo.is_video and photo.duration_str:
        rows.append(("Duración", photo.duration_str))
    rows.append(("Carpeta", os.path.dirname(photo.path)))
    return rows


class InfoPanel(QWidget):
    """Panel lateral del visor. Los datos EXIF se leen en un hilo (el disco puede ser lento)."""

    tags_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        self.setStyleSheet("background:#13131F;")
        self._photo: Photo | None = None
        self._worker: TaskWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_box = QScrollArea()
        self.scroll_box.setWidgetResizable(True)
        self.scroll_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_box.setStyleSheet("QScrollArea{border:none;border-left:1px solid #2D2D3F;}")
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        lay.addWidget(_section("ARCHIVO"))
        self.file_form = QFormLayout()
        self.file_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.file_form.setVerticalSpacing(4)
        lay.addLayout(self.file_form)

        self.camera_title = _section("CÁMARA")
        lay.addWidget(self.camera_title)
        self.camera_form = QFormLayout()
        self.camera_form.setVerticalSpacing(4)
        lay.addLayout(self.camera_form)

        lay.addWidget(_section("ETIQUETAS"))
        self.tag_editor = TagEditor()
        self.tag_editor.tags_changed.connect(self.tags_changed)
        lay.addWidget(self.tag_editor)
        lay.addStretch()
        self.scroll_box.setWidget(inner)
        outer.addWidget(self.scroll_box)

    @staticmethod
    def _fill(form: QFormLayout, rows: list[tuple[str, str]]) -> None:
        while form.rowCount():
            form.removeRow(0)
        for key, value in rows:
            key_lbl = QLabel(key)
            key_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
            form.addRow(key_lbl, _value_label(value))

    def form_values(self) -> dict[str, str]:
        """{etiqueta: valor} mostrados (archivo + cámara). Para tests."""
        out: dict[str, str] = {}
        for form in (self.file_form, self.camera_form):
            for r in range(form.rowCount()):
                k = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
                v = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
                kw = k.widget() if k is not None else None
                vw = v.widget() if v is not None else None
                if isinstance(kw, QLabel) and isinstance(vw, QLabel):
                    out[kw.text()] = vw.text()
        return out

    def set_photo(self, photo: Photo | None) -> None:
        self._photo = photo
        self._retire_worker()
        self._fill(self.camera_form, [])
        self.camera_title.setVisible(False)
        if photo is None:
            self._fill(self.file_form, [])
            self.tag_editor.set_photo(None)
            return
        self._fill(self.file_form, basic_info(photo))
        self.tag_editor.set_photo(photo.id)
        if not photo.is_video and self.isVisible():
            self._load_details(photo)

    def showEvent(self, event):
        super().showEvent(event)
        # Si estaba oculto al cambiar de foto, los datos de cámara se leen al mostrarlo
        if self._photo is not None and self._worker is None and not self.camera_form.rowCount():
            if not self._photo.is_video:
                self._load_details(self._photo)

    def set_image_size(self, width: int, height: int) -> None:
        """La DB no tenía la resolución (p. ej. HEIC indexados sin pillow-heif): la de la imagen cargada."""
        if self._photo is None or (self._photo.width and self._photo.height):
            return
        rows = basic_info(self._photo)
        rows.insert(2, ("Resolución", f"{width} × {height}"))
        self._fill(self.file_form, rows)

    def _load_details(self, photo: Photo) -> None:
        w = TaskWorker(lambda progress_callback=None, should_stop=None: services.get_photo_details(photo))
        w.completed.connect(lambda details, pid=photo.id: self._on_details(pid, details))
        self._worker = w
        w.start()

    def _on_details(self, photo_id: int, details: dict[str, str]) -> None:
        # El hilo aún no terminó del todo (cierra su conexión): retire_thread lo mantiene vivo
        retire_thread(self._worker)
        self._worker = None
        if self._photo is None or self._photo.id != photo_id:
            return
        self._fill(self.camera_form, list(details.items()))
        self.camera_title.setVisible(bool(details))

    def _retire_worker(self) -> None:
        if self._worker is not None:
            disconnect_all(self._worker.completed, self._worker.error)
            retire_thread(self._worker)
            self._worker = None

    def shutdown(self) -> None:
        self._retire_worker()
