"""Exportar / importar etiquetas con asignaciones (#14)."""

import json

import pytest

import database as db
import services


@pytest.fixture
def photo(db_path):
    db.init_db()

    def add(path: str, tags: tuple[str, ...] = (), size: int = 100) -> int:
        pid = db.upsert_photo(path, path.rsplit("\\", 1)[-1], 2020, 1, size)
        for t in tags:
            db.add_tag_to_photo(pid, db.create_tag(t))
        return pid

    return add


def _reset_db(db_path):
    db.close_connection()
    db_path.unlink()
    for suffix in ("-wal", "-shm"):
        p = db_path.with_name(db_path.name + suffix)
        if p.exists():
            p.unlink()
    db.init_db()


def test_exporta_asignaciones(tmp_path, photo):
    photo(r"G:\a.jpg", ("playa", "familia"))
    photo(r"G:\b.jpg")
    out = tmp_path / "export.json"
    s = services.export_tags(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["version"] == 2
    assert s.assignments == 1
    assert data["assignments"] == [
        {"path": r"G:\a.jpg", "filename": "a.jpg", "filesize": 100, "tags": ["familia", "playa"]}
    ]
    assert not list(tmp_path.glob("*.tmp"))


def test_ida_y_vuelta_tras_perder_la_db(tmp_path, db_path, photo):
    photo(r"G:\a.jpg", ("playa",))
    photo(r"G:\b.jpg", ("viaje", "playa"))
    tag = next(t for t in db.get_all_tags() if t.name == "viaje")
    services.update_tag(tag.id, "viaje", "lugares", "#123456")
    out = tmp_path / "export.json"
    services.export_tags(str(out))

    # Se pierde la DB y se vuelve a indexar la colección (sin etiquetas)
    _reset_db(db_path)
    a = photo(r"G:\a.jpg"); b = photo(r"G:\b.jpg")

    r = services.import_tags(str(out))
    assert (r.photos_matched, r.photos_by_name, r.photos_missing) == (2, 0, 0)
    assert r.pairs_added == 3
    assert {t.name for t in db.get_photo_tags(a)} == {"playa"}
    assert {t.name for t in db.get_photo_tags(b)} == {"viaje", "playa"}
    viaje = next(t for t in db.get_all_tags() if t.name == "viaje")
    assert (viaje.category, viaje.color) == ("lugares", "#123456")


def test_empareja_por_nombre_y_tamano_si_cambio_la_ruta(tmp_path, db_path, photo):
    photo(r"G:\Fotos\a.jpg", ("playa",), size=111)
    out = tmp_path / "export.json"
    services.export_tags(str(out))
    _reset_db(db_path)
    moved = photo(r"E:\Fotos\a.jpg", size=111)

    r = services.import_tags(str(out))
    assert (r.photos_matched, r.photos_by_name) == (0, 1)
    assert [t.name for t in db.get_photo_tags(moved)] == ["playa"]


def test_no_adivina_si_hay_varias_coincidencias(tmp_path, db_path, photo):
    photo(r"G:\a.jpg", ("playa",), size=5)
    out = tmp_path / "export.json"
    services.export_tags(str(out))
    _reset_db(db_path)
    photo(r"E:\x\a.jpg", size=5); photo(r"E:\y\a.jpg", size=5)

    r = services.import_tags(str(out))
    assert r.photos_missing == 1 and r.pairs_added == 0


def test_importar_sin_asignaciones(tmp_path, db_path, photo):
    photo(r"G:\a.jpg", ("playa",))
    out = tmp_path / "export.json"
    services.export_tags(str(out))
    _reset_db(db_path)
    a = photo(r"G:\a.jpg")
    r = services.import_tags(str(out), include_assignments=False)
    assert r.pairs_added == 0 and db.get_photo_tags(a) == []


def test_importar_nunca_quita_etiquetas(tmp_path, photo):
    a = photo(r"G:\a.jpg", ("playa",))
    out = tmp_path / "export.json"
    services.export_tags(str(out))
    db.add_tag_to_photo(a, db.create_tag("extra"))
    services.import_tags(str(out))
    assert {t.name for t in db.get_photo_tags(a)} == {"playa", "extra"}


def test_formato_v1_sigue_funcionando(tmp_path, photo):
    old = tmp_path / "v1.json"
    old.write_text(json.dumps({
        "version": 1,
        "tags": [{"name": "Antigua", "category": "vieja", "color": "#111111"}],
        "categories": ["vieja"],
    }), encoding="utf-8")
    assert services.read_export_summary(str(old)).assignments == 0
    r = services.import_tags(str(old))
    assert r.created == 1
    assert "antigua" in {t.name for t in db.get_all_tags()}


def test_formato_desconocido(tmp_path, photo):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        services.import_tags(str(bad))
