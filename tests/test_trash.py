"""Papelera interna (#17): nada de lo que se quita de PhotoVault se pierde de inmediato."""

import sqlite3

import pytest

import database as db
import services
from tests.conftest import make_legacy_db


@pytest.fixture
def photo(db_path):
    db.init_db()

    def add(path: str, tags: tuple[str, ...] = ()) -> int:
        pid = db.upsert_photo(path, path.rsplit("\\", 1)[-1], 2020, 5, 1234, 640, 480)
        for t in tags:
            db.add_tag_to_photo(pid, db.create_tag(t))
        return pid

    return add


def test_migracion_v2_crea_tabla(db_path):
    make_legacy_db(db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert "deleted_photos" in tables
    assert version == db.SCHEMA_VERSION >= 2


def test_deindex_va_a_la_papelera_y_se_restaura(photo):
    pid = photo(r"D:\Fotos\a.jpg", ("playa", "familia"))
    photo(r"D:\Fotos\b.jpg")
    photo(r"D:\Otras\c.jpg")

    services.deindex_folder(r"D:\Fotos")
    assert db.get_photo_count() == 1
    batches = services.list_trash()
    assert len(batches) == 1
    assert (batches[0].count, batches[0].tagged) == (2, 1)
    assert "D:\\Fotos" in batches[0].reason

    restored, merged = services.restore_trash_batch(batches[0].batch_id)
    assert (restored, merged) == (2, 0)
    assert services.count_trash() == 0
    assert db.get_photo_count() == 3

    a = next(p for p in db.get_photos() if p.filename == "a.jpg")
    assert a.id != pid  # registro nuevo…
    assert (a.year, a.month, a.width, a.height, a.filesize) == (2020, 5, 640, 480, 1234)
    assert {t.name for t in db.get_photo_tags(a.id)} == {"playa", "familia"}  # …con sus tags


def test_restaurar_fusiona_si_se_reindexo(photo):
    photo(r"D:\Fotos\a.jpg", ("playa",))
    services.deindex_folder(r"D:\Fotos")
    # El usuario vuelve a indexar la carpeta antes de restaurar
    new_id = photo(r"D:\Fotos\a.jpg", ("nuevo",))

    restored, merged = services.restore_trash_batch(services.list_trash()[0].batch_id)
    assert (restored, merged) == (0, 1)
    assert db.get_photo_count() == 1
    assert {t.name for t in db.get_photo_tags(new_id)} == {"playa", "nuevo"}


def test_restaurar_recrea_tags_borrados(photo):
    photo(r"D:\Fotos\a.jpg", ("viaje",))
    tag = next(t for t in db.get_all_tags() if t.name == "viaje")
    services.update_tag(tag.id, "viaje", "lugares", "#ABCDEF")
    # La papelera guarda categoría y color tal como estaban al quitar el registro
    services.deindex_folder(r"D:\Fotos")
    services.delete_tag(tag.id)

    services.restore_trash_batch(services.list_trash()[0].batch_id)
    restored_tag = next(t for t in db.get_all_tags() if t.name == "viaje")
    assert (restored_tag.category, restored_tag.color) == ("lugares", "#ABCDEF")


def test_faltantes_van_a_la_papelera(tmp_path, photo):
    photo(str(tmp_path / "borrada.jpg"), ("x",))
    report = services.find_missing_files()
    services.delete_missing(report)
    assert services.count_trash() == 1
    assert services.list_trash()[0].reason == "Archivos que ya no existían en disco"


def test_duplicado_borrado_va_a_la_papelera(tmp_path, photo, monkeypatch):
    f = tmp_path / "copia.jpg"
    f.write_bytes(b"x")
    pid = photo(str(f), ("dup",))
    monkeypatch.setattr(services, "send2trash", lambda p: None)
    services.delete_photo_file(pid)
    assert "copia.jpg" in services.list_trash()[0].reason


def test_cada_operacion_es_un_lote(photo):
    photo(r"D:\A\1.jpg")
    photo(r"D:\B\2.jpg")
    services.deindex_folder(r"D:\A")
    services.deindex_folder(r"D:\B")
    assert len(services.list_trash()) == 2


def test_eliminar_lote_y_vaciar(photo):
    photo(r"D:\A\1.jpg")
    photo(r"D:\B\2.jpg")
    services.deindex_folder(r"D:\A")
    services.deindex_folder(r"D:\B")
    b = services.list_trash()[0]
    assert services.delete_trash_batch(b.batch_id) == 1
    assert services.count_trash() == 1
    assert services.empty_trash() == 1
    assert services.list_trash() == []


def test_purga_por_antiguedad(photo):
    photo(r"D:\A\1.jpg")
    photo(r"D:\B\2.jpg")
    services.deindex_folder(r"D:\A")
    services.deindex_folder(r"D:\B")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE deleted_photos SET deleted_at = datetime('now', '-40 days') WHERE path LIKE 'D:\\A%'"
        )
    assert services.purge_old_trash() == 1
    assert [b.count for b in services.list_trash()] == [1]


def test_muchos_registros_en_lotes(photo):
    # Más de 500 (límite de parámetros por consulta)
    for i in range(1200):
        photo(rf"D:\Grande\{i}.jpg", ("t",) if i % 3 == 0 else ())
    services.deindex_folder(r"D:\Grande")
    [b] = services.list_trash()
    assert (b.count, b.tagged) == (1200, 400)
    services.restore_trash_batch(b.batch_id)
    assert db.get_photo_count() == 1200
    assert services.count_photos_with_tag(db.get_tag_ids_by_name()["t"]) == 400
