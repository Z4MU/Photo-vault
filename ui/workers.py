"""
PhotoVault - ui/workers.py
Hilos (QThread) y utilidades para correr tareas largas sin congelar la UI.
"""

import logging

from PyQt6.QtCore import QEventLoop, Qt, QThread, pyqtSignal
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
    progress  = pyqtSignal(int, int, str)
    completed = pyqtSignal(int, int, int)   # nuevas, actualizadas, errores
    error     = pyqtSignal(str)

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            added, updated, errors = indexer.index_folder(
                self.folder,
                progress_callback=lambda c, t, p: self.progress.emit(c, t, p),
                should_stop=self.is_stopping,
            )
            self.completed.emit(added, updated, errors)
        except Exception as e:
            logger.exception("Error indexando %s", self.folder)
            self.error.emit(str(e))
        finally:
            db.close_connection()


class ThumbnailLoader(StoppableThread):
    loaded = pyqtSignal(int, QImage)

    def __init__(self, photos: list[Photo]):
        super().__init__()
        self.photos = photos

    def run(self):
        for photo in self.photos:
            if self._stop_flag:
                break
            try:
                jpeg = (thumbnail_cache.get_video_thumbnail(photo.path)
                        if photo.is_video
                        else thumbnail_cache.get_thumbnail(photo.path))
                if jpeg:
                    img = QImage.fromData(jpeg)
                    if not img.isNull():
                        if img.width() > 200 or img.height() > 200:
                            img = img.scaled(200, 200,
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                        self.loaded.emit(photo.id, img)
            except Exception:
                logger.exception("Error cargando miniatura de %s", photo.path)
            if not self._stop_flag:
                self.msleep(5)
        db.close_connection()


class MD5Worker(StoppableThread):
    progress  = pyqtSignal(int, int)
    completed = pyqtSignal(int)
    error     = pyqtSignal(str)

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
    progress  = pyqtSignal(int, int)
    completed = pyqtSignal(object)          # services.MissingReport
    error     = pyqtSignal(str)

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
    progress  = pyqtSignal(int, int)
    completed = pyqtSignal(object)
    error     = pyqtSignal(str)

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
    dlg.setAutoClose(False); dlg.setAutoReset(False)
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
