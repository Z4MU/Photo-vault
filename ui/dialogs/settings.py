"""
PhotoVault - ui/dialogs/settings.py
Configuración: al abrir, carpetas vigiladas, caché de miniaturas, sidecars XMP.
"""

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setFixedSize(520, 720)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ── Al abrir / carpetas vigiladas (fase 8) ────────────────────────
        layout.addWidget(QLabel("<b>Al abrir</b>"))
        self.chk_session = QCheckBox("Recordar la ventana, los filtros, el orden y por dónde iba")
        self.chk_session.setChecked(services.is_restore_session_enabled())
        layout.addWidget(self.chk_session)

        layout.addWidget(QLabel("<b>Carpetas vigiladas</b>"))
        watch_info = QLabel(
            "Las fotos nuevas que aparezcan en estas carpetas se indexan solas. "
            "Si el disco no está conectado, se saltan sin avisar."
        )
        watch_info.setWordWrap(True)
        watch_info.setStyleSheet("color:#666;font-size:11px;")
        layout.addWidget(watch_info)
        self.watch_list = QListWidget()
        self.watch_list.setFixedHeight(84)
        for folder in services.get_watched_folders():
            self.watch_list.addItem(folder)
        layout.addWidget(self.watch_list)
        wrow = QHBoxLayout()
        btn_add = QPushButton("＋  Agregar carpeta…")
        btn_add.clicked.connect(self._add_watched)
        btn_remove = QPushButton("✕  Quitar")
        btn_remove.clicked.connect(self._remove_watched)
        wrow.addWidget(btn_add)
        wrow.addWidget(btn_remove)
        wrow.addStretch()
        layout.addLayout(wrow)
        self.chk_watch_start = QCheckBox("Revisarlas al abrir PhotoVault")
        self.chk_watch_start.setChecked(services.is_watch_on_start())
        self.chk_watch_live = QCheckBox("Vigilarlas mientras PhotoVault está abierto")
        self.chk_watch_live.setChecked(services.is_watch_live())
        layout.addWidget(self.chk_watch_start)
        layout.addWidget(self.chk_watch_live)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setStyleSheet("color:#2D2D3F;")
        layout.addWidget(sep0)

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

        btn_gen = QPushButton("⚙  Generar todas")
        btn_gen.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_gen.setToolTip(
            "Genera en segundo plano las miniaturas que falten de toda la colección, "
            "para que la galería cargue al instante"
        )
        btn_gen.clicked.connect(self._generate_all)

        cache_row.addWidget(btn_clear)
        cache_row.addWidget(btn_purge)
        cache_row.addWidget(btn_gen)
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
        size = thumbnail_cache.cache_size_mb()
        if (
            QMessageBox.question(
                self,
                "Limpiar caché",
                f"¿Borrar todas las miniaturas ({size} MB)?\n\n"
                "No se pierde nada: se vuelven a generar al navegar, pero la galería "
                "tardará más hasta entonces.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        # En un hilo: con 170.000 miniaturas borrar tarda y antes congelaba la ventana
        run_with_progress(
            self,
            "Limpiar caché",
            "Borrando miniaturas…",
            lambda progress_callback=None, should_stop=None: thumbnail_cache.clear_cache(),
        )
        self._refresh_cache_label()

    def _purge_orphans(self):
        n = run_with_progress(
            self,
            "Purgar huérfanos",
            "Buscando miniaturas que ya no se usan…",
            lambda progress_callback=None, should_stop=None: services.purge_cache_orphans(),
        )
        if n is None:
            return
        self._refresh_cache_label()
        QMessageBox.information(
            self, "Purga completada", f"Se eliminaron {n:,} miniaturas huérfanas del caché."
        )

    def _generate_all(self):
        window = self.parent()
        start = getattr(window, "start_thumbnail_generation", None)
        if start is None:
            return
        if (
            QMessageBox.question(
                self,
                "Generar miniaturas",
                "Se generarán en segundo plano las miniaturas que falten de toda la colección "
                "(puede tardar horas la primera vez y ocupar varios GB en el caché).\n\n"
                "Puedes seguir usando la app y cancelarlo desde la barra de abajo. ¿Empezar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if not start(None):
            QMessageBox.information(
                self, "Ocupado", "Hay otra tarea en segundo plano. Inténtalo cuando termine."
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

    def _add_watched(self):
        folder = QFileDialog.getExistingDirectory(self, "Carpeta a vigilar")
        if folder:
            folder = os.path.normpath(folder)
            if not self.watch_list.findItems(folder, Qt.MatchFlag.MatchExactly):
                self.watch_list.addItem(folder)

    def _remove_watched(self):
        for item in self.watch_list.selectedItems():
            self.watch_list.takeItem(self.watch_list.row(item))

    def watched_folders(self) -> list[str]:
        return [item.text() for i in range(self.watch_list.count()) if (item := self.watch_list.item(i))]

    def _apply(self):
        services.set_restore_session_enabled(self.chk_session.isChecked())
        before = services.get_watched_folders()
        services.set_watched_folders(self.watched_folders())
        services.set_watch_options(self.chk_watch_start.isChecked(), self.chk_watch_live.isChecked())
        apply_watch = getattr(self.parent(), "apply_watch_settings", None)
        if apply_watch is not None:
            apply_watch()
        new = [f for f in services.get_watched_folders() if f not in before]
        scan = getattr(self.parent(), "scan_watched_folders", None)
        if new and scan is not None:
            scan([f for f in new if os.path.isdir(f)])  # las recién agregadas se revisan ya
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
