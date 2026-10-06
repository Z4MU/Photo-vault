"""
PhotoVault - ui/workers.py
Hilos (QThread) y utilidades para correr tareas largas sin congelar la UI.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import QEventLoop, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QMessageBox,
    QProgressDialog,
    QWidget,
)

import config
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
    if thread.isRunning():
        _retired_threads.add(thread)
        thread.finished.connect(lambda t=thread: _retired_threads.discard(t))


def wait_all_threads(timeout_ms: int = 5000) -> None:
    """Al cerrar la app: esperar a que terminen los hilos retirados."""
    for t in list(_retired_threads):
        if not t.wait(timeout_ms):
            logger.warning("Un hilo no terminó a tiempo al cerrar: %r", t)


def disconnect_all(*signals) -> None:
    for sig in signals:
        try:
            sig.disconnect()
        except TypeError:
            pass  # No tenía conexiones


class IndexWorker(StoppableThread):
    progress = pyqtSignal(int, int, str)
    completed = pyqtSignal(object)  # indexer.IndexResult
    error = pyqtSignal(str)

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            result = indexer.index_folder(
                self.folder,
                progress_callback=lambda c, t, p: self.progress.emit(c, t, p),
                should_stop=self.is_stopping,
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


class ThumbnailLoader(StoppableThread):
    """
    Carga las miniaturas de la página actual en paralelo (THUMB_WORKERS hilos)
    y emite `loaded` a medida que están. Después sigue generando en caché las
    de `prefetch` (la página siguiente) sin emitir nada, para que al pasar de
    página aparezcan al instante.
    """

    loaded = pyqtSignal(int, QImage)

    def __init__(self, photos: list[Photo], prefetch: list[Photo] | None = None, size: int | None = None):
        super().__init__()
        self.photos = photos
        self.prefetch = prefetch or []
        self.size = size or config.THUMB_SIZE_GALLERY

    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=services.THUMB_WORKERS) as pool:
                futures = {pool.submit(self._safe_load, p): p for p in self.photos}
                for fut in as_completed(futures):
                    if self._stop_flag:
                        break
                    img = fut.result()
                    if img is not None:
                        self.loaded.emit(futures[fut].id, img)
                if self._stop_flag:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return
                # Precarga: solo dejar las miniaturas en el caché de disco
                for chunk_start in range(0, len(self.prefetch), services.THUMB_WORKERS):
                    if self._stop_flag:
                        break
                    chunk = self.prefetch[chunk_start : chunk_start + services.THUMB_WORKERS]
                    list(pool.map(self._safe_cache, chunk))
        finally:
            db.close_connection()

    def _safe_load(self, photo: Photo) -> QImage | None:
        if self._stop_flag:
            return None
        try:
            return _load_thumbnail_image(photo, self.size)
        except Exception:
            logger.exception("Error cargando miniatura de %s", photo.path)
            return None

    def _safe_cache(self, photo: Photo) -> None:
        if self._stop_flag:
            return
        try:
            thumbnail_cache.get_photo_thumbnail(photo, size=self.size)
        except Exception:
            logger.exception("Error precargando miniatura de %s", photo.path)


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
