"""
PhotoVault - ui/update_notice.py
UpdateNoticeMixin (fase 11): al abrir, si toca (una vez al día y activado en
Configuración), consulta en un hilo si hay una versión nueva y la avisa con un
aviso que abre su página al pulsarlo. Nunca descarga nada.
"""

import logging
from typing import cast

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

import updates
from ui.dialogs.about import open_url
from ui.workers import TaskWorker, disconnect_all, retire_on_destroy, retire_thread

logger = logging.getLogger(__name__)

# Unos segundos después de abrir: que la galería y la revisión de carpetas vayan primero
UPDATE_CHECK_DELAY_MS = 8000


class UpdateNoticeMixin:
    _update_worker: TaskWorker | None = None
    _closing: bool

    def toast(self, text: str, kind: str = "info", ms: int = 4000, on_click=None) -> None:
        raise NotImplementedError

    def schedule_update_check(self) -> None:
        if updates.is_due():
            QTimer.singleShot(UPDATE_CHECK_DELAY_MS, self._start_update_check)

    def _start_update_check(self) -> None:
        if self._closing or self._update_worker is not None:
            return
        w = TaskWorker(updates.check_for_update)
        w.completed.connect(self._on_update_checked)
        w.error.connect(self._on_update_error)
        self._update_worker = w
        w.start()
        retire_on_destroy(cast(QWidget, self), w)

    def _release_update_worker(self) -> None:
        retire_thread(self._update_worker)
        self._update_worker = None

    def _on_update_checked(self, release: updates.Release | None) -> None:
        self._release_update_worker()
        if self._closing or release is None:
            return
        self.toast(
            f"⬆ Hay una versión nueva de PhotoVault: {release.version}. Clic para ver qué trae y descargarla.",
            "info",
            ms=15000,
            on_click=lambda url=release.url: open_url(url),
        )

    def _on_update_error(self, message: str) -> None:
        self._release_update_worker()
        logger.warning("Error al buscar actualizaciones: %s", message)

    def _shutdown_update_check(self) -> None:
        if self._update_worker is not None:
            disconnect_all(self._update_worker.completed, self._update_worker.error)
            retire_thread(self._update_worker)
            self._update_worker = None
