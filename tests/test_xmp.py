"""Sidecars XMP (#16)."""

from pathlib import Path

import pytest

import database as db
import services
import xmp_sidecar as xmp

FOREIGN_XMP = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="digiKam">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/">
   <dc:subject><rdf:Bag><rdf:li>Vacaciones</rdf:li><rdf:li>Perro</rdf:li></rdf:Bag></dc:subject>
   <lr:hierarchicalSubject><rdf:Bag><rdf:li>Animales|Perro</rdf:li></rdf:Bag></lr:hierarchicalSubject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""


# ── Módulo xmp_sidecar ────────────────────────────────────────────────────────

def test_build_y_parse_ida_y_vuelta():
    tags = [("playa", "lugar"), ("rock&roll", "música"), ("<raro>", None)]
    parsed, managed = xmp.parse_xmp(xmp.build_xmp(tags))
    assert managed
    assert parsed == [("playa", "lugar"), ("rock&roll", "música"), ("<raro>", None)]


def test_lee_xmp_de_otro_programa():
    parsed, managed = xmp.parse_xmp(FOREIGN_XMP)
    assert not managed
    assert parsed == [("vacaciones", None), ("perro", "animales")]


def test_write_crea_actualiza_y_borra(tmp_path):
    foto = tmp_path / "IMG_1.JPG"; foto.write_bytes(b"x")
    side = tmp_path / "IMG_1.JPG.xmp"

    assert xmp.write_sidecar(str(foto), [("a", "c")]) == xmp.WriteResult.WRITTEN
    assert side.exists()
    assert xmp.write_sidecar(str(foto), [("a", "c")]) == xmp.WriteResult.UNCHANGED
    assert xmp.write_sidecar(str(foto), [("a", "c"), ("b", None)]) == xmp.WriteResult.WRITTEN
    assert xmp.read_sidecar(str(foto)) == [("a", "c"), ("b", None)]
    assert xmp.write_sidecar(str(foto), []) == xmp.WriteResult.REMOVED
    assert not side.exists()
    assert xmp.write_sidecar(str(foto), []) == xmp.WriteResult.UNCHANGED


def test_nunca_toca_xmp_ajeno(tmp_path):
    foto = tmp_path / "IMG_2.JPG"; foto.write_bytes(b"x")
    side = tmp_path / "IMG_2.JPG.xmp"
    side.write_text(FOREIGN_XMP, encoding="utf-8")

    assert xmp.write_sidecar(str(foto), [("nuevo", None)]) == xmp.WriteResult.SKIPPED_FOREIGN
    assert xmp.write_sidecar(str(foto), []) == xmp.WriteResult.SKIPPED_FOREIGN
    assert side.read_text(encoding="utf-8") == FOREIGN_XMP


def test_lee_convencion_lightroom(tmp_path):
    foto = tmp_path / "IMG_3.CR2"; foto.write_bytes(b"x")
    (tmp_path / "IMG_3.xmp").write_text(FOREIGN_XMP, encoding="utf-8")
    assert ("perro", "animales") in (xmp.read_sidecar(str(foto)) or [])


def test_carpeta_inexistente_no_crea_nada(tmp_path):
    ghost = tmp_path / "no_existe" / "a.jpg"
    assert xmp.write_sidecar(str(ghost), [("x", None)]) == xmp.WriteResult.ERROR
    assert not (tmp_path / "no_existe").exists()


def test_xmp_corrupto_no_rompe_la_lectura(tmp_path):
    foto = tmp_path / "a.jpg"; foto.write_bytes(b"x")
    (tmp_path / "a.jpg.xmp").write_text("<x:xmpmeta><roto", encoding="utf-8")
    assert xmp.read_sidecar(str(foto)) is None


# ── Integración con services ─────────────────────────────────────────────────

@pytest.fixture
def photo(db_path, tmp_path):
    db.init_db()

    def add(name: str) -> tuple[int, str]:
        f = tmp_path / name; f.write_bytes(b"x")
        return db.upsert_photo(str(f), name, 2020, 1, 1), str(f)

    return add


def test_desactivado_por_defecto_no_escribe(photo):
    pid, path = photo("a.jpg")
    services.add_tag(pid, "playa")
    assert not services.is_xmp_enabled()
    assert xmp.read_sidecar(path) is None


def test_activado_sigue_cada_cambio(photo):
    services.set_xmp_enabled(True)
    pid, path = photo("a.jpg")

    tag = services.add_tag(pid, "playa")
    assert xmp.read_sidecar(path) == [("playa", "general")]

    services.update_tag(tag.id, "costa", "lugar", "#000000")
    assert xmp.read_sidecar(path) == [("costa", "lugar")]

    services.bulk_add_tag([pid], "verano")
    assert {n for n, _ in xmp.read_sidecar(path) or []} == {"costa", "verano"}

    services.delete_tag(tag.id)
    assert [n for n, _ in xmp.read_sidecar(path) or []] == ["verano"]

    services.remove_tag(pid, db.get_tag_ids_by_name()["verano"])
    assert xmp.read_sidecar(path) is None        # sin etiquetas → sidecar eliminado


def test_sync_all_e_importar_desde_sidecars(db_path, photo):
    pid, path = photo("a.jpg")
    pid2, path2 = photo("b.jpg")
    services.add_tag(pid, "playa")
    services.set_xmp_enabled(True)
    r = services.sync_all_sidecars()
    assert r.written == 1

    # Un .xmp de otro programa junto a la segunda foto
    Path(path2 + ".xmp").write_text(FOREIGN_XMP, encoding="utf-8")

    # Perder las etiquetas en la DB y recuperarlas desde los .xmp
    db.remove_tag_from_photo(pid, db.get_tag_ids_by_name()["playa"])
    ri = services.import_from_sidecars()
    assert ri.with_xmp == 2
    assert {t.name for t in db.get_photo_tags(pid)} == {"playa"}
    assert {t.name for t in db.get_photo_tags(pid2)} == {"vacaciones", "perro"}
    assert next(t for t in db.get_all_tags() if t.name == "perro").category == "animales"
