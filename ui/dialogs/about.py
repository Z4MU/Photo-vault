"""
PhotoVault - ui/dialogs/about.py
"Acerca de" (fase 11): versión, dónde están los datos, licencias de los
modelos y buscar una versión nueva a mano.
"""

import html
import logging
import platform
import sys

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

import config
import database as db
import logging_setup
import updates
from ui.style import DARK_STYLE
from ui.workers import TaskWorker, disconnect_all, retire_on_destroy, retire_thread

logger = logging.getLogger(__name__)


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def open_folder(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acerca de PhotoVault")
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(480)
        self._worker: TaskWorker | None = None
        self.release: updates.Release | None = None
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QHBoxLayout()
        icon = QLabel()
        pix = QPixmap(str(config.ICON_PATH))
        if not pix.isNull():
            icon.setPixmap(
                pix.scaled(
                    72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            )
        head.addWidget(icon)
        title = QLabel(
            f"<span style='font-size:20px;font-weight:bold;color:#4A9EFF;'>PhotoVault {config.APP_VERSION}</span><br>"
            "<span style='color:#8888AA;'>Indexar, navegar, etiquetar y buscar tus fotos, todo en tu computadora.</span>"
        )
        title.setWordWrap(True)
        head.addWidget(title, stretch=1)
        lay.addLayout(head)

        details = QLabel(
            "<table cellspacing='3' style='color:#D0D0E8;'>"
            f"<tr><td style='color:#8888AA;'>Base de datos</td><td>esquema v{db.SCHEMA_VERSION}</td></tr>"
            f"<tr><td style='color:#8888AA;'>Datos</td><td>{html.escape(str(config.DATA_DIR))}</td></tr>"
            f"<tr><td style='color:#8888AA;'>Python · Qt</td><td>{platform.python_version()} · "
            f"Qt {QT_VERSION_STR} (PyQt {PYQT_VERSION_STR})</td></tr>"
            f"<tr><td style='color:#8888AA;'>Sistema</td><td>{html.escape(platform.platform())}"
            f"{' · .exe' if getattr(sys, 'frozen', False) else ''}</td></tr>"
            "</table>"
        )
        details.setTextFormat(Qt.TextFormat.RichText)
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(details)

        row = QHBoxLayout()
        for text, slot in (
            ("📂 Carpeta de datos", lambda: open_folder(config.DATA_DIR)),
            ("📄 Ver el log", lambda: open_folder(logging_setup.LOG_FILE)),
            ("🌐 GitHub", lambda: open_url(config.REPO_URL)),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch()
        lay.addLayout(row)

        credits = QLabel(
            "Búsqueda por contenido: CLIP ViT-B/32 de OpenAI (MIT) y clip-ViT-B-32-multilingual-v1 de "
            "sentence-transformers (Apache 2.0). Hecho con PyQt6, Pillow, OpenCV, pillow-heif, "
            "onnxruntime, numpy y cryptography."
        )
        credits.setWordWrap(True)
        credits.setStyleSheet("color:#666;font-size:11px;")
        lay.addWidget(credits)

        urow = QHBoxLayout()
        self.update_lbl = QLabel("")
        self.update_lbl.setWordWrap(True)
        urow.addWidget(self.update_lbl, stretch=1)
        self.btn_check = QPushButton("⬆ Buscar actualizaciones")
        self.btn_check.clicked.connect(self._check)
        urow.addWidget(self.btn_check)
        self.btn_open_release = QPushButton("Ver la versión nueva")
        self.btn_open_release.setVisible(False)
        self.btn_open_release.clicked.connect(self._open_release)
        urow.addWidget(self.btn_open_release)
        lay.addLayout(urow)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        lay.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    def _open_release(self) -> None:
        if self.release is not None:
            open_url(self.release.url)

    def done(self, result: int) -> None:
        if self._worker is not None:
            disconnect_all(self._worker.completed, self._worker.error)
            retire_thread(self._worker)
            self._worker = None
        super().done(result)

    def _check(self) -> None:
        self.btn_check.setEnabled(False)
        self.update_lbl.setText("Consultando GitHub…")
        w = TaskWorker(lambda progress_callback=None, should_stop=None: updates.fetch_latest())
        w.completed.connect(self._on_checked)
        w.error.connect(self._on_check_error)
        self._worker = w
        w.start()
        retire_on_destroy(self, w)

    def _finish_check(self) -> None:
        retire_thread(self._worker)
        self._worker = None
        self.btn_check.setEnabled(True)

    def _on_checked(self, release: updates.Release | None) -> None:
        self._finish_check()
        if release is None:
            self.update_lbl.setText(
                "No se pudo consultar (¿sin conexión?) o todavía no hay versiones publicadas."
            )
        elif updates.is_newer(release.version):
            self.release = release
            self.update_lbl.setText(f"⬆ Hay una versión nueva: {release.version}")
            self.update_lbl.setStyleSheet("color:#4AFF9E;")
            self.btn_open_release.setVisible(True)
        else:
            self.update_lbl.setText(f"✓ Tienes la última versión ({release.version}).")

    def _on_check_error(self, message: str) -> None:
        self._finish_check()
        self.update_lbl.setText(f"Error: {message}")
