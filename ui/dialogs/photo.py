"""
PhotoVault - ui/dialogs/photo.py
Detalle de una foto y etiquetado en lote.
"""

import html
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import services
import thumbnail_cache
from models import Photo, Tag
from ui.images import load_preview_pixmap
from ui.style import DARK_STYLE

logger = logging.getLogger(__name__)


# ─── Dialog: Ver/editar foto ──────────────────────────────────────────────────

class PhotoDetailDialog(QDialog):
    tags_changed = pyqtSignal()

    def __init__(self, photo: Photo, parent=None):
        super().__init__(parent)
        self.photo = photo
        self.setWindowTitle("Detalle")
        self.setMinimumSize(900, 600)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._load_tags()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        if self.photo.is_video:
            left = QWidget(); left.setMinimumWidth(500)
            ll   = QVBoxLayout(left)
            ll.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb = thumbnail_cache.get_video_thumbnail(self.photo.path, size=480)
            lbl   = QLabel(); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if thumb:
                pix = QPixmap(); pix.loadFromData(thumb); lbl.setPixmap(pix)
            else:
                lbl.setText("🎬"); lbl.setStyleSheet("font-size:64px;")
            ll.addWidget(lbl)
            btn = QPushButton("▶  Reproducir")
            btn.setStyleSheet(
                "QPushButton{background:#4A9EFF22;color:#4A9EFF;"
                "border:1px solid #4A9EFF;border-radius:8px;padding:10px 20px;font-size:14px;}"
                "QPushButton:hover{background:#4A9EFF44;}"
            )
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.photo.path)))
            ll.addWidget(btn)
            layout.addWidget(left)
        else:
            img = QLabel(); img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img.setMinimumWidth(500)
            pix = load_preview_pixmap(self.photo.path, 560)
            if pix is not None:
                img.setPixmap(pix)
            else:
                img.setText("No se pudo cargar la imagen")
            layout.addWidget(img)

        right = QVBoxLayout(); right.setSpacing(10)
        right.addWidget(QLabel(f"<b>{html.escape(Path(self.photo.path).name)}</b>"))
        right.addWidget(QLabel("Etiquetas:"))

        self.tags_container = QWidget()
        self.tags_layout    = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea(); scroll.setWidget(self.tags_container)
        scroll.setWidgetResizable(True); scroll.setFixedHeight(80)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(scroll)

        right.addWidget(QLabel("Agregar etiqueta:"))
        row = QHBoxLayout()
        self.tag_combo = QComboBox(); self.tag_combo.setEditable(True)
        self.tag_combo.setPlaceholderText("Buscar o nueva etiqueta…")
        row.addWidget(self.tag_combo)
        btn_add = QPushButton("＋ Agregar"); btn_add.clicked.connect(self._add_tag)
        row.addWidget(btn_add)
        right.addLayout(row)
        right.addStretch()
        layout.addLayout(right)

    def _load_tags(self):
        for i in reversed(range(self.tags_layout.count())):
            self.tags_layout.itemAt(i).widget().deleteLater()
        for tag in services.get_photo_tags(self.photo.id):
            self.tags_layout.addWidget(self._chip(tag))
        self.tag_combo.clear()
        for t in services.get_all_tags():
            self.tag_combo.addItem(t.name, userData=t.id)

    def _chip(self, tag: Tag) -> QPushButton:
        btn = QPushButton(f"{tag.name}  ✕")
        btn.setStyleSheet(
            f"QPushButton{{background:{tag.color}33;color:{tag.color};"
            f"border:1px solid {tag.color};border-radius:10px;padding:2px 8px;font-size:11px;}}"
            f"QPushButton:hover{{background:{tag.color}66;}}"
        )
        btn.clicked.connect(lambda _, tid=tag.id: self._remove_tag(tid))
        return btn

    def _add_tag(self):
        text = self.tag_combo.currentText().strip().lower()
        if text:
            services.add_tag(self.photo.id, text)
            self._load_tags(); self.tags_changed.emit()

    def _remove_tag(self, tag_id: int):
        services.remove_tag(self.photo.id, tag_id)
        self._load_tags(); self.tags_changed.emit()


# ─── Dialog: Etiquetado en lote ───────────────────────────────────────────────

class BulkTagDialog(QDialog):
    """Agrega o quita una etiqueta a todas las fotos seleccionadas."""

    def __init__(self, photo_ids: list[int], parent=None):
        super().__init__(parent)
        self.photo_ids = photo_ids
        self.setWindowTitle(f"Etiquetar {len(photo_ids)} fotos")
        self.setFixedSize(420, 200)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            f"<b>{len(self.photo_ids)} fotos seleccionadas</b><br>"
            "<span style='color:#888;font-size:11px;'>"
            "Escribe o elige una etiqueta y aplica a todas.</span>"
        ))

        self.combo = QComboBox(); self.combo.setEditable(True)
        self.combo.setPlaceholderText("Etiqueta a aplicar…")
        for t in services.get_all_tags():
            self.combo.addItem(t.name, userData=t.id)
        layout.addWidget(self.combo)

        row = QHBoxLayout()
        btn_add = QPushButton("＋ Agregar a todas")
        btn_add.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_add.clicked.connect(self._add)

        btn_rem = QPushButton("✕ Quitar de todas")
        btn_rem.setStyleSheet("background:#FF4A4A22;color:#FF4A4A;border:1px solid #FF4A4A;")
        btn_rem.clicked.connect(self._remove)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)

        row.addWidget(btn_add); row.addWidget(btn_rem); row.addWidget(btn_cancel)
        layout.addLayout(row)

    def _add(self):
        name = self.combo.currentText().strip().lower()
        if not name:
            return
        n = services.bulk_add_tag(self.photo_ids, name)
        QMessageBox.information(self, "Listo", f"Etiqueta '{name}' agregada a {n} fotos.")
        self.accept()

    def _remove(self):
        name = self.combo.currentText().strip().lower()
        if not name:
            return
        all_tags = services.get_all_tags()
        tag = next((t for t in all_tags if t.name == name), None)
        if not tag:
            QMessageBox.warning(self, "No encontrada",
                                f"La etiqueta '{name}' no existe.")
            return
        n = services.bulk_remove_tag(self.photo_ids, tag.id)
        QMessageBox.information(self, "Listo", f"Etiqueta '{name}' quitada de {n} fotos.")
        self.accept()
