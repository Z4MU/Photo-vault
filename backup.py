"""
PhotoVault - backup.py
Copias de seguridad rotativas de la base de datos.

- Diario: al arrancar, si no hay copia de hoy, se crea una. Se guardan las
  últimas DAILY_KEEP.
- Pre-migración: antes de cambiar el esquema de la DB siempre se crea una
  copia. Se guardan las últimas MIGRATION_KEEP.

Usa la API de backup de SQLite, que es segura aunque la DB esté en modo WAL
o abierta por otra conexión. Las copias van a ~/.photovault/backups/.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

BACKUP_DIR     = config.BACKUP_DIR
DAILY_KEEP     = 7
MIGRATION_KEEP = 3

_DAILY_PREFIX     = "photovault-diario-"
_MIGRATION_PREFIX = "photovault-premigracion-"


def create_backup(db_path: Path, prefix: str, backup_dir: Path | None = None,
                  suffix: str = "") -> Path | None:
    """
    Copia db_path a backup_dir/<prefix><fecha-hora><suffix>.db.
    Devuelve la ruta creada, o None si la DB todavía no existe.
    Lanza sqlite3.Error / OSError si la copia falla.
    """
    backup_dir = backup_dir or BACKUP_DIR
    if not db_path.exists():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = backup_dir / f"{prefix}{stamp}{suffix}.db"
    n = 1
    while final.exists():  # Dos copias en el mismo segundo
        final = backup_dir / f"{prefix}{stamp}{suffix}-{n}.db"
        n += 1
    tmp   = final.with_suffix(".db.tmp")

    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Renombrar al final: nunca queda una copia a medias con nombre válido
    tmp.replace(final)
    logger.info("Backup creado: %s", final)
    return final


def daily_backup(db_path: Path, backup_dir: Path | None = None,
                 keep: int = DAILY_KEEP) -> Path | None:
    """Crea la copia diaria si aún no existe una de hoy. Nunca lanza excepciones."""
    backup_dir = backup_dir or BACKUP_DIR
    try:
        today = datetime.now().strftime("%Y%m%d")
        if any(backup_dir.glob(f"{_DAILY_PREFIX}{today}-*.db")):
            return None
        path = create_backup(db_path, _DAILY_PREFIX, backup_dir)
        _rotate(backup_dir, _DAILY_PREFIX, keep)
        return path
    except (sqlite3.Error, OSError):
        # Un backup diario fallido no debe impedir abrir la app
        logger.exception("No se pudo crear el backup diario")
        return None


def pre_migration_backup(db_path: Path, target_version: int,
                         backup_dir: Path | None = None,
                         keep: int = MIGRATION_KEEP) -> Path | None:
    """
    Copia obligatoria antes de migrar el esquema.
    A diferencia del diario, si falla lanza la excepción: es preferible no
    migrar a migrar sin respaldo.
    """
    backup_dir = backup_dir or BACKUP_DIR
    path = create_backup(db_path, _MIGRATION_PREFIX, backup_dir, suffix=f"-a-v{target_version}")
    _rotate(backup_dir, _MIGRATION_PREFIX, keep)
    return path


def list_backups(backup_dir: Path | None = None) -> list[Path]:
    """Todas las copias, de la más reciente a la más antigua."""
    backup_dir = backup_dir or BACKUP_DIR
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("photovault-*.db"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _rotate(backup_dir: Path, prefix: str, keep: int) -> None:
    # El nombre lleva fecha-hora, así que el orden alfabético es cronológico
    files = sorted(backup_dir.glob(f"{prefix}*.db"))
    for old in files[:-keep] if keep > 0 else files:
        try:
            old.unlink()
            logger.info("Backup antiguo eliminado: %s", old.name)
        except OSError:
            logger.warning("No se pudo eliminar el backup antiguo %s", old, exc_info=True)
