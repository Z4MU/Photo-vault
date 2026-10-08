"""
Fixtures compartidas.

IMPORTANTE: ningún test debe tocar la DB real del usuario
(~/.photovault/photovault.db) ni sus backups. Las fixtures redirigen
DB_PATH y BACKUP_DIR a un directorio temporal.
"""

import os
import sqlite3
import sys
import traceback
from pathlib import Path

import pytest

import backup
import database as db
import privacy
import thumbnail_cache


@pytest.fixture(autouse=True)
def slot_errors(monkeypatch: pytest.MonkeyPatch):
    """
    Sin excepthook propio, una excepción en un slot de Qt hace que PyQt6
    aborte el proceso (0xC0000409) y no se sabe qué test fue. Aquí se juntan
    y el test falla con el traceback.
    """
    errors: list[str] = []

    def hook(exc_type, exc, tb):
        errors.append("".join(traceback.format_exception(exc_type, exc, tb)))

    if not os.environ.get("PV_NO_SLOT_HOOK"):
        monkeypatch.setattr(sys, "excepthook", hook)
    yield errors
    if errors:
        pytest.fail("Excepción en un slot de Qt:\n" + "\n".join(errors))


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch: pytest.MonkeyPatch):
    """
    Un QMessageBox de verdad en un test se queda esperando para siempre (nadie
    lo cierra). Los informativos se aceptan solos; un error, una advertencia o
    una pregunta que el test no esperaba (no la reemplazó) hacen fallar el test.
    """
    if "PyQt6.QtWidgets" not in sys.modules:
        yield []
        return
    from PyQt6.QtWidgets import QMessageBox

    unexpected: list[str] = []

    def record(kind):
        def show(*args, **_kwargs):
            texts = [a for a in args if isinstance(a, str)]
            unexpected.append(f"{kind}: {' / '.join(texts)}")
            return QMessageBox.StandardButton.No

        return staticmethod(show)

    for kind in ("critical", "warning", "question"):
        monkeypatch.setattr(QMessageBox, kind, record(kind))
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    yield unexpected
    if unexpected:
        pytest.fail("Diálogo inesperado en el test:\n" + "\n".join(unexpected))


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirige la DB y los backups a tmp_path para TODOS los tests."""
    db.close_connection()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "photovault.db")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(thumbnail_cache, "CACHE_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(thumbnail_cache, "PRIVATE_DIR", tmp_path / "thumbs_private")
    # PIN: cada test empieza bloqueado; scrypt más liviano (el real tarda ~0,1 s)
    monkeypatch.setattr(privacy, "KDF_N", 2**10)
    privacy.lock()
    yield tmp_path
    privacy.lock()
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
