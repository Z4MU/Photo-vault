"""
PhotoVault - ui/widgets.py
Widgets reutilizables de la galería.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from models import Photo

logger = logging.getLogger(__name__)


# ─── Widget de miniatura ──────────────────────────────────────────────────────

class PhotoThumbnail(QFrame):
    clicked  = pyqtSignal(int)
    selected = pyqtSignal(int, bool)   # photo_id, is_selected

    def __init__(self, photo: Photo, selectable: bool = False, parent=None):
        super().__init__(parent)
        self.photo_id   = photo.id
        self.selectable = selectable
        self._selected  = False
        self.setFixedSize(210, 230)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        img_container = QWidget()
        img_container.setFixedSize(200, 200)
        img_container.setStyleSheet("background:#13131F; border-radius:6px;")
        img_inner = QVBoxLayout(img_container)
        img_inner.setContentsMargins(0, 0, 0, 0)

        self.img_label = QLabel()
        self.img_label.setFixedSize(200, 200)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background:transparent;")
        img_inner.addWidget(self.img_label)

        if photo.is_video:
            play = QLabel("▶", img_container)
            play.setStyleSheet(
                "color:white;font-size:28px;background:rgba(0,0,0,0.55);"
                "border-radius:20px;padding:4px 8px;"
            )
            play.adjustSize()
            play.move((200 - play.width()) // 2, (200 - play.height()) // 2)
            if photo.duration_str:
                dur = QLabel(photo.duration_str, img_container)
                dur.setStyleSheet(
                    "color:white;font-size:10px;background:rgba(0,0,0,0.7);"
                    "border-radius:3px;padding:1px 5px;"
                )
                dur.adjustSize()
                dur.move(200 - dur.width() - 6, 200 - dur.height() - 6)

        # Checkbox de selección (visible solo en modo seleccionable)
        if selectable:
            self.chk = QCheckBox(img_container)
            self.chk.setStyleSheet("""
                QCheckBox::indicator { width:18px; height:18px; border-radius:4px; }
                QCheckBox::indicator:unchecked {
                    background:rgba(0,0,0,0.5); border:2px solid #888;
                }
                QCheckBox::indicator:checked {
                    background:#4A9EFF; border:2px solid #4A9EFF;
                }
            """)
            self.chk.move(6, 6)
            self.chk.stateChanged.connect(
                lambda s: self.selected.emit(self.photo_id, bool(s))
            )

        layout.addWidget(img_container)
        name_label = QLabel(photo.short_name)
        name_label.setTextFormat(Qt.TextFormat.PlainText)
        name_label.setStyleSheet("color:#8888AA;font-size:10px;")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

    def _update_style(self):
        if self._selected:
            self.setStyleSheet("""
                QFrame { background:#1E2E4E; border-radius:8px;
                         border:2px solid #4A9EFF; }
            """)
        else:
            self.setStyleSheet("""
                QFrame { background:#1E1E2E; border-radius:8px;
                         border:1px solid #2D2D3F; }
                QFrame:hover { border:1px solid #4A9EFF; background:#252538; }
            """)

    def set_pixmap(self, pix: QPixmap):
        self.img_label.setPixmap(pix)

    def set_selected(self, val: bool):
        self._selected = val
        self._update_style()
        if self.selectable and hasattr(self, "chk"):
            self.chk.blockSignals(True)
            self.chk.setChecked(val)
            self.chk.blockSignals(False)

    def mousePressEvent(self, event):
        if self.selectable:
            new_state = not self._selected
            self.set_selected(new_state)
            self.selected.emit(self.photo_id, new_state)
        else:
            self.clicked.emit(self.photo_id)
