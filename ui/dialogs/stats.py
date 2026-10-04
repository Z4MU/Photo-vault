"""
PhotoVault - ui/dialogs/stats.py
Estadísticas de la colección.
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

import services
from ui.charts import build_bar_chart_svg
from ui.style import DARK_STYLE

logger = logging.getLogger(__name__)


# ─── Dialog: Estadísticas ─────────────────────────────────────────────────────

class StatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Estadísticas")
        self.setMinimumSize(700, 520)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()

    def _build_ui(self):
        stats  = services.get_stats()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Resumen numérico ───────────────────────────────────────────────
        summary = QHBoxLayout()
        for label, value in [
            ("Archivos totales", f"{stats.total_photos:,}"),
            ("Imágenes", f"{stats.by_type.get('image', 0):,}"),
            ("Videos", f"{stats.by_type.get('video', 0):,}"),
            ("Etiquetas", f"{stats.total_tags:,}"),
        ]:
            card = QWidget()
            card.setStyleSheet("background:#1E1E2E;border-radius:8px;padding:4px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 10, 16, 10)
            v = QLabel(value); v.setStyleSheet("font-size:22px;font-weight:bold;color:#4A9EFF;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l = QLabel(label); l.setStyleSheet("font-size:11px;color:#888;")
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(v); cl.addWidget(l)
            summary.addWidget(card)
        layout.addLayout(summary)

        # ── Gráfico de barras por año ──────────────────────────────────────
        if stats.years:
            layout.addWidget(QLabel("<b>Archivos por año</b>"))
            layout.addWidget(self._bar_chart(stats.years, "#4A9EFF"))

        # ── Top 10 etiquetas ──────────────────────────────────────────────
        if stats.top_tags:
            layout.addWidget(QLabel("<b>Etiquetas más usadas</b>"))
            layout.addWidget(self._bar_chart(stats.top_tags, "#9E4AFF", is_text_key=True))

        layout.addStretch()

    def _bar_chart(self, data: list[tuple], color: str,
                   is_text_key: bool = False) -> QWidget:
        """Genera un widget SVG con un gráfico de barras horizontal."""
        if not data:
            return QLabel("Sin datos")
        svg, height = build_bar_chart_svg(data, color)
        widget = QSvgWidget()
        widget.load(svg.encode())
        widget.setFixedHeight(height + 10)
        return widget
