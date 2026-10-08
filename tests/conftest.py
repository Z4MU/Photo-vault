"""
Fixtures compartidas.

IMPORTANTE: ningún test debe tocar la DB real del usuario
(~/.photovault/photovault.db) ni sus backups. Las fixtures redirigen
DB_PATH y BACKUP_DIR a un directorio temporal.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

import backup
import database as db
import thumbnail_cache


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirige la DB y los backups a tmp_path para TODOS los tests."""
    db.close_connection()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "photovault.db")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(thumbnail_cache, "CACHE_DIR", tmp_path / "thumbs")
    yield tmp_path
    # Hilos que una ventana retiró al cerrarse: esperarlos como hace la app al
    # salir (si el proceso termina con uno vivo, Qt lo cierra de golpe)
    workers = sys.modules.get("ui.workers")
    if workers is not None:
        workers.wait_all_threads(5000)
    db.close_connection()


@pytest.fixture(scope="session")
def qapp():
    """QApplication sin ventanas (QT_QPA_PLATFORM=offscreen) para los tests de UI."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def db_path(isolated_paths: Path) -> Path:
    return isolated_paths / "photovault.db"


@pytest.fixture
def backup_dir(isolated_paths: Path) -> Path:
    return isolated_paths / "backups"


def make_legacy_db(path: Path, with_tags: bool = True) -> None:
    """
    Simula una DB de V1: sin md5, sin sidebar_hidden, sin categories ni
    app_settings, y sin user_version.
    """
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            path        TEXT UNIQUE NOT NULL,
            filename    TEXT NOT NULL,
            year        INTEGER,
            month       INTEGER,
            filesize    INTEGER,
            width       INTEGER,
            height      INTEGER,
            added_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE tags (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL COLLATE NOCASE,
            category    TEXT DEFAULT 'general',
            color       TEXT DEFAULT '#4A9EFF',
            hidden      INTEGER DEFAULT 0
        );
        CREATE TABLE photo_tags (
            photo_id    INTEGER REFERENCES photos(id) ON DELETE CASCADE,
            tag_id      INTEGER REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (photo_id, tag_id)
        );
        INSERT INTO photos (path, filename, year, month, filesize)
            VALUES ('C:\\fotos\\a.jpg', 'a.jpg', 2020, 5, 1234);
    """)
    if with_tags:
        conn.executescript("""
            INSERT INTO tags (name, category, color) VALUES ('playa', 'lugar', '#00AAFF');
            INSERT INTO photo_tags (photo_id, tag_id) VALUES (1, 1);
        """)
    conn.commit()
    conn.close()
