"""Editar tags (#11), tags ocultos del sidebar (#10), tamaño de página (#9)."""

import pytest

import database as db
import services


@pytest.fixture(autouse=True)
def _db(db_path):
    db.init_db()


def _tag(name: str):
    return next(t for t in db.get_all_tags() if t.name == name)


# ── Editar etiquetas ──────────────────────────────────────────────────────────


def test_editar_tag_conserva_asignaciones():
    pid = db.upsert_photo(r"C:\a.jpg", "a.jpg", 2020, 1, 1)
    tag = _tag("meme")
    db.add_tag_to_photo(pid, tag.id)

    services.update_tag(tag.id, "  Memes ", "humor", "#123456")

    edited = db.get_tag(tag.id)
    assert edited is not None
    assert (edited.name, edited.category, edited.color) == ("memes", "humor", "#123456")
    assert "humor" in db.get_all_categories()
    assert [t.id for t in db.get_photo_tags(pid)] == [tag.id]


def test_editar_tag_nombre_duplicado():
    with pytest.raises(db.TagNameConflictError):
        services.update_tag(_tag("meme").id, "ANIME", "tipo", "#FFFFFF")


def test_editar_tag_mismo_nombre_otro_color():
    tag = _tag("meme")
    services.update_tag(tag.id, "meme", tag.category, "#000000")
    edited = db.get_tag(tag.id)
    assert edited is not None and edited.color == "#000000"


def test_editar_tag_nombre_vacio():
    with pytest.raises(ValueError):
        services.update_tag(_tag("meme").id, "   ", "tipo", "#FFFFFF")


def test_add_tag_devuelve_el_tag():
    pid = db.upsert_photo(r"C:\a.jpg", "a.jpg", 2020, 1, 1)
    tag = services.add_tag(pid, "Nuevo")
    assert tag.name == "nuevo"
    assert services.count_photos_with_tag(tag.id) == 1


# ── Tags ocultos del sidebar ─────────────────────────────────────────────────


def test_tags_ocultos_se_pueden_listar_para_restaurarlos():
    tag = _tag("meme")
    services.set_tag_sidebar_hidden(tag.id, True)

    visibles = [t.id for ts in services.get_sidebar_tags().values() for t in ts]
    todos = [t.id for ts in services.get_sidebar_tags(include_hidden=True).values() for t in ts]
    assert tag.id not in visibles
    assert tag.id in todos
    assert services.count_sidebar_hidden_tags() == 1


# ── Tamaño de miniaturas (slider de la galería) ──────────────────────────────


def test_tamano_de_miniatura_por_defecto():
    assert services.get_thumb_display_size() == services.THUMB_DISPLAY_DEFAULT


def test_tamano_de_miniatura_persiste():
    services.set_thumb_display_size(260)
    db.close_connection()
    db.init_db()
    assert services.get_thumb_display_size() == 260


@pytest.mark.parametrize("raw, expected", [("9999", 400), ("1", 100), ("basura", 200)])
def test_tamano_de_miniatura_se_acota(raw, expected):
    db.set_setting("thumb_size", raw)
    assert services.get_thumb_display_size() == expected


def test_contador_para_etiquetado_rapido():
    for i in range(4):
        db.upsert_photo(rf"C:\f\{i}.jpg", f"{i}.jpg", 2020, 1, 1)
    assert services.count_photos_for_tagging(untagged_only=True) == 4
    assert services.count_photos_for_tagging() == len(services.get_photos_for_tagging())
