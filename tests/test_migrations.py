"""Tests de init_db() y del sistema de migraciones versionadas."""

import sqlite3

import pytest

import database as db
from tests.conftest import make_legacy_db


def _tables(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _indexes(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()


def _user_version(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


# ── DB nueva ──────────────────────────────────────────────────────────────────

def test_db_nueva_crea_esquema_y_version(db_path):
    db.init_db()
    assert {"photos", "tags", "photo_tags", "categories", "app_settings"} <= _tables(db_path)
    assert "idx_photos_md5" in _indexes(db_path)
    assert _user_version(db_path) == db.SCHEMA_VERSION


def test_db_nueva_siembra_tags_una_vez(db_path):
    db.init_db()
    n = len(db.get_all_tags())
    assert n > 0
    db.close_connection()
    db.init_db()
    assert len(db.get_all_tags()) == n


def test_db_nueva_no_crea_backup_premigracion(db_path, backup_dir):
    db.init_db()
    assert not backup_dir.exists() or not list(backup_dir.glob("*premigracion*"))


def test_init_db_es_idempotente(db_path):
    db.init_db()
    db.close_connection()
    db.init_db()
    db.close_connection()
    db.init_db()
    assert _user_version(db_path) == db.SCHEMA_VERSION


# ── Regresión: lo que el usuario borra no reaparece ──────────────────────────

def test_tag_borrado_no_reaparece(db_path):
    db.init_db()
    tag = next(t for t in db.get_all_tags() if t.name == "meme")
    db.delete_tag(tag.id)
    db.close_connection()
    db.init_db()
    assert "meme" not in {t.name for t in db.get_all_tags()}


def test_categoria_borrada_no_reaparece(db_path):
    db.init_db()
    db.delete_category("emocion")
    db.close_connection()
    db.init_db()
    assert "emocion" not in db.get_all_categories()


# ── DB vieja (V1) ─────────────────────────────────────────────────────────────

def test_migra_db_vieja_sin_perder_datos(db_path):
    make_legacy_db(db_path)
    db.init_db()

    cols_photos = db._columns(db.get_connection(), "photos")
    cols_tags   = db._columns(db.get_connection(), "tags")
    assert {"media_type", "duration", "md5"} <= cols_photos
    assert "sidebar_hidden" in cols_tags
    assert "idx_photos_md5" in _indexes(db_path)
    assert _user_version(db_path) == db.SCHEMA_VERSION

    # Los datos del usuario siguen ahí
    photos = db.get_photos()
    assert [p.filename for p in photos] == ["a.jpg"]
    assert photos[0].media_type == "image"
    assert [t.name for t in db.get_photo_tags(photos[0].id)] == ["playa"]


def test_db_vieja_con_tags_no_recibe_seed(db_path):
    make_legacy_db(db_path, with_tags=True)
    db.init_db()
    assert [t.name for t in db.get_all_tags()] == ["playa"]
    assert "lugar" in db.get_all_categories()


def test_db_vieja_sin_tags_recibe_seed(db_path):
    make_legacy_db(db_path, with_tags=False)
    db.init_db()
    assert len(db.get_all_tags()) > 0


def test_migrar_db_existente_crea_backup_previo(db_path, backup_dir):
    make_legacy_db(db_path)
    db.init_db()
    backups = list(backup_dir.glob("photovault-premigracion-*.db"))
    assert len(backups) == 1
    # El backup es la DB ANTES de migrar
    assert _user_version(backups[0]) == 0
    conn = sqlite3.connect(backups[0])
    try:
        assert "md5" not in {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    finally:
        conn.close()


def test_si_el_backup_falla_no_se_migra(db_path, monkeypatch):
    make_legacy_db(db_path)

    def falla(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(db.backup, "pre_migration_backup", falla)
    with pytest.raises(OSError):
        db.init_db()
    db.close_connection()
    assert _user_version(db_path) == 0


# ── Transaccionalidad y versiones ────────────────────────────────────────────

def test_migracion_fallida_hace_rollback(db_path, monkeypatch):
    db.init_db()
    db.close_connection()

    def m_rota(conn):
        """Migración de prueba que falla a mitad de camino."""
        conn.execute("ALTER TABLE photos ADD COLUMN columna_nueva TEXT")
        raise RuntimeError("falla simulada")

    monkeypatch.setattr(db, "_MIGRATIONS", [*db._MIGRATIONS, m_rota])
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(db._MIGRATIONS))

    with pytest.raises(RuntimeError):
        db.init_db()
    db.close_connection()

    assert _user_version(db_path) == db.SCHEMA_VERSION - 1
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    conn.close()
    assert "columna_nueva" not in cols


def test_migracion_nueva_se_aplica(db_path, monkeypatch):
    db.init_db()
    db.close_connection()

    def m_nueva(conn):
        """Agrega una columna de prueba."""
        db._add_column_if_missing(conn, "photos", "rating", "INTEGER DEFAULT 0")

    monkeypatch.setattr(db, "_MIGRATIONS", [*db._MIGRATIONS, m_nueva])
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(db._MIGRATIONS))
    db.init_db()
    assert "rating" in db._columns(db.get_connection(), "photos")
    assert _user_version(db_path) == db.SCHEMA_VERSION


def test_db_de_version_futura_se_rechaza_sin_tocarla(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
    conn.commit()
    conn.close()

    with pytest.raises(db.DatabaseTooNewError):
        db.init_db()
    db.close_connection()
    assert _tables(db_path) == set()
    assert _user_version(db_path) == db.SCHEMA_VERSION + 5
