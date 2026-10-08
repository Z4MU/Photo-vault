"""
PhotoVault - ui/workers.py
Hilos (QThread) y utilidades para correr tareas largas sin congelar la UI.
"""

import logging
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PyQt6.QtCore import QEventLoop, QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QMessageBox,
    QProgressDialog,
    QWidget,
)

import database as db
import indexer
import logging_setup
import services
import thumbnail_cache
from models import Photo
from ui.style import DARK_STYLE

logger = logging.getLogger(__name__)


# ─── Hilos ────────────────────────────────────────────────────────────────────
#
# Reglas (ver CLAUDE.md §10):
#   - Los workers NO crean QPixmap ni widgets: solo QImage / datos. El QPixmap
#     se crea en el hilo de la UI al recibir la señal.
#   - Ningún worker redefine la señal `finished` de QThread; para resultados
#     usan `completed`.
#   - Para descartar un worker que sigue corriendo se usa retire_thread():
#     pide que pare y guarda una referencia hasta que termine de verdad, así
#     Python nunca destruye un QThread en ejecución (y la UI no se bloquea).


class StoppableThread(QThread):
    def __init__(self):
        super().__init__()
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def is_stopping(self) -> bool:
        return self._stop_flag


_retired_threads: set[QThread] = set()


def retire_thread(thread: StoppableThread | None) -> None:
    """Pide a un hilo que pare sin bloquear la UI y lo mantiene vivo hasta que termine."""
    if thread is None:
        return
    thread.stop()
    try:
        running = thread.isRunning()
    except RuntimeError:  # Qt ya lo destruyó (terminó hace rato): nada que retirar
        return
    if running:
        _retired_threads.add(thread)
        thread.finished.connect(lambda t=thread: _retired_threads.discard(t))


def retire_on_destroy(owner: QObject, thread: StoppableThread) -> None:
    """
    Si `owner` (una ventana) se destruye sin haber retirado su hilo (p. ej. se
    creó y se descartó sin mostrarse, así que nunca pasó por done()), el hilo
    se retira en vez de destruirse vivo, que cierra la app de golpe.
    """
    # weakref.finalize guarda su propia referencia al hilo hasta que corre: el
    # hilo no puede destruirse antes. (Usar la señal `destroyed` corría código en
    # medio de la destrucción de la ventana en C++ y cerraba la app.)
    weakref.finalize(owner, retire_thread, thread)


def wait_all_threads(timeout_ms: int = 5000) -> None:
    """Al cerrar la app: esperar a que terminen los hilos retirados."""
    for t in list(_retired_threads):
        try:
            if not t.wait(timeout_ms):
                logger.warning("Un hilo no terminó a tiempo al cerrar: %r", t)
        except RuntimeError:  # el objeto de Qt ya se destruyó: no hay nada que esperar
            _retired_threads.discard(t)


def disconnect_all(*signals) -> None:
    for sig in signals:
        try:
            sig.disconnect()
        except TypeError:
            pass  # No tenía conexiones


PROGRESS_INTERVAL = 0.1  # segundos entre avisos de progreso de la indexación


class IndexWorker(StoppableThread):
    progress = pyqtSignal(int, int, str)
    completed = pyqtSignal(object)  # indexer.IndexResult
    error = pyqtSignal(str)

    def __init__(self, folder: str, force: bool = False):
        super().__init__()
        self.folder = folder
        self.force = force
        self._last_report = 0.0

    def _report(self, current: int, total: int, path: str) -> None:
        # Una señal por archivo (171.843) saturaba la UI: revisar G:\Fotos tardaba 23 s
        # en vez de ~2. Como mucho cada PROGRESS_INTERVAL, y siempre el último.
        now = time.monotonic()
        if current == total or now - self._last_report >= PROGRESS_INTERVAL:
            self._last_report = now
            self.progress.emit(current, total, path)

    def run(self):
        try:
            result = indexer.index_folder(
                self.folder,
                progress_callback=self._report,
                should_stop=self.is_stopping,
                force=self.force,
            )
            self.completed.emit(result)
        except Exception as e:
            logger.exception("Error indexando %s", self.folder)
            self.error.emit(str(e))
        finally:
            db.close_connection()


def _load_thumbnail_image(photo: Photo, size: int) -> QImage | None:
    """Miniatura como QImage (QImage sí se puede crear fuera del hilo de la UI)."""
    jpeg = thumbnail_cache.get_photo_thumbnail(photo, size=size)
    if not jpeg:
        return None
    img = QImage.fromData(jpeg)
    if img.isNull():
        return None
    if img.width() > size or img.height() > size:
        img = img.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return img


