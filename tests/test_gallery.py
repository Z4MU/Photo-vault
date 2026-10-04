"""Galería: filtros, ocultos, búsqueda, orden, paginación; estadísticas y categorías."""

import pytest

import database as db
import services
from models import SortField, SortOrder


@pytest.fixture
def col(db_path):
    """Colección pequeña: 6 fotos con etiquetas variadas."""
    db.init_db()

    def add(name, year, month, size, tags=(), video=False):
        pid = db.upsert_photo(rf"D:\F\{name}", name, year, month, size,
                              media_type="video" if video else "image")
        for t in tags:
            db.add_tag_to_photo(pid, db.create_tag(t))
        return pid

    ids = {
        "a": add("a_playa.jpg",   2019, 7, 300, ("playa", "familia")),
        "b": add("b_playa.jpg",   2020, 1, 100, ("playa",)),
        "c": add("c_casa.jpg",    2021, 5, 500, ("familia",)),
        "d": add("d_secreto.jpg", 2022, 2, 200, ("privado", "playa")),
        "e": add("e_sin.jpg",     2018, 3, 50),
        "f": add("f_video.mp4",   2023, 9, 900, ("playa",), video=True),
    }
    tag_id = db.get_tag_ids_by_name()
    return ids, tag_id


def _names(page) -> list[str]:
    return [p.filename[0] for p in page.photos]


def test_tags_ocultos_no_aparecen(col):
    ids, tag = col
    services.set_tag_hidden(tag["privado"], True)
    page = services.get_gallery_page(limit=100)
    assert page.total == 5
    assert "d" not in _names(page)


def test_filtro_por_tags_es_and(col):
    _ids, tag = col
    page = services.get_gallery_page(tag_ids=[tag["playa"], tag["familia"]])
    assert _names(page) == ["a"]


def test_busqueda_por_nombre(col):
    page = services.get_gallery_page(search="playa")
    assert sorted(_names(page)) == ["a", "b"]


@pytest.mark.parametrize("field, order, expected", [
    (SortField.DATE,     SortOrder.DESC, ["f", "d", "c", "b", "a", "e"]),
    (SortField.DATE,     SortOrder.ASC,  ["e", "a", "b", "c", "d", "f"]),
    (SortField.FILESIZE, SortOrder.DESC, ["f", "c", "a", "d", "b", "e"]),
    (SortField.FILENAME, SortOrder.ASC,  ["a", "b", "c", "d", "e", "f"]),
])
def test_ordenamiento(col, field, order, expected):
    assert _names(services.get_gallery_page(sort_field=field, sort_order=order)) == expected


def test_paginacion(col):
    p1 = services.get_gallery_page(limit=4, offset=0, sort_field=SortField.FILENAME,
                                   sort_order=SortOrder.ASC)
    p2 = services.get_gallery_page(limit=4, offset=4, sort_field=SortField.FILENAME,
                                   sort_order=SortOrder.ASC)
    assert (p1.total, p1.total_pages, p1.page_number) == (6, 2, 1)
    assert p1.has_next and not p1.has_prev
    assert p2.has_prev and not p2.has_next and p2.page_number == 2
    assert _names(p1) + _names(p2) == ["a", "b", "c", "d", "e", "f"]


def test_sin_etiquetar(col):
    assert _names(services.get_gallery_page(untagged_only=True)) == ["e"]


def test_etiquetado_rapido_orden_cronologico(col):
    _ids, tag = col
    photos = services.get_photos_for_tagging(tag_ids=[tag["playa"]])
    assert [p.filename[0] for p in photos] == ["a", "b", "d", "f"]


def test_estadisticas(col):
    s = services.get_stats()
    assert s.total_photos == 6
    assert s.by_type == {"image": 5, "video": 1}
    assert dict(s.years)[2019] == 1
    assert s.top_tags[0] == ("playa", 4)
    assert (2023, 9, 1) in s.by_month


def test_etiquetado_en_lote(col):
    ids, tag = col
    assert services.bulk_add_tag([ids["c"], ids["e"]], "  Nuevo ") == 2
    nuevo = db.get_tag_ids_by_name()["nuevo"]
    assert services.count_photos_with_tag(nuevo) == 2
    assert services.bulk_remove_tag([ids["c"]], nuevo) == 1
    assert services.count_photos_with_tag(nuevo) == 1
    assert services.bulk_add_tag([], "x") == 0 and services.bulk_add_tag([ids["a"]], "  ") == 0


def test_categorias_renombrar_y_eliminar(col):
    services.update_tag(db.get_tag_ids_by_name()["playa"], "playa", "lugares", "#000000")
    services.rename_category("lugares", "Sitios")
    assert db.get_tag(db.get_tag_ids_by_name()["playa"]).category == "sitios"  # type: ignore[union-attr]
    services.delete_category("sitios")
    assert db.get_tag(db.get_tag_ids_by_name()["playa"]).category == "general"  # type: ignore[union-attr]
    assert "sitios" not in services.get_all_categories()
    assert services.create_category("Nueva") is True
    assert services.create_category("nueva") is False


def test_carpetas_indexadas(col):
    db.upsert_photo(r"D:\F\sub\x.jpg", "x.jpg", 2020, 1, 1)
    assert services.get_indexed_folders() == [(r"D:\F", 6), (r"D:\F\sub", 1)]
