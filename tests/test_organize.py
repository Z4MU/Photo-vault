"""Fase 6: valoración, favoritas, notas, filtros AND/OR/NOT y por atributos, jerarquía, alias, fusión, búsquedas."""

import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

import database as db
import indexer
import services
import xmp_sidecar as xmp
from models import SortField, SortOrder
from services import GalleryQuery
from tests.conftest import make_legacy_db


@pytest.fixture
def add(db_path):
    db.init_db()

    def _add(name: str, year=2020, month=1, size=100, w=None, h=None, kind="image", duration=None) -> int:
        return db.upsert_photo(rf"G:\f\{name}", name, year, month, size, w, h, kind, duration)

    return _add


def names(q: GalleryQuery) -> set[str]:
    return {p.filename for p in services.get_gallery_chunk(q, 0, 1000)}


# ── Migración v4 ──────────────────────────────────────────────────────────────


def test_migracion_v4(db_path):
    make_legacy_db(db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
        tag_cols = {r[1] for r in conn.execute("PRAGMA table_info(tags)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        row = conn.execute("SELECT rating, favorite, note FROM photos").fetchone()
    finally:
        conn.close()
    assert {"rating", "favorite", "note"} <= cols and "parent_id" in tag_cols
    assert {"tag_aliases", "saved_searches"} <= tables
    assert row == (0, 0, None)  # la foto de la DB vieja queda sin valorar


def test_orden_por_valoracion_usa_indice(add):
    plan = " ".join(
        str(r[-1])
        for r in db.get_connection().execute(
            "EXPLAIN QUERY PLAN SELECT id FROM photos p ORDER BY "
            + db.sort_to_sql(SortField.RATING, SortOrder.DESC)
            + " LIMIT 50"
        )
    )
    assert "idx_photos_rating" in plan and "TEMP B-TREE" not in plan


# ── Valoración, favoritas, notas ──────────────────────────────────────────────


def test_valoracion_favorita_y_nota(add):
    a, b, c = add("a.jpg"), add("b.jpg"), add("c.jpg")
    services.set_rating([a, b], 4)
    services.set_rating([b], 9)  # se acota a 5
    assert services.toggle_favorite([a, c]) is True
    assert services.toggle_favorite([a, c]) is False  # todas lo eran: se quitan
    services.toggle_favorite([a])
    services.set_note(c, "  cumpleaños de Ana  ")
    pa, pb, pc = (db.get_photo_by_id(i) for i in (a, b, c))
    assert pa and pb and pc
    assert (pa.rating, pa.favorite, pa.stars) == (4, True, "★★★★☆")
    assert pb.rating == 5 and pc.note == "cumpleaños de Ana"
    services.set_note(c, "   ")
    assert db.get_photo_by_id(c).note is None  # type: ignore[union-attr]


def test_filtros_de_valoracion_favoritas_y_notas(add):
    a, b, c = add("a.jpg"), add("b.jpg"), add("c.jpg")
    services.set_rating([a], 3)
    services.set_rating([b], 5)
    services.toggle_favorite([c])
    services.set_note(c, "playa con Ana")
    assert names(GalleryQuery(min_rating=4)) == {"b.jpg"}
    assert names(GalleryQuery(min_rating=3)) == {"a.jpg", "b.jpg"}
    assert names(GalleryQuery(favorites_only=True)) == {"c.jpg"}
    assert names(GalleryQuery(has_note=True)) == {"c.jpg"}
    assert names(GalleryQuery(search="ana")) == {"c.jpg"}  # busca también en la nota
    rated = [p.filename for p in services.get_gallery_chunk(GalleryQuery(sort_field=SortField.RATING), 0, 9)]
    assert rated[:2] == ["b.jpg", "a.jpg"]


def test_filtros_por_atributos(add):
    add("ancha.jpg", 2018, w=4000, h=3000)
    add("alta.jpg", 2019, w=3000, h=4000)
    add("cuadrada.jpg", 2020, w=1000, h=990)
    add("chica.jpg", 2021, w=800, h=600)
    add("corto.mp4", 2020, kind="video", duration=10)
    add("largo.mp4", 2022, kind="video", duration=600)
    add("sin_fecha.jpg", None, None)
    assert names(GalleryQuery(media_type="video")) == {"corto.mp4", "largo.mp4"}
    assert names(GalleryQuery(media_type="image", year_from=2019, year_to=2020)) == {
        "alta.jpg",
        "cuadrada.jpg",
    }
    assert names(GalleryQuery(orientation="landscape")) == {"ancha.jpg", "chica.jpg"}
    assert names(GalleryQuery(orientation="portrait")) == {"alta.jpg"}
    assert names(GalleryQuery(orientation="square")) == {"cuadrada.jpg"}
    assert names(GalleryQuery(min_megapixels=8)) == {"ancha.jpg", "alta.jpg"}
    assert names(GalleryQuery(max_duration=30)) == {"corto.mp4"}
    assert names(GalleryQuery(min_duration=300)) == {"largo.mp4"}
    assert GalleryQuery(min_rating=2, favorites_only=True, year_from=2000).attribute_filter_count() == 3


# ── Etiquetas: AND / OR / NOT y jerarquía ─────────────────────────────────────


@pytest.fixture
def tagged(add):
    """viajes › playa ; familia. a: playa+familia, b: viajes, c: familia, d: nada."""
    ids = {n: add(f"{n}.jpg") for n in "abcd"}
    playa = services.add_tag(ids["a"], "playa")
    familia = services.add_tag(ids["a"], "familia")
    viajes = services.add_tag(ids["b"], "viajes")
    services.add_tag(ids["c"], "familia")
    services.update_tag(playa.id, "playa", playa.category, playa.color, parent_id=viajes.id)
    return ids, {"playa": playa.id, "familia": familia.id, "viajes": viajes.id}


def test_and_or_not(tagged):
    _ids, t = tagged
    assert names(GalleryQuery(tag_ids=(t["playa"], t["familia"]))) == {"a.jpg"}
    assert names(GalleryQuery(tag_ids=(t["playa"], t["familia"]), match_any=True)) == {"a.jpg", "c.jpg"}
    assert names(GalleryQuery(exclude_tag_ids=(t["familia"],))) == {"b.jpg", "d.jpg"}
    assert names(GalleryQuery(tag_ids=(t["familia"],), exclude_tag_ids=(t["playa"],))) == {"c.jpg"}


def test_padre_incluye_a_sus_hijas(tagged):
    _ids, t = tagged
    assert names(GalleryQuery(tag_ids=(t["viajes"],))) == {"a.jpg", "b.jpg"}  # a tiene playa (hija)
    assert names(GalleryQuery(exclude_tag_ids=(t["viajes"],))) == {"c.jpg", "d.jpg"}
    assert services.tag_path_names()[t["playa"]] == ["viajes", "playa"]
    assert services.tag_descendants()[t["viajes"]] == {t["playa"]}


def test_etiqueta_borrada_en_una_consulta_se_ignora(tagged):
    _ids, t = tagged
    q = GalleryQuery(tag_ids=(t["familia"],))
    services.delete_tag(t["familia"])
    assert services.count_gallery(q) == 4  # no filtra por algo que ya no existe


def test_padre_sin_ciclos_y_al_borrar_quedan_huerfanas(tagged):
    _ids, t = tagged
    with pytest.raises(ValueError):
        services.update_tag(t["viajes"], "viajes", "general", "#FFFFFF", parent_id=t["playa"])
    assert db.get_tag(t["viajes"]).name == "viajes"  # type: ignore[union-attr]
    services.delete_tag(t["viajes"])
    assert db.get_tag(t["playa"]).parent_id is None  # type: ignore[union-attr]


def test_alias(tagged):
    ids, t = tagged
    services.update_tag(t["playa"], "playa", "lugar", "#00AAFF", aliases=["Costa", "mar", "costa"])
    assert services.get_tag_aliases()[t["playa"]] == ["costa", "mar"]
    services.add_tag(ids["d"], "MAR")  # el alias agrega la etiqueta real
    assert [x.name for x in services.get_photo_tags(ids["d"])] == ["playa"]
    services.bulk_add_tag([ids["c"]], "costa")
    assert "playa" in [x.name for x in services.get_photo_tags(ids["c"])]
    with pytest.raises(db.TagNameConflictError):  # alias = nombre de otra etiqueta
        services.update_tag(t["playa"], "playa", "lugar", "#00AAFF", aliases=["familia"])
    with pytest.raises(db.TagNameConflictError):  # nombre = alias de otra
        services.update_tag(t["familia"], "mar", "tema", "#FFFFFF")
    assert db.get_tag(t["familia"]).name == "familia"  # type: ignore[union-attr]


def test_fusionar(tagged):
    ids, t = tagged
    sid = services.save_search(
        "con playa", GalleryQuery(tag_ids=(t["playa"],), exclude_tag_ids=(t["familia"],))
    )
    n = services.merge_tags(t["familia"], t["viajes"])
    assert n == 2
    assert db.get_tag(t["familia"]) is None
    assert {p.filename for p in services.get_gallery_chunk(GalleryQuery(tag_ids=(t["viajes"],)), 0, 9)} == {
        "a.jpg",
        "b.jpg",
        "c.jpg",
    }
    assert db.resolve_tag_name("familia") == t["viajes"]  # el nombre viejo queda como alias
    saved = next(s for s in services.list_saved_searches() if s.id == sid)
    assert saved.query["exclude_tag_ids"] == [t["viajes"]]


def test_fusionar_en_una_descendiente_no_crea_ciclos(tagged):
    _ids, t = tagged
    services.merge_tags(t["viajes"], t["playa"])  # playa era hija de viajes
    assert db.get_tag(t["playa"]).parent_id is None  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        services.merge_tags(t["playa"], t["playa"])


def test_contadores_por_etiqueta(tagged):
    _ids, t = tagged
    counts = services.get_tag_photo_counts()
    assert counts[t["familia"]] == 2 and counts[t["playa"]] == 1


# ── Búsquedas guardadas ───────────────────────────────────────────────────────


def test_consulta_ida_y_vuelta():
    q = GalleryQuery(
        tag_ids=(3, 1),
        exclude_tag_ids=(7,),
        match_any=True,
        search="x",
        folder=r"G:\Fotos",
        sort_field=SortField.RATING,
        sort_order=SortOrder.ASC,
        media_type="video",
        year_from=2010,
        min_megapixels=8.0,
        orientation="portrait",
        max_duration=30.0,
        min_rating=3,
        favorites_only=True,
        untagged_only=True,
        has_note=True,
    )
    assert GalleryQuery.from_dict(json.loads(json.dumps(q.to_dict()))) == q
    assert GalleryQuery.from_dict({"sort_field": "NO_EXISTE", "otra_cosa": 1}) == GalleryQuery()


def test_busquedas_guardadas(add):
    add("a.jpg")
    sid = services.save_search("Mías", GalleryQuery(min_rating=1))
    assert services.saved_search_exists("mías")
    services.save_search("MÍAS", GalleryQuery(favorites_only=True))  # mismo nombre: reemplaza
    saved = services.list_saved_searches()
    assert len(saved) == 1 and saved[0].id == sid and saved[0].query.get("favorites_only") is True
    services.save_search("Otra", GalleryQuery())
    with pytest.raises(ValueError):
        services.rename_saved_search(sid, "otra")
    services.rename_saved_search(sid, "Favs")
    services.delete_saved_search(sid)
    assert [s.name for s in services.list_saved_searches()] == ["Otra"]
    for _name, q in services.BUILTIN_ALBUMS:
        services.count_gallery(q)  # todos son consultas válidas


# ── Nada se pierde: papelera, reubicar, exportar, XMP ─────────────────────────


def test_papelera_conserva_valoracion_y_nota(add):
    a = add("a.jpg")
    services.set_rating([a], 5)
    services.toggle_favorite([a])
    services.set_note(a, "importante")
    db.delete_photos([a], "prueba")
    batch = db.list_trash_batches()[0].batch_id
    db.restore_trash_batch(batch)
    p = db.get_photos(limit=-1)[0]
    assert (p.rating, p.favorite, p.note) == (5, True, "importante")


def test_reubicar_fusionando_conserva_valoracion(add):
    old = db.upsert_photo(r"D:\Fotos\a.jpg", "a.jpg", 2020, 1, 1)
    services.set_rating([old], 4)
    services.set_note(old, "nota vieja")
    new = db.upsert_photo(r"G:\Fotos\a.jpg", "a.jpg", 2020, 1, 1)  # se re-indexó en la unidad nueva
    preview = services.preview_relocation(r"D:\Fotos", r"G:\Fotos")
    services.apply_relocation(preview)
    p = db.get_photo_by_id(new)
    assert p is not None and (p.rating, p.note) == (4, "nota vieja")


def test_exportar_e_importar_v3(tmp_path, tagged, monkeypatch):
    ids, t = tagged
    services.set_rating([ids["a"]], 4)
    services.toggle_favorite([ids["d"]])
    services.set_note(ids["d"], "sin etiquetas pero con nota")
    services.update_tag(t["playa"], "playa", "lugar", "#00AAFF", parent_id=t["viajes"], aliases=["costa"])
    out = tmp_path / "e.json"
    s = services.export_tags(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 3 and s.assignments == 4
    playa = next(x for x in data["tags"] if x["name"] == "playa")
    assert playa["parent"] == "viajes" and playa["aliases"] == ["costa"]

    # DB nueva con las mismas fotos, sin nada
    db.close_connection()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nueva.db")
    db.init_db()
    for n in "abcd":
        db.upsert_photo(rf"G:\f\{n}.jpg", f"{n}.jpg", 2020, 1, 100)
    r = services.import_tags(str(out))
    assert r.extras_added == 2
    by_name = {p.filename: p for p in db.get_photos(limit=-1)}
    assert by_name["a.jpg"].rating == 4 and by_name["d.jpg"].favorite and by_name["d.jpg"].note
    new_playa = db.resolve_tag_name("playa")
    assert db.get_tag(new_playa).parent_id == db.resolve_tag_name("viajes")  # type: ignore[arg-type,union-attr]
    assert db.resolve_tag_name("costa") == new_playa
    # Importar dos veces no cambia nada
    assert services.import_tags(str(out)).extras_added == 0


def test_xmp_valoracion_y_jerarquia(tmp_path, add, monkeypatch):
    foto = tmp_path / "a.jpg"
    foto.write_bytes(b"x")
    pid = db.upsert_photo(str(foto), "a.jpg", 2020, 1, 1)
    services.set_xmp_enabled(True)
    viajes = services.create_tag("viajes", "lugar")
    services.add_tag(pid, "playa")
    playa_id = db.resolve_tag_name("playa")
    assert playa_id is not None
    services.update_tag(playa_id, "playa", "lugar", "#00AAFF", parent_id=viajes)
    services.set_rating([pid], 3)
    text = xmp.sidecar_path(str(foto)).read_text(encoding="utf-8")
    assert "lugar|viajes|playa" in text
    tags, managed, rating = xmp.parse_xmp_full(text)
    assert tags == [("playa", "lugar")] and managed and rating == 3
    # Sin cambios: no se reescribe aunque la categoría lleve el padre
    assert services._write_photo_sidecar(pid) == xmp.WriteResult.UNCHANGED
    # Importar desde .xmp trae la valoración a una foto sin valorar
    db.set_rating([pid], 0)
    r = services.import_from_sidecars()
    assert r.ratings_added == 1 and db.get_photo_by_id(pid).rating == 3  # type: ignore[union-attr]


def test_xmp_solo_valoracion(tmp_path):
    foto = tmp_path / "b.jpg"
    foto.write_bytes(b"x")
    assert xmp.write_sidecar(str(foto), [], rating=2) == xmp.WriteResult.WRITTEN
    assert xmp.read_sidecar_full(str(foto)) == ([], 2)
    assert xmp.write_sidecar(str(foto), [], rating=0) == xmp.WriteResult.REMOVED


def test_xmp_valoracion_de_lightroom_como_elemento():
    text = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:Rating>4</xmp:Rating>'
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    assert xmp.parse_xmp_full(text)[2] == 4


# ── Indexador: orientación EXIF ───────────────────────────────────────────────


def test_dimensiones_con_la_orientacion_aplicada(tmp_path, db_path):
    db.init_db()
    p = tmp_path / "celular.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # girada 90°
    Image.new("RGB", (400, 300)).save(p, exif=exif)
    indexer.index_folder(str(tmp_path))
    photo = db.get_photos(limit=-1)[0]
    assert (photo.width, photo.height) == (300, 400)
    assert names(GalleryQuery(orientation="portrait")) == {"celular.jpg"}


def test_releer_todo_actualiza_registros_viejos(tmp_path, db_path):
    db.init_db()
    p = tmp_path / "celular.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (400, 300)).save(p, exif=exif)
    st = p.stat()
    # Como lo guardaba la versión anterior: sin girar
    db.upsert_photo(str(p), p.name, 2020, 1, st.st_size, 400, 300, mtime=st.st_mtime)
    assert indexer.index_folder(str(tmp_path)).unchanged == 1  # incremental: no lo relee
    assert indexer.index_folder(str(tmp_path), force=True).updated == 1
    assert Path(db.get_photos(limit=-1)[0].path) == p and db.get_photos(limit=-1)[0].height == 400
