"""
PhotoVault - ui/dialogs/ai_settings.py
Pestaña "IA" de Configuración (fase 10): descargar el modelo de búsqueda por
contenido, analizar las fotos y borrar el análisis.
"""

import logging

from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

import clip_model
import embedding_store
import smart
from ui.workers import run_with_progress

logger = logging.getLogger(__name__)

_HINT_STYLE = "color:#666;font-size:11px;"
# Medido con la colección real: 37 ms por foto (lectura del USB y modelo en paralelo)
SECONDS_PER_PHOTO = 0.037


class AiSettingsPanel(QWidget):
    def __init__(self, window=None, parent=None):
        super().__init__(parent)
        self._window = window  # MainWindow (para analizar en segundo plano)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(10)

        lay.addWidget(QLabel("<b>Búsqueda por contenido</b>"))
        info = QLabel(
            "Escribe lo que buscas («perro en la playa», «captura de pantalla», «comida») y "
            "PhotoVault encuentra las fotos por lo que se ve en ellas, en español. También "
            "sugiere etiquetas por contenido y ordena la revisión rápida por parecido.<br>"
            "Todo funciona en este equipo: las fotos nunca salen de tu computadora."
        )
        info.setWordWrap(True)
        info.setStyleSheet(_HINT_STYLE)
        lay.addWidget(info)

        self.model_lbl = QLabel("")
        self.model_lbl.setWordWrap(True)
        lay.addWidget(self.model_lbl)
        mrow = QHBoxLayout()
        self.btn_download = QPushButton(
            f"⬇  Descargar el modelo ({clip_model.DOWNLOAD_SIZE // 1_048_576} MB)"
        )
        self.btn_download.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_download.clicked.connect(self._download)
        self.btn_remove_model = QPushButton("Quitar el modelo")
        self.btn_remove_model.clicked.connect(self._remove_model)
        mrow.addWidget(self.btn_download)
        mrow.addWidget(self.btn_remove_model)
        mrow.addStretch()
        lay.addLayout(mrow)

        self.analysis_lbl = QLabel("")
        self.analysis_lbl.setWordWrap(True)
        lay.addWidget(self.analysis_lbl)
        arow = QHBoxLayout()
        self.btn_analyze = QPushButton("🧠  Analizar las fotos que faltan")
        self.btn_analyze.setStyleSheet("color:#4AFF9E;border:1px solid #4AFF9E;")
        self.btn_analyze.setToolTip("Corre en segundo plano: puedes seguir usando la app y cancelarlo abajo")
        self.btn_analyze.clicked.connect(self._analyze)
        self.btn_forget = QPushButton("Borrar el análisis")
        self.btn_forget.clicked.connect(self._forget)
        arow.addWidget(self.btn_analyze)
        arow.addWidget(self.btn_forget)
        arow.addStretch()
        lay.addLayout(arow)
        self.chk_auto = QCheckBox("Analizar solas las fotos nuevas después de indexar")
        self.chk_auto.setChecked(smart.is_auto_analyze())
        lay.addWidget(self.chk_auto)

        notes = QLabel(
            "Modelos: CLIP ViT-B/32 de OpenAI (imagen, licencia MIT) y su versión multilingüe "
            "de sentence-transformers (texto, Apache 2.0), descargados de Hugging Face con "
            "verificación SHA-256.<br>El contenido oculto solo se analiza con el PIN escrito."
        )
        notes.setWordWrap(True)
        notes.setStyleSheet(_HINT_STYLE)
        lay.addWidget(notes)
        lay.addStretch()
        self._refresh()

    def _refresh(self) -> None:
        installed = clip_model.is_installed()
        self.model_lbl.setText(
            "✓ Modelo descargado."
            if installed
            else "El modelo no está descargado (se descarga una sola vez)."
        )
        self.btn_download.setVisible(not installed)
        self.btn_remove_model.setVisible(installed)
        done, total = smart.analysis_status()
        missing = max(0, total - done)
        txt = f"Fotos analizadas: {done:,} de {total:,} ({embedding_store.size_mb()} MB)."
        if missing and installed:
            hours = missing * SECONDS_PER_PHOTO / 3600
            eta = f"~{hours:.1f} h" if hours >= 1 else f"~{max(1, round(hours * 60))} min"
            txt += f" Faltan {missing:,}: {eta} la primera vez (lee cada original)."
        self.analysis_lbl.setText(txt)
        self.btn_analyze.setEnabled(installed and missing > 0)
        self.btn_forget.setEnabled(done > 0)

    def _download(self) -> None:
        ok = run_with_progress(
            self, "Descargar modelo", "Descargando el modelo de búsqueda por contenido…", clip_model.download
        )
        if ok:
            QMessageBox.information(
                self,
                "Modelo listo",
                "Modelo descargado. Ahora analiza tus fotos (botón 🧠) para poder buscarlas por contenido.",
            )
        self._refresh()

    def _remove_model(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Quitar el modelo",
                "¿Borrar el modelo descargado? El análisis ya hecho se conserva; para volver a "
                "buscar por contenido habrá que descargarlo otra vez.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            clip_model.unload()
            clip_model.remove()
            self._refresh()

    def _analyze(self) -> None:
        start = getattr(self._window, "start_content_analysis", None)
        if start is None:
            return
        if start():
            self.analysis_lbl.setText("Analizando en segundo plano (ver la barra de abajo)…")
            self.btn_analyze.setEnabled(False)
        else:
            QMessageBox.information(
                self, "Ocupado", "Hay otra tarea en segundo plano. Inténtalo cuando termine."
            )

    def _forget(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Borrar el análisis",
                "¿Borrar el análisis de contenido de todas las fotos? Habrá que volver a analizarlas "
                "(lee cada original) para buscar por contenido.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            smart.forget_content_analysis()
            self._refresh()

    def apply(self) -> None:
        smart.set_auto_analyze(self.chk_auto.isChecked())