class ImageLoadQueue(StoppableThread):
    """
    Cola persistente de carga de imágenes (miniaturas de la galería, fotos del
    visor). `request(key, arg)` desde el hilo de la UI; `load_fn(arg)` corre en
    `workers` hilos y se emite `loaded(key, QImage)` (QImage nula = falló).

    Se atiende primero lo último que se pidió (LIFO): al hacer scroll rápido,
    lo que está en pantalla ahora va antes que lo que ya pasó. Si se acumulan
    más de `max_pending`, se descartan los pedidos más viejos y `request`
    devuelve sus claves (para que quien pidió pueda volver a pedirlas).
    """

    loaded = pyqtSignal(str, QImage)

    def __init__(self, load_fn: Callable[[Any], QImage | None], workers: int, max_pending: int = 400):
        super().__init__()
        self._load_fn = load_fn
        self._workers = max(1, workers)
        self._max_pending = max_pending
        self._cond = threading.Condition()
        self._pending: OrderedDict[str, Any] = OrderedDict()

    def request(self, key: str, arg: Any) -> list[str]:
        dropped: list[str] = []
        with self._cond:
            if key in self._pending:
                self._pending.move_to_end(key)
            else:
                self._pending[key] = arg
                while len(self._pending) > self._max_pending:
                    dropped.append(self._pending.popitem(last=False)[0])
            self._cond.notify()
        return dropped

    def clear(self) -> None:
        """Descarta lo pendiente (lo que ya se está cargando igual se emite)."""
        with self._cond:
            self._pending.clear()

    def pending_count(self) -> int:
        with self._cond:
            return len(self._pending)

    def stop(self):
        super().stop()
        with self._cond:
            self._cond.notify_all()

    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                while True:
                    with self._cond:
                        while not self._pending and not self._stop_flag:
                            self._cond.wait()
                        if self._stop_flag:
                            return
                        n = min(self._workers, len(self._pending))
                        batch = [self._pending.popitem(last=True) for _ in range(n)]
                    for (key, _arg), img in zip(batch, pool.map(self._safe_load, batch), strict=True):
                        if self._stop_flag:
                            return
                        self.loaded.emit(key, img if img is not None else QImage())
        finally:
            db.close_connection()

    def _safe_load(self, item: tuple[str, Any]) -> QImage | None:
        if self._stop_flag:
            return None
        try:
            return self._load_fn(item[1])
        except Exception:
            logger.exception("Error cargando imagen %s", item[0])
            return None


def thumbnail_queue() -> ImageLoadQueue:
    """Cola de miniaturas de la galería: arg = (Photo, tamaño)."""
    return ImageLoadQueue(lambda arg: _load_thumbnail_image(*arg), workers=services.THUMB_WORKERS)


class MD5Worker(StoppableThread):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(int)
    error = pyqtSignal(str)

    def run(self):
        try:
            n = services.compute_missing_md5s(
                progress_callback=lambda c, t: self.progress.emit(c, t),
                should_stop=self.is_stopping,
            )
            self.completed.emit(n)
        except Exception as e:
            logger.exception("Error calculando MD5s")
            self.error.emit(str(e))
        finally:
            db.close_connection()


class MissingFilesWorker(StoppableThread):
    """Busca registros de archivos que ya no existen (no borra nada)."""

    progress = pyqtSignal(int, int)
    completed = pyqtSignal(object)  # services.MissingReport
    error = pyqtSignal(str)

    def run(self):
        try:
            report = services.find_missing_files(
                progress_callback=lambda c, t: self.progress.emit(c, t),
                should_stop=self.is_stopping,
            )
            self.completed.emit(report)
        except Exception as e:
            logger.exception("Error buscando archivos faltantes")
            self.error.emit(str(e))
        finally:
            db.close_connection()


class TaskWorker(StoppableThread):
    """
    Corre fn(progress_callback=..., should_stop=...) en un hilo.
    Para funciones de services que siguen esa convención.
    """

    progress = pyqtSignal(int, int)
    completed = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn(
                progress_callback=lambda c, t: self.progress.emit(c, t),
                should_stop=self.is_stopping,
            )
            self.completed.emit(result)
        except Exception as e:
            logger.exception("Error en tarea en segundo plano")
            self.error.emit(str(e))
        finally:
            db.close_connection()


def run_with_progress(parent: QWidget, title: str, text: str, fn):
    """
    Ejecuta fn en un TaskWorker mostrando un diálogo de progreso con botón
    Cancelar. La UI sigue respondiendo. Devuelve el resultado de fn (parcial
    si se canceló), o None si hubo un error (ya mostrado al usuario).
    """
    dlg = QProgressDialog(text, "Cancelar", 0, 100, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setStyleSheet(DARK_STYLE)

    worker = TaskWorker(fn)
    box: dict = {}
    loop = QEventLoop()

    def finish(key: str, value) -> None:
        box[key] = value
        loop.quit()

    def cancel() -> None:
        dlg.setLabelText("Cancelando…")
        worker.stop()

    worker.progress.connect(lambda c, t: dlg.setValue(int(c / t * 100) if t else 0))
    worker.completed.connect(lambda r: finish("result", r))
    worker.error.connect(lambda e: finish("error", e))
    dlg.canceled.connect(cancel)
    worker.start()
    loop.exec()
    worker.wait()
    dlg.close()
    if "error" in box:
        QMessageBox.critical(parent, title, f"{box['error']}\n\nDetalles en:\n{logging_setup.LOG_FILE}")
        return None
    return box.get("result")
