"""
PhotoVault - ui/dialogs/duplicates.py
Búsqueda y limpieza de duplicados: idénticos (MD5) o parecidos (hash
perceptual, smart.find_similar_photos; fase 10).
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
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
import smart
import thumbnail_cache
from models import DuplicateGroup
from ui.style import DARK_STYLE
from ui.widgets import clear_layout
from ui.workers import (
    MD5Worker,
    TaskWorker,
    disconnect_all,
    retire_thread,
)

logger = logging.getLogger(__name__)

# Con miles de grupos (ráfagas de fotos) crear todas las tarjetas congelaría la ventana
MAX_GROUPS_SHOWN = 200
MAX_PHOTOS_PER_GROUP = 10

SENSITIVITIES = [
    ("normal", "Normal"),
    ("strict", "Estricta (casi iguales)"),
    ("loose", "Amplia (incluye ráfagas)"),
]


# ─── Dialog: Duplicados ───────────────────────────────────────────────────────


class DuplicatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vista de duplicados")
        self.setMinimumSize(700, 540)
        self.setStyleSheet(DARK_STYLE)
        self._groups: list[DuplicateGroup] = []
        self._worker: TaskWorker | MD5Worker | None = None
        self._build_ui()

    def done(self, result: int):
        # Se llama al cerrar por cualquier vía (botón, Esc, X)
        if self._worker is not None:
            w = self._worker
            disconnect_all(w.progress, w.completed, w.error)
            retire_thread(self._worker)
            self._worker = None
        super().done(result)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(
            QLabel(
                "<b>Duplicados</b><br>"
                "<span style='color:#888;font-size:11px;'>"
                "<b>Idénticas</b>: el mismo archivo (MD5). <b>Parecidas</b>: la misma foto "
                "redimensionada, recomprimida o ráfagas casi iguales. "
                "Puedes mandar las que sobren a la Papelera de reciclaje.</span>"
            )
        )
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Buscar:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Idénticas", userData="exact")
        self.mode_combo.addItem("Parecidas", userData="similar")
        mode_row.addWidget(self.mode_combo)
        self.sens_lbl = QLabel("Sensibilidad:")
        mode_row.addWidget(self.sens_lbl)
        self.sens_combo = QComboBox()
        for value, text in SENSITIVITIES:
            self.sens_combo.addItem(text, userData=value)
        mode_row.addWidget(self.sens_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._on_mode_changed()

        # Barra de cálculo de MD5
        md5_bar = QHBoxLayout()
        self.md5_progress = QProgressBar()
        self.md5_progress.setRange(0, 100)
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
        self.scroll_box = QScrollArea()
        self.scroll_box.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(8)
        self.scroll_box.setWidget(self.container)
        layout.addWidget(self.scroll_box, stretch=1)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def mode(self) -> str:
        return self.mode_combo.currentData()

    def _on_mode_changed(self, *_args) -> None:
        similar = self.mode() == "similar"
        self.sens_lbl.setVisible(similar)
        self.sens_combo.setVisible(similar)

    def _start_scan(self):
        self.btn_scan.setEnabled(False)
        self.mode_combo.setEnabled(False)
        self.sens_combo.setEnabled(False)
        self.md5_progress.setVisible(True)
        self.md5_progress.setValue(0)
        if self.mode() == "similar":
            sensitivity = self.sens_combo.currentData()
            self.status_lbl.setText(
                "Calculando la huella de cada foto (la primera vez tarda: usa las miniaturas)…"
            )
            worker: TaskWorker | MD5Worker = TaskWorker(
                lambda progress_callback=None, should_stop=None: smart.find_similar_photos(
                    sensitivity, progress_callback, should_stop
                )
            )
            worker.completed.connect(self._on_similar_done)
        else:
            self.status_lbl.setText("Calculando hashes…")
            worker = MD5Worker()
            worker.completed.connect(self._on_scan_done)
        worker.progress.connect(lambda c, t: self.md5_progress.setValue(int(c / t * 100) if t else 0))
        worker.error.connect(self._on_scan_error)
        self._worker = worker
        worker.start()

    def _scan_finished(self) -> None:
        retire_thread(self._worker)
        self._worker = None
        self.md5_progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        self.mode_combo.setEnabled(True)
        self.sens_combo.setEnabled(True)

    def _on_similar_done(self, groups: list[DuplicateGroup]) -> None:
        self._scan_finished()
        self._groups = groups
        self._render_groups()

    def _on_scan_error(self, message: str):
        self._scan_finished()
        QMessageBox.critical(self, "Error", message)

    def _on_scan_done(self, n: int):
        self._scan_finished()
        self._groups = services.get_duplicate_groups()
        self._render_groups()

    def _render_groups(self):
        clear_layout(self.vbox)

        if not self._groups:
            self.status_lbl.setText("✓ No se encontraron duplicados.")
            self.vbox.addWidget(QLabel("No hay duplicados."))
            return

        total_wasted = sum(g.wasted_bytes for g in self._groups)
        status = f"{len(self._groups):,} grupos  •  {total_wasted / 1_048_576:.1f} MB recuperables"
        if len(self._groups) > MAX_GROUPS_SHOWN:
            status += f"  •  se muestran los {MAX_GROUPS_SHOWN} más grandes"
        self.status_lbl.setText(status)

        for group in self._groups[:MAX_GROUPS_SHOWN]:
            card = QWidget()
            card.setStyleSheet("background:#1A1A2E;border-radius:8px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(6)

            detail = "parecidas" if group.similar else f"MD5: {group.md5[:16]}…"
            header_lbl = QLabel(
                f"<b>{group.size} {'fotos' if group.similar else 'copias'}</b>  "
                f"<span style='color:#888;font-size:11px;'>"
                f"{detail}  •  "
                f"+{group.wasted_bytes // 1024} KB duplicados</span>"
            )
            header_lbl.setTextFormat(Qt.TextFormat.RichText)
            cl.addWidget(header_lbl)

            photos_row = QHBoxLayout()
            best = group.best if group.similar else None
            for photo in group.photos[:MAX_PHOTOS_PER_GROUP]:
                col = QVBoxLayout()
                # Miniatura pequeña
                img_lbl = QLabel()
                img_lbl.setFixedSize(100, 100)
                img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_lbl.setStyleSheet("background:#13131F;border-radius:4px;")
                jpeg = thumbnail_cache.get_photo_thumbnail(photo, size=config.THUMB_SIZE_SMALL)
                if jpeg:
                    pix = QPixmap()
                    pix.loadFromData(jpeg)
                    img_lbl.setPixmap(
                        pix.scaled(
                            100,
                            100,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                img_lbl.setToolTip(photo.path)
                col.addWidget(img_lbl)

                name = QLabel(photo.short_name)
                name.setTextFormat(Qt.TextFormat.PlainText)
                name.setStyleSheet("font-size:10px;color:#8888AA;")
                name.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col.addWidget(name)

                size_txt = f"{(photo.filesize or 0) // 1024} KB"
                if group.similar and photo.width and photo.height:
                    size_txt = f"{photo.width}×{photo.height}  ·  {size_txt}"
                size_lbl = QLabel(size_txt)
                size_lbl.setStyleSheet("font-size:10px;color:#666;")
                size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col.addWidget(size_lbl)
                if best is not None and photo.id == best.id:
                    best_lbl = QLabel("★ mejor calidad")
                    best_lbl.setStyleSheet("font-size:10px;color:#4AFF9E;")
                    best_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    col.addWidget(best_lbl)

                btn_del = QPushButton("🗑 A la Papelera")
                btn_del.setFixedWidth(110)
                btn_del.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;font-size:11px;")
                btn_del.clicked.connect(lambda _, pid=photo.id, g=group: self._delete_photo(pid, g))
                col.addWidget(btn_del)
                photos_row.addLayout(col)

            if group.size > MAX_PHOTOS_PER_GROUP:
                more = QLabel(f"+{group.size - MAX_PHOTOS_PER_GROUP} más")
                more.setStyleSheet("color:#8888AA;font-size:11px;")
                photos_row.addWidget(more)
            photos_row.addStretch()
            cl.addLayout(photos_row)
            self.vbox.addWidget(card)

        self.vbox.addStretch()

    def _delete_photo(self, photo_id: int, group: DuplicateGroup):
        if group.size <= 1:
            QMessageBox.warning(self, "Atención", "No puedes eliminar la última copia del archivo.")
            return
        photo = next((p for p in group.photos if p.id == photo_id), None)
        if photo is None:
            return
        reply = QMessageBox.question(
            self,
            "Confirmar",
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
            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo mandar el archivo a la Papelera.\nDetalles en:\n{logging_setup.LOG_FILE}",
            )
