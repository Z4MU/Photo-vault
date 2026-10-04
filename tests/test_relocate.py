"""Reubicar carpeta / unidad (#15) sin perder etiquetas."""

from pathlib import Path

import pytest

import database as db
import services


@pytest.fixture
def photo(db_path):
    db.init_db()

    def add(path: str, tags: tuple[str, ...] = ()) -> int:
        pid = db.upsert_photo(path, Path(path).name, 2020, 1, 1)
        for t in tags:
            db.add_tag_to_photo(pid, db.create_tag(t))
        return pid

    return add


def test_cambio_de_letra_de_unidad(photo):
    a = photo(r"G:\Fotos\2020\a.jpg", ("playa",))
    photo(r"G:\b.jpg")
    photo(r"H:\c.jpg")

    p = services.preview_relocation("G:\\", "E:\\")
    assert (p.count, p.conflicts) == (2, 0)
    assert services.apply_relocation(p) == (2, 0)

    paths = {x.path for x in db.get_photos()}
    assert paths == {r"E:\Fotos\2020\a.jpg", r"E:\b.jpg", r"H:\c.jpg"}
    assert [t.name for t in db.get_photo_tags(a)] == ["playa"]        # mismo registro, mismos tags


def test_mover_carpeta_a_otra_ruta(photo):
    photo(r"D:\Fotos\sub\a.jpg")
    photo(r"D:\Fotos2\b.jpg")         # carpeta hermana: no se toca
    p = services.preview_relocation(r"D:\Fotos", r"E:\Respaldo\Fotos")
    services.apply_relocation(p)
    assert {x.path for x in db.get_photos()} == {r"E:\Respaldo\Fotos\sub\a.jpg", r"D:\Fotos2\b.jpg"}
    assert next(x for x in db.get_photos() if x.path.startswith("E:")).filename == "a.jpg"


def test_fusiona_si_ya_se_reindexo_la_ruta_nueva(photo):
    photo(r"G:\a.jpg", ("viejo",))
    nuevo = photo(r"E:\a.jpg", ("nuevo",))
    p = services.preview_relocation("G:\\", "E:\\")
    assert p.conflicts == 1
    assert services.apply_relocation(p) == (0, 1)
    assert db.get_photo_count() == 1
    assert {t.name for t in db.get_photo_tags(nuevo)} == {"viejo", "nuevo"}


def test_vista_previa_no_cambia_nada(photo):
    photo(r"G:\a.jpg")
    services.preview_relocation("G:\\", "E:\\")
    assert [x.path for x in db.get_photos()] == [r"G:\a.jpg"]


def test_comprobacion_en_disco(tmp_path, photo):
    old = tmp_path / "viejo"; new = tmp_path / "nuevo"
    new.mkdir()
    for i in range(10):
        (new / f"{i}.jpg").write_bytes(b"x")
        photo(str(old / f"{i}.jpg"))

    good = services.preview_relocation(str(old), str(new))
    assert good.looks_right and good.sample_found == good.sample_size == 10

    bad = services.preview_relocation(str(old), str(tmp_path / "tipeo"))
    assert not bad.looks_right and bad.sample_found == 0


@pytest.mark.parametrize("old, new", [("G:\\", "G:\\"), ("D:\\Fotos", "d:\\fotos\\"), ("", "E:\\")])
def test_rutas_invalidas(photo, old, new):
    with pytest.raises(ValueError):
        services.preview_relocation(old, new)


def test_raices_indexadas(tmp_path, photo):
    photo(str(tmp_path / "a.jpg"))
    photo(r"Q:\b.jpg"); photo(r"Q:\c.jpg")
    roots = {r: (n, ok) for r, n, ok in services.get_indexed_roots()}
    assert roots["Q:\\"] == (2, False)
    assert roots[tmp_path.anchor] == (1, True)
