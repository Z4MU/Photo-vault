"""Tests de las copias de seguridad rotativas."""

import sqlite3
from datetime import datetime

import backup


def _make_db(path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()


def _count(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        conn.close()


def test_sin_db_no_crea_nada(db_path, backup_dir):
    assert backup.daily_backup(db_path, backup_dir) is None
    assert not backup_dir.exists()


def test_backup_diario_copia_contenido(db_path, backup_dir):
    _make_db(db_path, rows=5)
    path = backup.daily_backup(db_path, backup_dir)
    assert path is not None and path.exists()
    assert _count(path) == 5
    assert not list(backup_dir.glob("*.tmp"))


def test_backup_incluye_datos_no_checkpointeados_del_wal(db_path, backup_dir):
    _make_db(db_path, rows=0)
    writer = sqlite3.connect(db_path)
    writer.execute("INSERT INTO t VALUES (42)")
    writer.commit()  # Queda en el -wal, la conexión sigue abierta
    try:
        path = backup.create_backup(db_path, "prueba-", backup_dir)
        assert _count(path) == 1
    finally:
        writer.close()


def test_backup_diario_solo_una_vez_por_dia(db_path, backup_dir):
    _make_db(db_path)
    assert backup.daily_backup(db_path, backup_dir) is not None
    assert backup.daily_backup(db_path, backup_dir) is None
    assert len(list(backup_dir.glob("photovault-diario-*.db"))) == 1


def test_rotacion_conserva_los_mas_recientes(db_path, backup_dir):
    _make_db(db_path)
    backup_dir.mkdir()
    # Simular 10 días de backups anteriores
    for day in range(1, 11):
        (backup_dir / f"photovault-diario-202601{day:02d}-120000.db").write_bytes(b"x")

    backup.daily_backup(db_path, backup_dir, keep=7)

    today = datetime.now().strftime("%Y%m%d")
    names = sorted(p.name for p in backup_dir.glob("photovault-diario-*.db"))
    assert len(names) == 7
    assert names[-1].startswith(f"photovault-diario-{today}")
    assert "photovault-diario-20260101-120000.db" not in names


def test_rotacion_no_mezcla_tipos(db_path, backup_dir):
    _make_db(db_path)
    for _ in range(5):
        backup.pre_migration_backup(db_path, target_version=2, backup_dir=backup_dir, keep=3)
    backup.daily_backup(db_path, backup_dir, keep=1)
    assert len(list(backup_dir.glob("photovault-premigracion-*.db"))) == 3
    assert len(list(backup_dir.glob("photovault-diario-*.db"))) == 1


def test_backup_diario_fallido_no_lanza(db_path, backup_dir, monkeypatch):
    _make_db(db_path)

    def falla(*a, **k):
        raise OSError("sin permisos")

    monkeypatch.setattr(backup, "create_backup", falla)
    assert backup.daily_backup(db_path, backup_dir) is None
