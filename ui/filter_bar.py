"""
PhotoVault - ui/filter_bar.py
Barra de filtros por atributos: tipo, años, orientación, resolución,
duración, valoración, favoritas, sin etiquetar, con nota.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import services

logger = logging.getLogger(__name__)

YEAR_NONE = 1989  # valor mínimo del spin = "sin límite" (se muestra "—")

# (texto, valor) de cada combo. El valor va directo a GalleryQuery.
MEDIA_TYPES = [("Todo", None), ("Fotos", "image"), ("Videos", "video")]
ORIENTATIONS = [
    ("Cualquiera", None),
    ("Horizontal", "landscape"),
    ("Vertical", "portrait"),
    ("Cuadrada", "square"),
]
RESOLUTIONS = [("Cualquiera", None), ("≥ 2 MP", 2.0), ("≥ 8 MP", 8.0), ("≥ 12 MP", 12.0), ("≥ 24 MP", 24.0)]
DURATIONS = [
    ("Cualquiera", (None, None)),
    ("< 30 s", (None, 30.0)),
    ("30 s – 5 min", (30.0, 300.0)),
    ("> 5 min", (300.0, None)),
]
RATINGS = [
    ("Cualquiera", None),
    ("★ o más", 1),
    ("★★ o más", 2),
    ("★★★ o más", 3),
    ("★★★★ o más", 4),
    ("★★★★★", 5),
]


def _combo(items: Sequence[tuple[str, object]], tip: str) -> QComboBox:
    c = QComboBox()
    for text, value in items:
        c.addItem(text, value)
    c.setToolTip(tip)
    return c


def _select(combo: QComboBox, value: object) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)


class FilterBar(QWidget):
    """Emite `changed` con cada cambio; `values()` da los campos para GalleryQuery."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QLabel{color:#8888AA;font-size:11px;} QCheckBox{font-size:11px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.type_combo = _combo(MEDIA_TYPES, "Fotos, videos o ambos")
        self.orient_combo = _combo(ORIENTATIONS, "Orientación de la foto")
        self.res_combo = _combo(RESOLUTIONS, "Resolución mínima (megapíxeles)")
        self.dur_combo = _combo(DURATIONS, "Duración (solo videos)")
        self.rating_combo = _combo(RATINGS, "Valoración mínima")
        max_year = datetime.now().year + 1
        self.year_from = QSpinBox()
        self.year_to = QSpinBox()
        for spin, tip in ((self.year_from, "Desde el año"), (self.year_to, "Hasta el año")):
            spin.setRange(YEAR_NONE, max_year)
            spin.setSpecialValueText("—")  # en el mínimo: sin límite
            spin.setValue(YEAR_NONE)
            spin.setToolTip(f"{tip} (— = sin límite)")
            spin.setFixedWidth(66)
        self.chk_fav = QCheckBox("♥ Favoritas")
        self.chk_untagged = QCheckBox("Sin etiquetar")
        self.chk_note = QCheckBox("Con nota")
        self.btn_clear = QPushButton("✕")
        self.btn_clear.setToolTip("Quitar estos filtros")
        self.btn_clear.setFixedWidth(28)
        self.btn_clear.clicked.connect(self.clear_and_emit)

        # Dos filas: en una sola no entra con la ventana al ancho mínimo
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for row, items in (
            (row1, (("Tipo", self.type_combo), ("Años", self.year_from), ("–", self.year_to),
                    ("Orientación", self.orient_combo), ("Resolución", self.res_combo))),
            (row2, (("Duración", self.dur_combo), ("Valoración", self.rating_combo))),
        ):  # fmt: skip
            for label, w in items:
                row.addWidget(QLabel(label))
                row.addWidget(w)
        for chk in (self.chk_fav, self.chk_untagged, self.chk_note):
            row2.addWidget(chk)
        row1.addStretch()
        row2.addStretch()
        row2.addWidget(self.btn_clear)
        lay.addLayout(row1)
        lay.addLayout(row2)

        for c in (self.type_combo, self.orient_combo, self.res_combo, self.dur_combo, self.rating_combo):
            c.currentIndexChanged.connect(self._emit)
        for s in (self.year_from, self.year_to):
            s.valueChanged.connect(self._emit)
        for chk in (self.chk_fav, self.chk_untagged, self.chk_note):
            chk.toggled.connect(self._emit)
        self._quiet = False

    def _emit(self, *_args) -> None:
        if not self._quiet:
            self.changed.emit()

    def values(self) -> dict:
        """Campos de services.GalleryQuery que maneja esta barra."""
        min_d, max_d = self.dur_combo.currentData() or (None, None)
        y_from, y_to = self.year_from.value(), self.year_to.value()
        media = self.type_combo.currentData()
        if (min_d is not None or max_d is not None) and media is None:
            media = "video"  # filtrar por duración solo tiene sentido con videos
        return {
            "media_type": media,
            "year_from": None if y_from == YEAR_NONE else y_from,
            "year_to": None if y_to == YEAR_NONE else y_to,
            "orientation": self.orient_combo.currentData(),
            "min_megapixels": self.res_combo.currentData(),
            "min_duration": min_d,
            "max_duration": max_d,
            "min_rating": self.rating_combo.currentData(),
            "favorites_only": self.chk_fav.isChecked(),
            "untagged_only": self.chk_untagged.isChecked(),
            "has_note": self.chk_note.isChecked(),
        }

    def set_values(self, q: services.GalleryQuery) -> None:
        """Muestra los filtros de una consulta (búsqueda guardada) sin emitir `changed`."""
        self._quiet = True
        try:
            _select(self.type_combo, q.media_type)
            _select(self.orient_combo, q.orientation)
            _select(self.res_combo, q.min_megapixels)
            _select(self.dur_combo, (q.min_duration, q.max_duration))
            _select(self.rating_combo, q.min_rating or None)
            self.year_from.setValue(q.year_from or YEAR_NONE)
            self.year_to.setValue(q.year_to or YEAR_NONE)
            self.chk_fav.setChecked(q.favorites_only)
            self.chk_untagged.setChecked(q.untagged_only)
            self.chk_note.setChecked(q.has_note)
        finally:
            self._quiet = False

    def clear(self) -> None:
        self.set_values(services.GalleryQuery())

    def clear_and_emit(self) -> None:
        self.clear()
        self.changed.emit()
