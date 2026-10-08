"""
PhotoVault - ui/background.py
Tareas en segundo plano de la ventana principal (una a la vez): indexar y
generar miniaturas, con progreso y Cancelar en la barra de estado. Las
indexaciones que llegan mientras hay otra en curso (carpetas vigiladas,
o el usuario) esperan en una cola. MainWindow las hereda.
"""

import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import cast

from PyQt6.QtWidgets import QMessageBox, QWidget

import indexer
import services
from ui.widgets import TaskStatusWidget
from ui.workers import IndexWorker, StoppableThread, TaskWorker, disconnect_all, retire_thread

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexRequest:
    folder: str
    force: bool = False
    auto: bool = False  # carpeta vigilada: sin avisos si no hubo cambios


class BackgroundTasksMixin:
    """Necesita `task_status`, `toast` y `_reload_all` (los define MainWindow)."""

    task_status: TaskStatusWidget
    _bg_worker: StoppableThread | None
    _bg_kind: str | None
    _index_queue: list[IndexRequest]
    _current_index: IndexRequest | None
    _pending_thumb_ids: list[int]
    _closing: bool  # la ventana se está cerrando: no empezar nada ni reaccionar a avisos tardíos

    def _reload_all(self) -> None:
        raise NotImplementedError

    def toast(self, text: str, kind: str = "info", ms: int = 4000) -> None:
        raise NotImplementedError

    def _after_auto_index(self) -> None:
        """Gancho: MainWindow actualiza las carpetas vigiladas (pudo haber subcarpetas nuevas)."""

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
            self._index_queue.clear()  # Cancelar también descarta lo que esperaba
            self._bg_worker.stop()  # Termina el archivo actual y emite completed

    def start_indexing(self, folder: str, force: bool = False, auto: bool = False) -> None:
        """
        Indexa en segundo plano: se puede seguir usando la app mientras tanto.
        force=True relee todos los archivos (fechas y dimensiones con las reglas actuales).
        Si ya hay una indexación, esta espera su turno.
        """
        if self._closing:
            return
        req = IndexRequest(folder, force, auto)
        if self._bg_kind == "index":
            current = self._current_index
            same = current is not None and current.folder == folder and current.force >= force
            if not same and not any(r.folder == folder and r.force >= force for r in self._index_queue):
                self._index_queue.append(req)
                if not auto:
                    self.toast(f"Se indexará {folder} cuando termine la indexación en curso.")
            return
        if self._bg_kind == "thumbs":
            self._retire_background()  # La indexación tiene prioridad
        w = IndexWorker(folder, force=force)
        w.progress.connect(self._on_index_progress)
        w.completed.connect(self._on_index_completed)
        w.error.connect(self._on_background_error)
        self._bg_worker, self._bg_kind = w, "index"
        self._current_index = req
        self.task_status.start(f"{'Revisando' if auto else 'Indexando'} {folder}…")
        w.start()

    def _on_index_progress(self, current: int, total: int, path: str) -> None:
        verb = "Revisando" if self._current_index and self._current_index.auto else "Indexando"
        self.task_status.set_progress(current, total, f"{verb} [{current:,}/{total:,}] {Path(path).name}")

    def _on_index_completed(self, result: indexer.IndexResult) -> None:
        if self._closing:  # aviso que ya estaba en camino cuando se cerró la ventana
            return
        req = self._current_index
        self._release_background()
        self._current_index = None
        changed = result.added or result.updated
        txt = (
            f"{'Indexación cancelada' if result.cancelled else '✓ Indexación lista'}: "
            f"{result.added:,} nuevas, {result.updated:,} actualizadas, "
            f"{result.unchanged:,} sin cambios"
        )
        if result.errors:
            txt += f", {result.errors:,} errores (ver log)"
        if req is not None and req.auto:
            self.task_status.finish(txt, hide_after_ms=1500)
            if changed:
                self.toast(
                    f"📥 {req.folder}: {result.added:,} nuevas, {result.updated:,} actualizadas", "success"
                )
        else:
            self.task_status.finish(txt, hide_after_ms=2500)
            self.toast(txt, "warning" if result.cancelled or result.errors else "success", ms=6000)
        if changed or not (req and req.auto):
            self._reload_all()
        if req is not None and req.auto:
            self._after_auto_index()
        self._pending_thumb_ids.extend(result.new_ids)
        if self._index_queue:
            nxt = self._index_queue.pop(0)
            self.start_indexing(nxt.folder, nxt.force, nxt.auto)
        elif self._pending_thumb_ids:
            # Las miniaturas de lo nuevo se generan ya, sin esperar a que se vean
            ids, self._pending_thumb_ids = self._pending_thumb_ids, []
            self.start_thumbnail_generation(ids)

    def start_thumbnail_generation(self, photo_ids: list[int] | None = None) -> bool:
        """Genera miniaturas en segundo plano (None = toda la colección). False si hay otra tarea."""
        if self._bg_worker is not None or self._closing:
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
        if self._closing:
            return
        self._release_background()
        txt = f"✓ Miniaturas: {r.generated:,} generadas, {r.already_cached:,} ya estaban"
        if r.failed:
            txt += f", {r.failed:,} no se pudieron generar"
        self.task_status.finish(txt)

    def _on_background_error(self, message: str) -> None:
        if self._closing:
            logger.warning("Error en segundo plano al cerrar (no se muestra): %s", message)
            return
        self._release_background()
        self._current_index = None
        self._index_queue.clear()
        self.task_status.finish(f"Error: {message}", hide_after_ms=20_000)
        QMessageBox.critical(cast(QWidget, self), "Error", message)
