"""
PhotoVault - ui/background.py
Tareas en segundo plano de la ventana principal (una a la vez): indexar y
generar miniaturas, con progreso y Cancelar en la barra de estado.
MainWindow las hereda.
"""

import logging
from functools import partial
from pathlib import Path
from typing import cast

from PyQt6.QtWidgets import QMessageBox, QWidget

import indexer
import services
from ui.widgets import TaskStatusWidget
from ui.workers import IndexWorker, StoppableThread, TaskWorker, disconnect_all, retire_thread

logger = logging.getLogger(__name__)


class BackgroundTasksMixin:
    """Necesita `task_status` y `_reload_all` (los define MainWindow)."""

    task_status: TaskStatusWidget
    _bg_worker: StoppableThread | None
    _bg_kind: str | None

    def _reload_all(self) -> None:
        raise NotImplementedError

    def is_indexing(self) -> bool:
        return self._bg_kind == "index"

    def _release_background(self) -> None:
        """La tarea avisó que terminó: soltarla sin destruir un hilo que aún cierra su conexión."""
        retire_thread(self._bg_worker)
        self._bg_worker, self._bg_kind = None, None

    def _retire_background(self) -> None:
        if self._bg_worker is not None:
            w = self._bg_worker
            signals = [getattr(w, name) for name in ("progress", "completed", "error") if hasattr(w, name)]
            disconnect_all(*signals)
            retire_thread(w)
        self._bg_worker = None
        self._bg_kind = None

    def _cancel_background(self) -> None:
        if self._bg_worker is not None:
            self.task_status.set_cancelling()
            self._bg_worker.stop()  # Termina el archivo actual y emite completed

    def start_indexing(self, folder: str, force: bool = False) -> None:
        """
        Indexa en segundo plano: se puede seguir usando la app mientras tanto.
        force=True relee todos los archivos (fechas y dimensiones con las reglas actuales).
        """
        if self._bg_kind == "index":
            QMessageBox.information(
                cast(QWidget, self), "Indexación en curso", "Ya hay una indexación en curso."
            )
            return
        if self._bg_kind == "thumbs":
            self._retire_background()  # La indexación tiene prioridad
        w = IndexWorker(folder, force=force)
        w.progress.connect(self._on_index_progress)
        w.completed.connect(self._on_index_completed)
        w.error.connect(self._on_background_error)
        self._bg_worker, self._bg_kind = w, "index"
        self.task_status.start(f"Indexando {folder}…")
        w.start()

    def _on_index_progress(self, current: int, total: int, path: str) -> None:
        self.task_status.set_progress(current, total, f"Indexando [{current:,}/{total:,}] {Path(path).name}")

    def _on_index_completed(self, result: indexer.IndexResult) -> None:
        self._release_background()
        txt = (
            f"{'Indexación cancelada' if result.cancelled else '✓ Indexación lista'}: "
            f"{result.added:,} nuevas, {result.updated:,} actualizadas, "
            f"{result.unchanged:,} sin cambios"
        )
        if result.errors:
            txt += f", {result.errors:,} errores (ver log)"
        self.task_status.finish(txt)
        self._reload_all()
        if result.new_ids:
            # Las miniaturas de lo nuevo se generan ya, sin esperar a que se vean
            self.start_thumbnail_generation(result.new_ids)

    def start_thumbnail_generation(self, photo_ids: list[int] | None = None) -> bool:
        """Genera miniaturas en segundo plano (None = toda la colección). False si hay otra tarea."""
        if self._bg_worker is not None:
            return False
        w = TaskWorker(partial(services.pregenerate_thumbnails, photo_ids))
        w.progress.connect(self._on_thumbs_progress)
        w.completed.connect(self._on_thumbs_completed)
        w.error.connect(self._on_background_error)
        self._bg_worker, self._bg_kind = w, "thumbs"
        self.task_status.start("Preparando miniaturas…")
        w.start()
        return True

    def _on_thumbs_progress(self, current: int, total: int) -> None:
        self.task_status.set_progress(current, total, f"Generando miniaturas [{current:,}/{total:,}]")

    def _on_thumbs_completed(self, r: services.ThumbnailBatchResult) -> None:
        self._release_background()
        txt = f"✓ Miniaturas: {r.generated:,} generadas, {r.already_cached:,} ya estaban"
        if r.failed:
            txt += f", {r.failed:,} no se pudieron generar"
        self.task_status.finish(txt)

    def _on_background_error(self, message: str) -> None:
        self._release_background()
        self.task_status.finish(f"Error: {message}", hide_after_ms=20_000)
        QMessageBox.critical(cast(QWidget, self), "Error", message)
