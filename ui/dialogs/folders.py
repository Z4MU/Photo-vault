"""
PhotoVault - ui/dialogs/folders.py
Indexar, carpetas indexadas, reubicar y papelera interna.
"""

import html
import logging
from datetime import UTC, datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import services
from models import TrashBatch
from ui.style import DARK_STYLE
from ui.widgets import clear_layout
from ui.workers import (
    MissingFilesWorker,
    disconnect_all,
    retire_thread,
)

logger = logging.getLogger(__name__)


# ─── Dialog: Indexar carpeta ──────────────────────────────────────────────────


class IndexDialog(QDialog):
    """
    Elige la carpeta a indexar. La indexación la corre la ventana principal en
    segundo plano (progreso en la barra de estado): este diálogo se cierra al
    empezar y se puede seguir usando la app.
    """

    start_requested = pyqtSignal(str, bool)  # carpeta, releer todo

    def __init__(self, parent=None, busy: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Indexar carpeta")
        self.setFixedSize(520, 230)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui(busy)

    def _build_ui(self, busy: bool):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Ruta de la carpeta…")
        btn_b = QPushButton("Examinar")
        btn_b.clicked.connect(self._browse)
        row.addWidget(self.path_edit)
        row.addWidget(btn_b)
        layout.addLayout(row)
        info = QLabel(
            "Se indexa en segundo plano (verás el progreso abajo en la ventana). "
            "Si la carpeta ya estaba indexada, solo se leen los archivos nuevos o modificados."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(info)
        self.chk_force = QCheckBox("Releer todos los archivos (lento)")
        self.chk_force.setToolTip(
            "Vuelve a leer fecha y dimensiones de cada archivo con las reglas actuales "
            "(p. ej. orientación de fotos de celular). Con 170.000 fotos tarda varios minutos."
        )
        layout.addWidget(self.chk_force)
        self.btn_start = QPushButton("▶  Iniciar indexación")
        self.btn_start.clicked.connect(self._start)
        if busy:
            self.btn_start.setEnabled(False)
            self.btn_start.setText("Ya hay una indexación en curso")
        layout.addWidget(self.btn_start)

    def _browse(self):
        f = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta")
        if f:
            self.path_edit.setText(str(Path(f)))

    def _start(self):
        folder = self.path_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, "Error", "Selecciona una carpeta válida.")
            return
        self.start_requested.emit(folder, self.chk_force.isChecked())
        self.accept()


# ─── Dialog: Des-indexar carpetas ─────────────────────────────────────────────


class DeindexDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carpetas indexadas")
        self.setMinimumSize(620, 480)
        self.setStyleSheet(DARK_STYLE)
        self._missing_worker: MissingFilesWorker | None = None
        self._build_ui()
        self._refresh()

    def done(self, result: int):
        if self._missing_worker is not None:
            w = self._missing_worker
            disconnect_all(w.progress, w.completed, w.error)
            retire_thread(w)
            self._missing_worker = None
        super().done(result)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("<b>Carpetas indexadas</b>"))
        layout.addWidget(QLabel("Los archivos originales NO se borran del disco."))
        self.scroll_box = QScrollArea()
        self.scroll_box.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(4)
        self.scroll_box.setWidget(self.container)
        layout.addWidget(self.scroll_box, stretch=1)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        layout.addWidget(sep)
        self.btn_missing = QPushButton("🧹  Buscar registros de archivos que ya no existen en disco")
        self.btn_missing.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        self.btn_missing.clicked.connect(self._scan_missing)
        layout.addWidget(self.btn_missing)
        self.missing_progress = QProgressBar()
        self.missing_progress.setRange(0, 100)
        self.missing_progress.setVisible(False)
        layout.addWidget(self.missing_progress)

        tools = QHBoxLayout()
        btn_reloc = QPushButton("📦  Reubicar carpeta o unidad…")
        btn_reloc.setToolTip(
            "Si moviste las fotos o cambió la letra de la unidad: actualiza las rutas sin perder etiquetas"
        )
        btn_reloc.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_reloc.clicked.connect(lambda: self._open_relocate())
        self.btn_trash = QPushButton("")
        self.btn_trash.setStyleSheet("color:#4AFFC3;border:1px solid #4AFFC3;")
        self.btn_trash.clicked.connect(self._open_trash)
        tools.addWidget(btn_reloc)
        tools.addWidget(self.btn_trash)
        layout.addLayout(tools)

    def _open_relocate(self, old_folder: str = ""):
        if RelocateDialog(self, old_folder).exec():
            self._refresh()

    def _open_trash(self):
        TrashDialog(self).exec()
        self._refresh()

    def _refresh(self):
        self.btn_trash.setText(f"♻  Papelera de PhotoVault ({services.count_trash():,})")
        clear_layout(self.vbox)
        folders = services.get_indexed_folders()
        if not folders:
            lbl = QLabel("No hay carpetas indexadas.")
            lbl.setStyleSheet("color:#666;")
            self.vbox.addWidget(lbl)
        else:
            for folder, count in folders:
                row = QWidget()
                row.setStyleSheet("background:#1E1E2E;border-radius:6px;")
                hl = QHBoxLayout(row)
                hl.setContentsMargins(8, 6, 8, 6)
                lbl = QLabel(
                    f"<b>{html.escape(folder)}</b>  <span style='color:#666;'>{count:,} archivos</span>"
                )
                lbl.setTextFormat(Qt.TextFormat.RichText)
                hl.addWidget(lbl, stretch=1)
                btn_mv = QPushButton("Reubicar")
                btn_mv.setFixedWidth(80)
                btn_mv.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;")
                btn_mv.clicked.connect(lambda _, f=folder: self._open_relocate(f))
                hl.addWidget(btn_mv)
                btn = QPushButton("Eliminar")
                btn.setFixedWidth(75)
                btn.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;")
                btn.clicked.connect(lambda _, f=folder: self._deindex(f))
                hl.addWidget(btn)
                self.vbox.addWidget(row)
        self.vbox.addStretch()

    def _deindex(self, folder: str):
        count, tagged = services.preview_deindex_folder(folder)
        msg = f"¿Quitar de PhotoVault la carpeta y sus subcarpetas?\n\n{folder}\n\nRegistros: {count:,}"
        if tagged:
            msg += f"\n{tagged:,} de ellos tienen etiquetas."
        msg += (
            "\n\nLos archivos NO se borran del disco. Podrás restaurar los registros "
            f"(con sus etiquetas) desde la Papelera de PhotoVault durante "
            f"{services.TRASH_KEEP_DAYS} días.\n\n"
            "Si solo moviste la carpeta, usa 'Reubicar' en su lugar."
        )
        if (
            QMessageBox.question(
                self, "Confirmar", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            == QMessageBox.StandardButton.Yes
        ):
            services.deindex_folder(folder)
            self._refresh()

    # ── Archivos faltantes: buscar (en hilo) → confirmar → borrar ─────────

    def _scan_missing(self):
        self.btn_missing.setEnabled(False)
        self.missing_progress.setValue(0)
        self.missing_progress.setVisible(True)
        w = MissingFilesWorker()
        w.progress.connect(lambda c, t: self.missing_progress.setValue(int(c / t * 100) if t else 0))
        w.completed.connect(self._on_missing_scanned)
        w.error.connect(self._on_missing_error)
        self._missing_worker = w
        w.start()

    def _on_missing_error(self, msg: str):
        self._missing_worker = None
        self.missing_progress.setVisible(False)
        self.btn_missing.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)

    def _on_missing_scanned(self, report: services.MissingReport):
        self._missing_worker = None
        self.missing_progress.setVisible(False)
        self.btn_missing.setEnabled(True)

        skipped_txt = ""
        if report.skipped:
            lines = [f"  • {s.root}  ({s.count:,} registros): {s.reason}" for s in report.skipped]
            skipped_txt = (
                "\n\nNo se tocarán estos registros, para proteger tus etiquetas:\n"
                + "\n".join(lines)
                + "\n(Si la unidad cambió de letra o moviste las fotos, usa 'Reubicar'. "
                "Si de verdad ya no existen, quítalos con 'Eliminar' en su carpeta.)"
            )

        if report.count == 0:
            QMessageBox.information(
                self,
                "Archivos faltantes",
                "No se encontraron registros de archivos faltantes." + skipped_txt,
            )
            return

        msg = f"Se encontraron {report.count:,} registros cuyo archivo ya no existe."
        if report.tagged:
            msg += f"\n{report.tagged:,} de ellos tienen etiquetas."
        msg += skipped_txt + (
            "\n\n¿Quitar esos registros de PhotoVault?\n"
            f"Podrás restaurarlos desde la Papelera de PhotoVault durante "
            f"{services.TRASH_KEEP_DAYS} días."
        )

        if (
            QMessageBox.question(
                self,
                "Confirmar",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        n = services.delete_missing(report)
        QMessageBox.information(
            self, "Listo", f"Se quitaron {n:,} registros (están en la Papelera de PhotoVault)."
        )
        self._refresh()


# ─── Dialog: Reubicar carpeta / unidad ───────────────────────────────────────


class RelocateDialog(QDialog):
    """
    Cambia el prefijo de ruta de los registros (p. ej. G:\\ → E:\\, o
    D:\\Fotos → E:\\Respaldo\\Fotos) sin perder etiquetas.
    """

    def __init__(self, parent=None, old_folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Reubicar carpeta o unidad")
        self.setMinimumSize(600, 360)
        self.setStyleSheet(DARK_STYLE)
        self._preview: services.RelocationPreview | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        intro = QLabel(
            "Usa esto si moviste las fotos a otra carpeta o si el disco cambió de letra. "
            "Se actualizan las rutas en PhotoVault y se conservan todas las etiquetas. "
            "No se mueve ningún archivo."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(intro)

        layout.addWidget(QLabel("Ruta vieja (como está en PhotoVault):"))
        self.old_combo = QComboBox()
        self.old_combo.setEditable(True)
        for root, n, available in services.get_indexed_roots():
            label = f"{root}   ({n:,} registros{'' if available else ' — NO disponible'})"
            self.old_combo.addItem(label, userData=root)
        if old_folder:
            self.old_combo.setEditText(old_folder)
        layout.addWidget(self.old_combo)

        layout.addWidget(QLabel("Ruta nueva (donde están ahora los archivos):"))
        row = QHBoxLayout()
        self.new_edit = QLineEdit()
        self.new_edit.setPlaceholderText("p. ej. E:\\  o  E:\\Respaldo\\Fotos")
        btn_b = QPushButton("Examinar")
        btn_b.clicked.connect(self._browse)
        row.addWidget(self.new_edit)
        row.addWidget(btn_b)
        layout.addLayout(row)

        self.preview_lbl = QLabel("")
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.preview_lbl, stretch=1)

        btns = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_prev = QPushButton("🔍  Vista previa")
        btn_prev.clicked.connect(self._do_preview)
        self.btn_apply = QPushButton("✓  Reubicar")
        self.btn_apply.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        btns.addWidget(btn_cancel)
        btns.addStretch()
        btns.addWidget(btn_prev)
        btns.addWidget(self.btn_apply)
        layout.addLayout(btns)

        # Cualquier cambio invalida la vista previa
        self.old_combo.editTextChanged.connect(self._invalidate)
        self.new_edit.textChanged.connect(self._invalidate)

    def _old_value(self) -> str:
        # Si el texto es el de un ítem de la lista, usar la ruta (sin el conteo)
        idx = self.old_combo.findText(self.old_combo.currentText())
        if idx >= 0 and self.old_combo.itemData(idx):
            return self.old_combo.itemData(idx)
        return self.old_combo.currentText().strip()

    def _browse(self):
        f = QFileDialog.getExistingDirectory(self, "Ubicación nueva")
        if f:
            self.new_edit.setText(str(Path(f)))

    def _invalidate(self):
        self._preview = None
        self.btn_apply.setEnabled(False)
        self.preview_lbl.setText("")

    def _do_preview(self):
        try:
            p = services.preview_relocation(self._old_value(), self.new_edit.text().strip())
        except ValueError as e:
            QMessageBox.warning(self, "Reubicar", str(e))
            return
        if p.count == 0:
            self.preview_lbl.setText(
                f"<span style='color:#FFD700;'>No hay registros dentro de {html.escape(p.old_folder)}.</span>"
            )
            return
        color = "#4AFF9E" if p.looks_right else "#FF4A4A"
        txt = (
            f"<b>{p.count:,}</b> registros pasarán de <b>{html.escape(p.old_folder)}</b> "
            f"a <b>{html.escape(p.new_folder)}</b>.<br>"
            f"<span style='color:{color};'>Comprobación: {p.sample_found} de {p.sample_size} "
            f"archivos de muestra existen en la ruta nueva.</span>"
        )
        if not p.looks_right:
            txt += (
                "<br><span style='color:#FF4A4A;'>⚠ La mayoría NO está en la ruta nueva. "
                "Revisa que la ruta sea correcta antes de continuar.</span>"
            )
        if p.conflicts:
            txt += (
                f"<br>{p.conflicts:,} ya estaban indexados en la ruta nueva: se fusionarán "
                "(sus etiquetas se suman al registro existente)."
            )
        self.preview_lbl.setText(txt)
        self._preview = p
        self.btn_apply.setEnabled(True)

    def _apply(self):
        p = self._preview
        if p is None:
            return
        if (
            not p.looks_right
            and QMessageBox.question(
                self,
                "¿Seguro?",
                "La mayoría de los archivos no está en la ruta nueva.\n¿Reubicar de todos modos?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        moved, merged = services.apply_relocation(p)
        QMessageBox.information(
            self,
            "Reubicado",
            f"Rutas actualizadas: {moved:,}\nFusionados con registros existentes: {merged:,}",
        )
        self.accept()


# ─── Dialog: Papelera de PhotoVault ──────────────────────────────────────────


class TrashDialog(QDialog):
    """Registros quitados (des-indexar, faltantes, duplicados) con sus etiquetas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Papelera de PhotoVault")
        self.setMinimumSize(620, 440)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        info = QLabel(
            "Aquí quedan los registros que quitaste de PhotoVault, con sus etiquetas, "
            f"durante {services.TRASH_KEEP_DAYS} días. Restaurar no recupera archivos del disco: "
            "si mandaste un duplicado a la Papelera de Windows, recupéralo desde allí."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(info)

        self.scroll_box = QScrollArea()
        self.scroll_box.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(4)
        self.scroll_box.setWidget(self.container)
        layout.addWidget(self.scroll_box, stretch=1)

        btns = QHBoxLayout()
        btn_empty = QPushButton("🗑  Vaciar papelera")
        btn_empty.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;")
        btn_empty.clicked.connect(self._empty)
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_empty)
        btns.addStretch()
        btns.addWidget(btn_close)
        layout.addLayout(btns)
        self._refresh()

    def _refresh(self):
        clear_layout(self.vbox)
        batches = services.list_trash()
        if not batches:
            lbl = QLabel("La papelera está vacía.")
            lbl.setStyleSheet("color:#666;")
            self.vbox.addWidget(lbl)
        for b in batches:
            row = QWidget()
            row.setStyleSheet("background:#1E1E2E;border-radius:6px;")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(8, 6, 8, 6)
            when = _local_time(b.deleted_at)
            tagged = f" · {b.tagged:,} con etiquetas" if b.tagged else ""
            lbl = QLabel(
                f"<b>{html.escape(b.reason)}</b><br>"
                f"<span style='color:#888;font-size:11px;'>{when} · "
                f"{b.count:,} registros{tagged}</span>"
            )
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            hl.addWidget(lbl, stretch=1)
            btn_r = QPushButton("↺ Restaurar")
            btn_r.setFixedWidth(95)
            btn_r.setStyleSheet("color:#4AFF9E;border:1px solid #4AFF9E;")
            btn_r.clicked.connect(lambda _, bb=b: self._restore(bb))
            btn_d = QPushButton("✕")
            btn_d.setFixedSize(28, 28)
            btn_d.setToolTip("Eliminar definitivamente este lote")
            btn_d.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;border-radius:4px;padding:0;")
            btn_d.clicked.connect(lambda _, bb=b: self._delete(bb))
            hl.addWidget(btn_r)
            hl.addWidget(btn_d)
            self.vbox.addWidget(row)
        self.vbox.addStretch()

    def _restore(self, b: TrashBatch):
        restored, merged = services.restore_trash_batch(b.batch_id)
        msg = f"Restaurados: {restored:,}"
        if merged:
            msg += f"\nYa estaban indexados de nuevo (se les devolvieron las etiquetas): {merged:,}"
        QMessageBox.information(self, "Restaurado", msg)
        self._refresh()

    def _delete(self, b: TrashBatch):
        if (
            QMessageBox.question(
                self,
                "Eliminar definitivamente",
                f"¿Eliminar definitivamente este lote ({b.count:,} registros)?\n"
                "Sus etiquetas ya no se podrán recuperar.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            services.delete_trash_batch(b.batch_id)
            self._refresh()

    def _empty(self):
        n = services.count_trash()
        if not n:
            return
        if (
            QMessageBox.question(
                self,
                "Vaciar papelera",
                f"¿Eliminar definitivamente los {n:,} registros de la papelera?\n"
                "Sus etiquetas ya no se podrán recuperar.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            services.empty_trash()
            self._refresh()


def _local_time(sqlite_utc: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' en UTC (datetime('now') de SQLite) → hora local legible."""
    try:
        dt = datetime.strptime(sqlite_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        return dt.astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return sqlite_utc
