"""
PhotoVault - ui/dialogs/settings.py
Configuración: página, caché de miniaturas, sidecars XMP.
"""

import logging

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

import services
import thumbnail_cache
from ui.style import DARK_STYLE
from ui.workers import (
    run_with_progress,
)

logger = logging.getLogger(__name__)


# ─── Dialog: Configuración ────────────────────────────────────────────────────


class SettingsDialog(QDialog):
    def __init__(self, current_page_size: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setFixedSize(480, 520)
        self.setStyleSheet(DARK_STYLE)
        self.page_size = current_page_size
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(QLabel("<b>Configuración de visualización</b>"))

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Fotos por página:"))
        self.spin = QSpinBox()
        self.spin.setRange(services.PAGE_SIZE_MIN, services.PAGE_SIZE_MAX)
        self.spin.setSingleStep(10)
        self.spin.setValue(self.page_size)
        self.spin.setFixedWidth(80)
        r1.addWidget(self.spin)
        r1.addStretch()
        layout.addLayout(r1)

        info = QLabel("💡 Recomendado: 50-100 para colecciones grandes.")
        info.setStyleSheet("color:#666;font-size:11px;")
        layout.addWidget(info)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        layout.addWidget(sep)

        layout.addWidget(QLabel("<b>Caché de miniaturas</b>"))
        self.cache_lbl = QLabel()
        self._refresh_cache_label()
        self.cache_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(self.cache_lbl)

        cache_row = QHBoxLayout()
        btn_clear = QPushButton("🗑  Limpiar caché")
        btn_clear.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        btn_clear.clicked.connect(self._clear_cache)

        btn_purge = QPushButton("🧹  Purgar huérfanos")
        btn_purge.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        btn_purge.setToolTip("Elimina miniaturas de archivos que ya no están indexados")
        btn_purge.clicked.connect(self._purge_orphans)

        cache_row.addWidget(btn_clear)
        cache_row.addWidget(btn_purge)
        layout.addLayout(cache_row)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#2D2D3F;")
        layout.addWidget(sep2)

        # ── Sidecars XMP ──────────────────────────────────────────────────
        layout.addWidget(QLabel("<b>Etiquetas en archivos .xmp</b>"))
        self.chk_xmp = QCheckBox("Guardar las etiquetas también en un .xmp junto a cada foto")
        self.chk_xmp.setChecked(services.is_xmp_enabled())
        layout.addWidget(self.chk_xmp)
        xmp_info = QLabel(
            "Crea p. ej. <i>IMG_1234.JPG.xmp</i> al lado de la foto. Otros programas "
            "(digiKam, darktable, Lightroom…) pueden leerlos, y sirven de respaldo si "
            "pierdes la base de datos.<br>"
            "⚠ Los nombres de <b>todas</b> las etiquetas quedan visibles en la carpeta, "
            "incluidas las de contenido oculto. Nunca se modifican .xmp de otros programas."
        )
        xmp_info.setWordWrap(True)
        xmp_info.setStyleSheet("color:#666;font-size:11px;")
        layout.addWidget(xmp_info)

        xmp_row = QHBoxLayout()
        btn_sync = QPushButton("⬆  Escribir .xmp ahora")
        btn_sync.setToolTip("Crea/actualiza el .xmp de todas las fotos que tienen etiquetas")
        btn_sync.setStyleSheet("color:#4AFFC3;border:1px solid #4AFFC3;")
        btn_sync.clicked.connect(self._sync_xmp)
        btn_read = QPushButton("⬇  Importar desde .xmp")
        btn_read.setToolTip("Busca .xmp junto a las fotos indexadas y agrega sus etiquetas")
        btn_read.setStyleSheet("color:#4AFFC3;border:1px solid #4AFFC3;")
        btn_read.clicked.connect(self._import_xmp)
        xmp_row.addWidget(btn_sync)
        xmp_row.addWidget(btn_read)
        layout.addLayout(xmp_row)

        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Aplicar")
        btn_ok.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_ok.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _refresh_cache_label(self):
        self.cache_lbl.setText(f"Tamaño del caché: {thumbnail_cache.cache_size_mb()} MB")

    def _clear_cache(self):
        import shutil

        if thumbnail_cache.CACHE_DIR.exists():
            shutil.rmtree(thumbnail_cache.CACHE_DIR)
        self._refresh_cache_label()
        QMessageBox.information(
            self, "Caché limpiado", "El caché fue eliminado. Se regenerará al navegar la galería."
        )

    def _purge_orphans(self):
        n = services.purge_cache_orphans()
        self._refresh_cache_label()
        QMessageBox.information(
            self, "Purga completada", f"Se eliminaron {n} miniaturas huérfanas del caché."
        )

    def _sync_xmp(self):
        r = run_with_progress(
            self, "Escribir .xmp", "Escribiendo etiquetas en archivos .xmp…", services.sync_all_sidecars
        )
        if r is None:
            return
        msg = f"Escritos: {r.written:,}\nSin cambios: {r.unchanged:,}"
        if r.foreign:
            msg += f"\nOmitidos (ya había un .xmp de otro programa): {r.foreign:,}"
        if r.errors:
            msg += f"\nErrores: {r.errors:,} (¿unidad desconectada? ver el log)"
        QMessageBox.information(self, "Archivos .xmp", msg)

    def _import_xmp(self):
        r = run_with_progress(
            self,
            "Importar desde .xmp",
            "Buscando archivos .xmp junto a las fotos…",
            services.import_from_sidecars,
        )
        if r is None:
            return
        QMessageBox.information(
            self,
            "Importado desde .xmp",
            f"Fotos revisadas: {r.checked:,}\n"
            f"Con archivo .xmp: {r.with_xmp:,}\n"
            f"Etiquetas nuevas creadas: {r.tags_created:,}\n"
            f"Asignaciones agregadas: {r.pairs_added:,}",
        )

    def _apply(self):
        self.page_size = self.spin.value()
        was_enabled = services.is_xmp_enabled()
        services.set_xmp_enabled(self.chk_xmp.isChecked())
        if self.chk_xmp.isChecked() and not was_enabled:
            if (
                QMessageBox.question(
                    self,
                    "Archivos .xmp",
                    "Desde ahora cada cambio de etiquetas actualiza el .xmp de la foto.\n\n"
                    "¿Escribir ya los .xmp de las fotos que tienen etiquetas?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                == QMessageBox.StandardButton.Yes
            ):
                self._sync_xmp()
        self.accept()
