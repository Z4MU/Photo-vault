"""
PhotoVault - ui/content_search.py
ContentSearchMixin (fase 10): el botón 🧠 junto al buscador (buscar por lo que
se ve en la foto) y "Parecidas por contenido" desde el menú de una foto.
"""

import logging
from typing import cast

from PyQt6.QtWidgets import QComboBox, QLineEdit, QMessageBox, QPushButton, QWidget

import clip_model
import smart
from models import Photo

logger = logging.getLogger(__name__)

SEARCH_PLACEHOLDER = "🔍 Buscar por nombre o nota…  (Ctrl+F)"
CONTENT_PLACEHOLDER = "🧠 Describe lo que se ve: perro en la playa, comida, captura de pantalla…"


class ContentSearchMixin:
    """Lo usa MainWindow (necesita el buscador, los combos de orden y _apply_query)."""

    search_edit: QLineEdit
    btn_semantic: QPushButton
    semantic_chip: QPushButton
    sort_field_combo: QComboBox
    sort_order_combo: QComboBox
    _semantic_special: str | None = None  # "@foto:<id>" (parecidas a una foto)

    def _apply_query(self) -> None:
        raise NotImplementedError

    def toast(self, text: str, kind: str = "info", ms: int = 4000) -> None:
        raise NotImplementedError

    def open_settings(self, tab: str | None = None) -> None:
        raise NotImplementedError

    def _content_window(self) -> QWidget:
        return cast(QWidget, self)

    def semantic_text(self) -> str | None:
        """Lo que se busca por contenido (None = búsqueda normal)."""
        if self._semantic_special:
            return self._semantic_special
        text = self.search_edit.text().strip()
        return text if self.btn_semantic.isChecked() and text else None

    def name_search_text(self) -> str | None:
        if self.btn_semantic.isChecked() or self._semantic_special:
            return None
        return self.search_edit.text().strip() or None

    def _ensure_content_ready(self) -> bool:
        """Modelo descargado y algo analizado; si no, explica qué falta."""
        if not smart.content_model_installed():
            if (
                QMessageBox.question(
                    self._content_window(),
                    "Búsqueda por contenido",
                    "Para buscar por lo que se ve en las fotos hay que descargar el modelo "
                    f"(una sola vez, {clip_model.DOWNLOAD_SIZE // 1_048_576} MB) y analizar las fotos."
                    "\n\n¿Abrir Configuración → IA?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            ):
                self.open_settings("ai")
            return False
        done, total = smart.analysis_status()
        if not done:
            self.toast("Todavía no hay fotos analizadas: Configuración → 🧠 IA → Analizar.", "warning", 7000)
            return False
        if done < total:
            self.toast(f"Se busca entre las {done:,} fotos ya analizadas (de {total:,}).", "info", 5000)
        return True

    def _on_semantic_toggled(self, checked: bool) -> None:
        if checked and not self._ensure_content_ready():
            self.btn_semantic.blockSignals(True)
            self.btn_semantic.setChecked(False)
            self.btn_semantic.blockSignals(False)
            return
        self.search_edit.setPlaceholderText(CONTENT_PLACEHOLDER if checked else SEARCH_PLACEHOLDER)
        self.search_edit.setFocus()
        if self.search_edit.text().strip():
            self._apply_query()
        self._update_sort_enabled()

    def show_similar_content(self, photo: Photo) -> None:
        """Muestra las fotos que se parecen por contenido a esta."""
        if not self._ensure_content_ready():
            return
        if not smart.semantic_ranking(f"@foto:{photo.id}"):
            self.toast("Esta foto todavía no está analizada.", "warning")
            return
        self.set_semantic_special(f"@foto:{photo.id}", f"🧠 Parecidas a {photo.short_name}")

    def set_semantic_special(self, semantic: str | None, label: str = "") -> None:
        self._semantic_special = semantic
        self.semantic_chip.setText(f"{label}  ✕")
        self.semantic_chip.setVisible(semantic is not None)
        self._apply_query()
        self._update_sort_enabled()

    def _restore_semantic(self, semantic: str | None) -> None:
        """Al aplicar una consulta guardada: pone el buscador o el chip según `semantic` (sin recargar)."""
        if semantic and semantic.startswith("@"):
            self._semantic_special = semantic
            self.semantic_chip.setText("🧠 Búsqueda por contenido  ✕")
            self.semantic_chip.setVisible(True)
        else:
            self._semantic_special = None
            self.semantic_chip.setVisible(False)
            self.btn_semantic.blockSignals(True)
            self.btn_semantic.setChecked(bool(semantic))
            self.btn_semantic.blockSignals(False)
            self.search_edit.setPlaceholderText(CONTENT_PLACEHOLDER if semantic else SEARCH_PLACEHOLDER)
        self._update_sort_enabled()

    def _update_sort_enabled(self) -> None:
        # Por contenido se ordena por parecido: el orden elegido no se usa
        by_content = bool(self._semantic_special) or self.btn_semantic.isChecked()
        for combo in (self.sort_field_combo, self.sort_order_combo):
            combo.setEnabled(not by_content)
            combo.setToolTip("Ordenadas por parecido (búsqueda por contenido)" if by_content else "")
