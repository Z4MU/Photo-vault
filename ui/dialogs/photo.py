"""
PhotoVault - ui/dialogs/photo.py
Editor de etiquetas de una foto (panel del visor) y etiquetado en lote, con
etiquetas sugeridas por carpeta (smart.suggest_tags, fase 10).
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import services
import smart
from models import Tag
from ui.style import DARK_STYLE
from ui.widgets import clear_layout

logger = logging.getLogger(__name__)

SUGGESTIONS_PER_ROW = 3


def suggestion_rows(suggestions: list[smart.TagSuggestion], on_click) -> list[QHBoxLayout]:
    """Botones "＋ etiqueta" de las sugerencias, en filas (el panel del visor es angosto)."""
    rows: list[QHBoxLayout] = []
    for i, s in enumerate(suggestions):
        if i % SUGGESTIONS_PER_ROW == 0:
            rows.append(QHBoxLayout())
            rows[-1].setSpacing(4)
        btn = QPushButton(f"＋ {s.tag.name}")
        btn.setToolTip(f"{s.reason}\nClic para agregarla")
        btn.setStyleSheet(
            f"QPushButton{{color:{s.tag.color};border:1px dashed {s.tag.color};border-radius:10px;"
            "padding:1px 8px;font-size:11px;background:transparent;}"
            f"QPushButton:hover{{background:{s.tag.color}33;}}"
        )
        btn.clicked.connect(lambda _, t=s.tag: on_click(t))
        rows[-1].addWidget(btn)
    for row in rows:
        row.addStretch()
    return rows


# ─── Editor de etiquetas de una foto ──────────────────────────────────────────


class TagEditor(QWidget):
    """Chips de las etiquetas de una foto (✕ quita) + combo para agregar. Lo usa el visor."""

    tags_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.photo_id: int | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.tags_container = QWidget()
        self.tags_layout = QVBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(4)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.tags_container)

        # Sugeridas por carpeta (fase 10)
        self.sugg_container = QWidget()
        self.sugg_layout = QVBoxLayout(self.sugg_container)
        self.sugg_layout.setContentsMargins(0, 0, 0, 0)
        self.sugg_layout.setSpacing(4)
        lay.addWidget(self.sugg_container)

        row = QHBoxLayout()
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.setPlaceholderText("Buscar o nueva etiqueta…")
        line = self.tag_combo.lineEdit()
        if line is not None:
            line.returnPressed.connect(self._add_tag)
        row.addWidget(self.tag_combo, stretch=1)
        btn_add = QPushButton("＋")
        btn_add.setToolTip("Agregar etiqueta (Enter)")
        btn_add.setFixedWidth(34)
        btn_add.clicked.connect(self._add_tag)
        row.addWidget(btn_add)
        lay.addLayout(row)

    def set_photo(self, photo_id: int | None) -> None:
        self.photo_id = photo_id
        self.reload()

    def reload(self) -> None:
        clear_layout(self.tags_layout)
        if self.photo_id is not None:
            tags = services.get_photo_tags(self.photo_id)
            for tag in tags:
                self.tags_layout.addWidget(self._chip(tag))
            if not tags:
                empty = QLabel("Sin etiquetas")
                empty.setStyleSheet("color:#666;font-size:11px;")
                self.tags_layout.addWidget(empty)
        self._reload_suggestions()
        text = self.tag_combo.currentText()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        for t in services.get_all_tags():
            self.tag_combo.addItem(t.name, userData=t.id)
        self.tag_combo.setCurrentIndex(-1)
        self.tag_combo.setEditText(text if self.photo_id is None else "")
        self.tag_combo.blockSignals(False)

    def _reload_suggestions(self) -> None:
        clear_layout(self.sugg_layout)
        suggestions = smart.suggest_tags([self.photo_id], limit=6) if self.photo_id is not None else []
        self.sugg_container.setVisible(bool(suggestions))
        if not suggestions:
            return
        title = QLabel("Sugeridas:")
        title.setStyleSheet("color:#666;font-size:10px;")
        self.sugg_layout.addWidget(title)
        for row in suggestion_rows(suggestions, self._add_suggested):
            self.sugg_layout.addLayout(row)

    def suggestions(self) -> list[str]:
        """Nombres de las etiquetas sugeridas que se muestran (para tests)."""
        return [
            b.text().removeprefix("＋ ")
            for b in self.sugg_container.findChildren(QPushButton)
            if not b.isHidden() and not self.sugg_container.isHidden()
        ]

    def _add_suggested(self, tag: Tag) -> None:
        if self.photo_id is not None:
            services.add_tag_by_id(self.photo_id, tag.id)
            self.reload()
            self.tags_changed.emit()

    def _chip(self, tag: Tag) -> QPushButton:
        btn = QPushButton(f"{tag.name}  ✕")
        btn.setToolTip(f"Quitar «{tag.name}» ({tag.category})")
        btn.setStyleSheet(
            f"QPushButton{{background:{tag.color}33;color:{tag.color};text-align:left;"
            f"border:1px solid {tag.color};border-radius:10px;padding:2px 8px;font-size:11px;}}"
            f"QPushButton:hover{{background:{tag.color}66;}}"
        )
        btn.clicked.connect(lambda _, tid=tag.id: self._remove_tag(tid))
        return btn

    def _add_tag(self):
        text = self.tag_combo.currentText().strip().lower()
        if text and self.photo_id is not None:
            services.add_tag(self.photo_id, text)
            self.reload()
            self.tags_changed.emit()

    def _remove_tag(self, tag_id: int):
        if self.photo_id is not None:
            services.remove_tag(self.photo_id, tag_id)
            self.reload()
            self.tags_changed.emit()


# ─── Dialog: Etiquetado en lote ───────────────────────────────────────────────


class BulkTagDialog(QDialog):
    """Agrega o quita una etiqueta a todas las fotos seleccionadas."""

    def __init__(self, photo_ids: list[int], parent=None):
        super().__init__(parent)
        self.photo_ids = photo_ids
        self.summary = ""  # lo que se hizo, para el aviso de la ventana principal
        self.setWindowTitle(f"Etiquetar {len(photo_ids)} fotos")
        self.setMinimumWidth(420)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(
            QLabel(
                f"<b>{len(self.photo_ids)} fotos seleccionadas</b><br>"
                "<span style='color:#888;font-size:11px;'>"
                "Escribe o elige una etiqueta y aplica a todas.</span>"
            )
        )

        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setPlaceholderText("Etiqueta a aplicar…")
        for t in services.get_all_tags():
            self.combo.addItem(t.name, userData=t.id)
        self.combo.setCurrentIndex(-1)
        layout.addWidget(self.combo)

        # Sugeridas por carpeta: un clic la elige (después "Agregar a todas")
        suggestions = smart.suggest_tags(self.photo_ids, limit=6)
        self.suggested = [s.tag.name for s in suggestions]
        if suggestions:
            hint = QLabel("Sugeridas:")
            hint.setStyleSheet("color:#666;font-size:10px;")
            layout.addWidget(hint)
            for row in suggestion_rows(suggestions, lambda t: self.combo.setEditText(t.name)):
                layout.addLayout(row)

        row = QHBoxLayout()
        btn_add = QPushButton("＋ Agregar a todas")
        btn_add.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_add.clicked.connect(self._add)

        btn_rem = QPushButton("✕ Quitar de todas")
        btn_rem.setStyleSheet("background:#FF4A4A22;color:#FF4A4A;border:1px solid #FF4A4A;")
        btn_rem.clicked.connect(self._remove)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)

        row.addWidget(btn_add)
        row.addWidget(btn_rem)
        row.addWidget(btn_cancel)
        layout.addLayout(row)

    def _add(self):
        name = self.combo.currentText().strip().lower()
        if not name:
            return
        n = services.bulk_add_tag(self.photo_ids, name)
        self.summary = f"🏷 «{name}» agregada a {n:,} foto{'s' if n != 1 else ''}"
        self.accept()

    def _remove(self):
        name = self.combo.currentText().strip().lower()
        if not name:
            return
        all_tags = services.get_all_tags()
        tag = next((t for t in all_tags if t.name == name), None)
        if not tag:
            QMessageBox.warning(self, "No encontrada", f"La etiqueta '{name}' no existe.")
            return
        n = services.bulk_remove_tag(self.photo_ids, tag.id)
        self.summary = f"🏷 «{name}» quitada de {n:,} foto{'s' if n != 1 else ''}"
        self.accept()
