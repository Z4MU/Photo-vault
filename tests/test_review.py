"""Fase 7: revisión por etiqueta (sí / no guardado), secuencia por tramos, teclas remapeables, retomar."""

import json
import sqlite3

import pytest

import database as db
import services
from services import GalleryQuery, ReviewState
from tests.conftest import make_legacy_db


@pytest.fixture
def setup(db_path):
    """6 fotos; 'viajes' › 'playa'. f0 tiene playa (hija), f1 tiene viajes."""
    db.init_db()
    ids = [db.upsert_photo(rf"G:\r\f{i}.jpg", f"f{i}.jpg", 2020, 1, 10 + i) for i in range(6)]
    viajes = services.create_tag("viajes")
    playa = services.create_tag("playa")
    services.update_tag(playa, "playa", "general", "#FFFFFF", parent_id=viajes)
    services.add_tag_by_id(ids[0], playa)
    services.add_tag_by_id(ids[1], viajes)
    return ids, viajes, playa


def pending(base: GalleryQuery, tag_id: int) -> list[int]:
    return services.get_gallery_ids(services.review_query(base, tag_id))


def test_migracion_v5(db_path):
    make_legacy_db(db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "tag_rejections" in tables
    assert db.get_schema_version() == db.SCHEMA_VERSION >= 5


def test_pendientes_excluye_las_que_tienen_la_etiqueta_o_una_hija(setup):
    ids, viajes, _playa = setup
    q = GalleryQuery(sort_field=services.SortField.FILENAME, sort_order=services.SortOrder.ASC)
    assert pending(q, viajes) == ids[2:]  # f0 (playa, hija) y f1 (viajes) ya están
    st = services.review_stats(q, viajes)
    assert (st.total, st.tagged, st.rejected, st.pending) == (6, 2, 0, 4)


def test_responder_y_retomar(setup):
    ids, viajes, _playa = setup
    q = GalleryQuery(sort_field=services.SortField.FILENAME, sort_order=services.SortOrder.ASC)
    services.review_answer(viajes, [ids[2]], [ids[3], ids[4]])
    assert viajes in {t.id for t in services.get_photo_tags(ids[2])}
    # Una sesión nueva empieza por lo que falta: solo f5
    assert pending(q, viajes) == [ids[5]]
    st = services.review_stats(q, viajes)
    assert (st.tagged, st.rejected, st.pending) == (3, 2, 1)
    # Con "volver a revisar" aparecen todas
    assert len(services.get_gallery_ids(services.review_query(q, viajes, include_reviewed=True))) == 6


def test_deshacer_deja_todo_como_estaba(setup):
    ids, viajes, _playa = setup
    before = services.review_answer(viajes, [ids[2]], [ids[3]])
    assert before == {ids[2]: ReviewState(False, False), ids[3]: ReviewState(False, False)}
    services.review_restore(viajes, before)
    assert viajes not in {t.id for t in services.get_photo_tags(ids[2])}
    assert db.get_rejected_ids(viajes, [ids[3]]) == set()


def test_rerevisar_con_no_quita_la_etiqueta_y_deshacer_la_devuelve(setup):
    ids, viajes, _playa = setup
    states = services.review_answer(viajes, [], [ids[1]])  # tenía viajes
    assert states[ids[1]] == ReviewState(True, False)
    assert viajes not in {t.id for t in services.get_photo_tags(ids[1])}
    assert db.get_rejected_ids(viajes, [ids[1]]) == {ids[1]}
    services.review_restore(viajes, states)
    assert viajes in {t.id for t in services.get_photo_tags(ids[1])}
    assert db.get_rejected_ids(viajes, [ids[1]]) == set()
    # Un "sí" después de un "no" quita el "no"
    services.review_answer(viajes, [], [ids[4]])
    services.review_answer(viajes, [ids[4]], [])
    assert db.get_rejected_ids(viajes, [ids[4]]) == set()


def test_los_no_se_borran_con_la_etiqueta_o_la_foto(setup):
    ids, viajes, playa = setup
    services.review_answer(playa, [], [ids[2], ids[3]])
    db.delete_photos([ids[2]], "prueba")
    assert db.get_rejected_ids(playa, ids) == {ids[3]}
    services.delete_tag(playa)
    assert db.get_connection().execute("SELECT COUNT(*) FROM tag_rejections").fetchone()[0] == 0


# ── Secuencia por tramos ──────────────────────────────────────────────────────


def test_lista_de_fotos_por_tramos(setup, monkeypatch):
    ids, _v, _p = setup
    monkeypatch.setattr(services.PhotoList, "CHUNK", 2)
    calls: list[int] = []
    real = db.get_photos_by_ids

    def spy(chunk):
        calls.append(len(chunk))
        return real(chunk)

    monkeypatch.setattr(db, "get_photos_by_ids", spy)
    order = list(reversed(ids))
    lst = services.PhotoList(order)
    assert lst.count() == 6 and calls == []
    assert [lst.photo_at(i).id for i in range(6)] == order  # type: ignore[union-attr]
    assert calls == [2, 2, 2]  # un acceso a la DB por tramo
    assert lst.photo_at(6) is None and lst.photo_at(-1) is None
    assert lst.index_of(ids[0]) == 5 and lst.index_of(999) is None


# ── Teclas ────────────────────────────────────────────────────────────────────


def test_teclas_de_fabrica_y_cambiadas(setup):
    km = services.get_keymap()
    assert km["review.yes"] == ["Right", "D"] and km["common.exit"] == ["Esc"]
    services.set_keymap({**km, "review.yes": ["L"], "inventada": ["X"]})
    km2 = services.get_keymap()
    assert km2["review.yes"] == ["L"] and "inventada" not in km2
    db.set_setting(services.KEYMAP_SETTING, "{no es json")
    assert services.get_keymap()["review.yes"] == ["Right", "D"]  # JSON roto: lo de fábrica


def test_teclas_de_etiquetas(setup):
    _ids, viajes, playa = setup
    defaults = services.get_tag_keys()
    assert list(defaults)[:2] == ["1", "2"] and len(defaults) <= services.DEFAULT_TAG_KEYS
    services.set_tag_keys({"Q": viajes, "Shift+1": playa})
    assert services.get_tag_keys() == {"Q": viajes, "Shift+1": playa}
    services.delete_tag(playa)
    assert services.get_tag_keys() == {"Q": viajes}  # la borrada desaparece
    services.reset_keys()
    first = db.get_all_tags()[: services.DEFAULT_TAG_KEYS]
    assert services.get_tag_keys() == {str(i + 1): t.id for i, t in enumerate(first)}


def test_conflictos_de_teclas(setup):
    _ids, viajes, playa = setup
    km = services.get_keymap()
    assert services.keymap_conflicts(km, services.get_tag_keys()) == []
    bad = {**km, "review.no": ["D"]}  # D ya es "sí"
    assert any("«D»" in p for p in services.keymap_conflicts(bad, {}))
    assert services.keymap_conflicts({**km, "grid.confirm": ["3"]}, {})  # 1–9 son de las celdas
    assert services.keymap_conflicts({**km, "grid.skip": ["Left"]}, {})  # flechas = foco
    assert services.keymap_conflicts({**km, "review.skip": ["Esc"]}, {})  # Esc es "salir" en todos
    assert services.keymap_conflicts(km, {"Right": viajes})  # tecla de etiqueta = "siguiente"
    # El mismo atajo en modos distintos no choca (Ctrl+Z en los tres)
    assert services.keymap_conflicts({**km, "multi.repeat": ["D"]}, {"Q": playa}) == []


# ── Retomar y recordar ────────────────────────────────────────────────────────


def test_retomar_posicion_y_configuracion(setup):
    ids, viajes, _p = setup
    q = GalleryQuery(folder=r"G:\r")
    assert services.resume_position(q) is None
    services.remember_position(q, ids[3])
    assert services.resume_position(q) == ids[3]
    assert services.resume_position(GalleryQuery()) is None  # otra consulta: no aplica
    services.set_quick_tag_setup({"mode": "grid", "tag_id": viajes})
    assert services.get_quick_tag_setup() == {"mode": "grid", "tag_id": viajes}


# ── Exportar / importar los "no" ──────────────────────────────────────────────


def test_exportar_e_importar_los_no(tmp_path, setup, monkeypatch):
    ids, viajes, _p = setup
    services.review_answer(viajes, [], [ids[3], ids[4]])
    out = tmp_path / "e.json"
    services.export_tags(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {a["filename"] for a in data["assignments"] if a.get("rejected") == ["viajes"]} == {
        "f3.jpg",
        "f4.jpg",
    }

    db.close_connection()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nueva.db")
    db.init_db()
    new_ids = [db.upsert_photo(rf"G:\r\f{i}.jpg", f"f{i}.jpg", 2020, 1, 10 + i) for i in range(6)]
    tid = services.create_tag("viajes")
    services.add_tag_by_id(new_ids[4], tid)  # aquí ya la tiene: el "no" no se importa
    services.import_tags(str(out))
    imported = db.resolve_tag_name("viajes")
    assert imported is not None and db.get_rejected_ids(imported, new_ids) == {new_ids[3]}
