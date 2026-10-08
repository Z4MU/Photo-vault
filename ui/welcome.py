"""
PhotoVault - ui/welcome.py
Lo que se ve en lugar de la galería cuando no hay nada que mostrar:
bienvenida (colección vacía: primeros pasos) o "sin resultados" (los
filtros no dejan ninguna foto).
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import config

logger = logging.getLogger(__name__)

_BTN = (
    "QPushButton{{background:{c}22;color:{c};border:1px solid {c};border-radius:8px;"
    "padding:10px 22px;font-size:14px;}}QPushButton:hover{{background:{c}44;}}"
)

STEPS = [
    ("1", "Indexa tu colección", "Elige la carpeta donde están tus fotos y videos. Se lee en segundo plano."),
    ("2", "Etiqueta rápido", "⚡ Etiquetado rápido: una etiqueta y sí / no con el teclado, o en cuadrícula."),
    ("3", "Explora", "Filtra por etiquetas, carpetas, fechas, estrellas… y guarda tus búsquedas."),
]


class EmptyState(QWidget):
    """Dos modos: `show_welcome()` y `show_no_results()`."""

    index_requested = pyqtSignal()
    clear_filters_requested = pyqtSignal()
    shortcuts_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{config.COLORS['bg']};")
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon = QLabel("📸")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("font-size:56px;")
        outer.addWidget(self.icon)
        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet(f"color:{config.COLORS['text']};font-size:22px;font-weight:bold;")
        outer.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(f"color:{config.COLORS['text_dim']};font-size:13px;margin-bottom:12px;")
        outer.addWidget(self.subtitle)

        self.steps = QWidget()
        sl = QHBoxLayout(self.steps)
        sl.setSpacing(14)
        for n, title, desc in STEPS:
            card = QFrame()
            card.setFixedWidth(230)
            card.setStyleSheet(
                f"QFrame{{background:{config.COLORS['panel_alt']};border:1px solid {config.COLORS['border']};"
                "border-radius:10px;}QLabel{border:none;}"
            )
            cl = QVBoxLayout(card)
            num = QLabel(n)
            num.setStyleSheet(f"color:{config.COLORS['accent']};font-size:26px;font-weight:bold;")
            t = QLabel(title)
            t.setStyleSheet(f"color:{config.COLORS['text']};font-size:14px;font-weight:bold;")
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet(f"color:{config.COLORS['text_dim']};font-size:12px;")
            for w in (num, t, d):
                cl.addWidget(w)
            sl.addWidget(card)
        outer.addWidget(self.steps, alignment=Qt.AlignmentFlag.AlignCenter)

        buttons = QHBoxLayout()
        buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_index = QPushButton("＋  Indexar una carpeta")
        self.btn_index.setStyleSheet(_BTN.format(c=config.COLORS["accent"]))
        self.btn_index.clicked.connect(self.index_requested)
        self.btn_clear = QPushButton("✕  Limpiar filtros")
        self.btn_clear.setStyleSheet(_BTN.format(c=config.COLORS["danger"]))
        self.btn_clear.clicked.connect(self.clear_filters_requested)
        self.btn_keys = QPushButton("⌨  Atajos de teclado")
        self.btn_keys.setStyleSheet(_BTN.format(c=config.COLORS["text_dim"]))
        self.btn_keys.clicked.connect(self.shortcuts_requested)
        for b in (self.btn_index, self.btn_clear, self.btn_keys):
            buttons.addWidget(b)
        outer.addSpacing(18)
        outer.addLayout(buttons)
        self.mode = ""

    def show_welcome(self) -> None:
        self.mode = "welcome"
        self.icon.setText("📸")
        self.title.setText("Bienvenido a PhotoVault")
        self.subtitle.setText(
            "Todavía no hay fotos indexadas. Tus archivos no se mueven ni se modifican: "
            "PhotoVault solo los lee y guarda la información aparte."
        )
        self.steps.setVisible(True)
        self.btn_index.setVisible(True)
        self.btn_clear.setVisible(False)
        self.btn_keys.setVisible(True)

    def show_no_results(self, detail: str = "") -> None:
        self.mode = "no_results"
        self.icon.setText("🔍")
        self.title.setText("No hay fotos con estos filtros")
        self.subtitle.setText(detail or "Prueba quitando alguna etiqueta, la carpeta o la búsqueda.")
        self.steps.setVisible(False)
        self.btn_index.setVisible(False)
        self.btn_clear.setVisible(True)
        self.btn_keys.setVisible(False)
