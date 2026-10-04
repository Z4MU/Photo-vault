"""
PhotoVault - ui/dialogs/duplicates.py
Búsqueda y limpieza de duplicados por MD5.
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
import logging_setup
import services
import thumbnail_cache
from models import DuplicateGroup
from ui.style import DARK_STYLE
from ui.widgets import clear_layout
from ui.workers import (
    MD5Worker,
    disconnect_all,
    retire_thread,
)

logger = logging.getLogger(__name__)


# ─── Dialog: Duplicados ───────────────────────────────────────────────────────

class DuplicatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vista de duplicados")
        self.setMinimumSize(700, 540)
        self.setStyleSheet(DARK_STYLE)
        self._groups: list[DuplicateGroup] = []
        self._worker: MD5Worker | None = None
        self._build_ui()

    def done(self, result: int):
        # Se llama al cerrar por cualquier vía (botón, Esc, X)
        if self._worker is not None:
            disconnect_all(self._worker.progress, self._worker.completed, self._worker.error)
            retire_thread(self._worker)
            self._worker = None
        super().done(result)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            "<b>Duplicados por hash MD5</b><br>"
            "<span style='color:#888;font-size:11px;'>"
            "Los archivos con el mismo contenido aparecen agrupados. "
            "Puedes mandar las copias extra a la Papelera de reciclaje.</span>"
        ))

        # Barra de cálculo de MD5
        md5_bar = QHBoxLayout()
        self.md5_progress = QProgressBar(); self.md5_progress.setRange(0, 100)
        self.md5_progress.setVisible(False)
        self.btn_scan = QPushButton("🔍  Escanear duplicados")
        self.btn_scan.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_scan.clicked.connect(self._start_scan)
        md5_bar.addWidget(self.btn_scan)
        md5_bar.addWidget(self.md5_progress, stretch=1)
        layout.addLayout(md5_bar)

        self.status_lbl = QLabel("Presiona 'Escanear' para calcular los MD5s.")
        self.status_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(self.status_lbl)

        # Lista de grupos
        self.scroll_box = QScrollArea(); self.scroll_box.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(8)
        self.scroll_box.setWidget(self.container)
        layout.addWidget(self.scroll_box, stretch=1)

        btn_close = QPushButton("Cerrar"); btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _start_scan(self):
        self.btn_scan.setEnabled(False)
        self.md5_progress.setVisible(True)
        self.md5_progress.setValue(0)
        self.status_lbl.setText("Calculando hashes…")
        self._worker = MD5Worker()
        self._worker.progress.connect(
            lambda c, t: self.md5_progress.setValue(int(c / t * 100) if t else 0)
        )
        self._worker.completed.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_error(self, message: str):
        self._worker = None
        self.md5_progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        QMessageBox.critical(self, "Error", message)

    def _on_scan_done(self, n: int):
        self._worker = None
        self.md5_progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        self._groups = services.get_duplicate_groups()
        self._render_groups()

    def _render_groups(self):
        clear_layout(self.vbox)

        if not self._groups:
            self.status_lbl.setText("✓ No se encontraron duplicados.")
            self.vbox.addWidget(QLabel("No hay duplicados."))
            return

        total_wasted = sum(g.wasted_bytes for g in self._groups)
        self.status_lbl.setText(
            f"{len(self._groups)} grupos de duplicados  •  "
            f"{total_wasted / 1_048_576:.1f} MB recuperables"
        )

        for group in self._groups:
            card = QWidget()
            card.setStyleSheet("background:#1A1A2E;border-radius:8px;")
            cl = QVBoxLayout(card); cl.setContentsMargins(10, 8, 10, 8); cl.setSpacing(6)

            header_lbl = QLabel(
                f"<b>{group.size} copias</b>  "
                f"<span style='color:#888;font-size:11px;'>"
                f"MD5: {group.md5[:16]}…  •  "
                f"+{group.wasted_bytes // 1024} KB duplicados</span>"
            )
            header_lbl.setTextFormat(Qt.TextFormat.RichText)
            cl.addWidget(header_lbl)

            photos_row = QHBoxLayout()
            for photo in group.photos:
                col = QVBoxLayout()
                # Miniatura pequeña
                img_lbl = QLabel()
                img_lbl.setFixedSize(100, 100)
                img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_lbl.setStyleSheet("background:#13131F;border-radius:4px;")
                jpeg = (thumbnail_cache.get_video_thumbnail(photo.path, size=config.THUMB_SIZE_SMALL)
                        if photo.is_video
                        else thumbnail_cache.get_thumbnail(photo.path, size=config.THUMB_SIZE_SMALL))
                if jpeg:
                    pix = QPixmap(); pix.loadFromData(jpeg)
                    img_lbl.setPixmap(pix.scaled(100, 100,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                img_lbl.setToolTip(photo.path)
                col.addWidget(img_lbl)

                name = QLabel(photo.short_name)
                name.setTextFormat(Qt.TextFormat.PlainText)
                name.setStyleSheet("font-size:10px;color:#8888AA;")
                name.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col.addWidget(name)

                size_lbl = QLabel(f"{(photo.filesize or 0) // 1024} KB")
                size_lbl.setStyleSheet("font-size:10px;color:#666;")
                size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col.addWidget(size_lbl)

                btn_del = QPushButton("🗑 A la Papelera")
                btn_del.setFixedWidth(110)
                btn_del.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;font-size:11px;")
                btn_del.clicked.connect(
                    lambda _, pid=photo.id, g=group: self._delete_photo(pid, g)
                )
                col.addWidget(btn_del)
                photos_row.addLayout(col)

            photos_row.addStretch()
            cl.addLayout(photos_row)
            self.vbox.addWidget(card)

        self.vbox.addStretch()

    def _delete_photo(self, photo_id: int, group: DuplicateGroup):
        if group.size <= 1:
            QMessageBox.warning(self, "Atención",
                "No puedes eliminar la última copia del archivo.")
            return
        photo = next((p for p in group.photos if p.id == photo_id), None)
        if photo is None:
            return
        reply = QMessageBox.question(
            self, "Confirmar",
            f"¿Mandar este archivo a la Papelera de reciclaje?\n\n{photo.path}\n\n"
            "También se quitará de PhotoVault; sus etiquetas quedan en la Papelera de "
            "PhotoVault. El archivo se recupera desde la Papelera de Windows.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok = services.delete_photo_file(photo_id)
        if ok:
            group.photos = [p for p in group.photos if p.id != photo_id]
            # Un grupo con una sola copia ya no es un duplicado
            self._groups = [g for g in self._groups if g.size >= 2]
            self._render_groups()
        else:
            QMessageBox.critical(self, "Error",
                f"No se pudo mandar el archivo a la Papelera.\n"
                f"Detalles en:\n{logging_setup.LOG_FILE}")
