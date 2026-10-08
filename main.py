"""
PhotoVault - main.py
Punto de entrada: logging, backup diario, migraciones de la DB y ventana principal.
La interfaz está en el paquete ui/.

Importante: aquí arriba solo se importa lo mínimo para configurar el logging.
El resto (PyQt, Pillow, la UI…) se importa dentro de main(), DESPUÉS de
setup_logging(), para que cualquier fallo al importar quede en el log; el .exe
no tiene consola y si no, se cerraría sin dejar rastro.
"""

import logging
import sys
import time

import logging_setup

logger = logging.getLogger(__name__)


def _startup() -> bool:
    """
    Backup diario + migraciones de la DB. Devuelve False si la app no debe
    abrirse (el error ya se mostró al usuario).
    """
    from PyQt6.QtWidgets import QMessageBox

    import backup
    import database as db
    import services

    backup.daily_backup(db.DB_PATH)
    try:
        db.init_db()
    except db.DatabaseTooNewError as e:
        logger.error("%s", e)
        QMessageBox.critical(None, "Versión de base de datos no compatible", str(e))
        return False
    except Exception as e:
        logger.exception("No se pudo inicializar la base de datos")
        QMessageBox.critical(
            None,
            "Error al abrir la base de datos",
            f"No se pudo preparar la base de datos:\n\n{e}\n\n"
            f"No se hicieron cambios. Detalles en:\n{logging_setup.LOG_FILE}",
        )
        return False
    try:
        services.purge_old_trash()
    except Exception:
        # No es crítico: la papelera se vaciará en otro arranque
        logger.exception("No se pudo limpiar la papelera interna")
    return True


def _set_app_id() -> None:
    """Windows agrupa la ventana con el ícono de PhotoVault (y no el de Python) en la barra de tareas."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Z4MU.PhotoVault")
        except (AttributeError, OSError) as e:
            logger.warning("No se pudo fijar el AppUserModelID: %s", e)


def main() -> int:
    started = time.perf_counter()
    logging_setup.setup_logging()
    try:
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QApplication

        import config
        import database as db
        from ui.main_window import MainWindow
    except Exception:
        logger.critical("No se pudo cargar la aplicación", exc_info=True)
        return 2

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    _set_app_id()
    if config.ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(config.ICON_PATH)))
    logging_setup.install_qt_handlers()
    logger.info("%s %s iniciando (esquema DB v%d)", config.APP_NAME, config.APP_VERSION, db.SCHEMA_VERSION)
    if not _startup():
        return 1
    window = MainWindow()
    window.show()
    logger.info("Ventana lista en %.2f s", time.perf_counter() - started)
    code = app.exec()
    logger.info("PhotoVault cerrado (código %d)", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
