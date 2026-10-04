"""
PhotoVault - main.py
Punto de entrada: logging, backup diario, migraciones de la DB y ventana principal.
La interfaz está en el paquete ui/.
"""

import logging
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

import backup
import database as db
import logging_setup
import services
from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


# ─── Entry point ──────────────────────────────────────────────────────────────


def _startup() -> bool:
    """
    Backup diario + migraciones de la DB. Devuelve False si la app no debe
    abrirse (el error ya se mostró al usuario).
    """
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


if __name__ == "__main__":
    logging_setup.setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("PhotoVault")
    logging_setup.install_qt_handlers()
    logger.info("PhotoVault iniciando (esquema DB v%d)", db.SCHEMA_VERSION)
    if not _startup():
        sys.exit(1)
    window = MainWindow()
    window.show()
    code = app.exec()
    logger.info("PhotoVault cerrado (código %d)", code)
    sys.exit(code)
