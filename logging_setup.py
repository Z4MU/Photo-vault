"""
PhotoVault - logging_setup.py
Configura el registro de eventos a archivo y captura errores no manejados.

El .exe corre con console=False, así que sin esto cualquier excepción
desaparece sin dejar rastro. Todo queda en ~/.photovault/logs/photovault.log
(rotativo: 5 archivos de 1 MB).
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from types import TracebackType

import config

LOG_DIR  = config.LOG_DIR
LOG_FILE = LOG_DIR / "photovault.log"

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(threadName)s): %(message)s"

logger = logging.getLogger("photovault")


def setup_logging(level: int = logging.INFO) -> None:
    """Configura el logger raíz: archivo rotativo + consola si existe."""
    root = logging.getLogger()
    if any(getattr(h, "_photovault", False) for h in root.handlers):
        return  # Ya configurado

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=1_048_576, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler._photovault = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError as e:
        print(f"[PhotoVault] No se pudo crear el log en {LOG_FILE}: {e}", file=sys.stderr)

    # En el .exe sin consola sys.stderr es None
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        console._photovault = True  # type: ignore[attr-defined]
        root.addHandler(console)

    sys.excepthook       = _handle_exception
    threading.excepthook = _handle_thread_exception


def install_qt_handlers() -> None:
    """
    Redirige los mensajes internos de Qt (warnings como
    'QThread: Destroyed while thread is still running') al log.
    Llamar después de importar PyQt6.
    """
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    qt_logger = logging.getLogger("qt")
    levels = {
        QtMsgType.QtDebugMsg:    logging.DEBUG,
        QtMsgType.QtInfoMsg:     logging.INFO,
        QtMsgType.QtWarningMsg:  logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg:    logging.CRITICAL,
    }

    def handler(msg_type, _context, message):
        qt_logger.log(levels.get(msg_type, logging.WARNING), message)

    qInstallMessageHandler(handler)


def _handle_exception(exc_type: type[BaseException], exc: BaseException,
                      tb: TracebackType | None) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logger.critical("Excepción no manejada", exc_info=(exc_type, exc, tb))
    _show_error_dialog(exc)


def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    logger.critical(
        "Excepción no manejada en hilo %s",
        args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[arg-type]
    )


def _show_error_dialog(exc: BaseException) -> None:
    """Muestra el error al usuario si hay una QApplication corriendo."""
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        return
    app = QApplication.instance()
    # Solo desde el hilo de la UI; mostrar widgets desde otro hilo crashea
    if app is None or QThread.currentThread() is not app.thread():
        return
    QMessageBox.critical(
        None, "Error inesperado",
        f"Ocurrió un error inesperado:\n\n{exc}\n\n"
        f"Los detalles quedaron registrados en:\n{LOG_FILE}",
    )
