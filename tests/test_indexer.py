"""Indexador: fecha EXIF (#5), conteo nuevas/actualizadas (#12), cancelación, HEIC."""

import os
import time

import pytest
from PIL import Image

import database as db
import indexer


def _jpeg_with_dates(path, original: str | None = None, modified: str | None = None) -> None:
    img = Image.new("RGB", (40, 30))
    exif = Image.Exif()
    if modified:
        exif[indexer._TAG_DATETIME] = modified
    if original:
        exif.get_ifd(indexer._EXIF_IFD)[indexer._TAG_DATETIME_ORIGINAL] = original
    img.save(path, format="JPEG", exif=exif)


# ── Fecha EXIF ────────────────────────────────────────────────────────────────


def test_prioriza_datetime_original_sobre_modificacion(tmp_path):
    p = tmp_path / "editada.jpg"
    _jpeg_with_dates(p, original="2015:06:20 10:00:00", modified="2023:01:05 18:00:00")
    assert indexer._extract_date_from_exif(str(p)) == (2015, 6)


def test_usa_datetime_si_no_hay_original(tmp_path):
    p = tmp_path / "solo_mod.jpg"
    _jpeg_with_dates(p, modified="2019:11:02 08:00:00")
    assert indexer._extract_date_from_exif(str(p)) == (2019, 11)


def test_fecha_exif_invalida_pasa_a_la_siguiente(tmp_path):
    p = tmp_path / "ceros.jpg"
    _jpeg_with_dates(p, original="0000:00:00 00:00:00", modified="2018:03:01 00:00:00")
    assert indexer._extract_date_from_exif(str(p)) == (2018, 3)


def test_sin_exif(tmp_path):
    p = tmp_path / "plano.png"
    Image.new("RGB", (10, 10)).save(p)
    assert indexer._extract_date_from_exif(str(p)) == (None, None)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2020:05:12 13:14:15", (2020, 5)),
        (b"2020:05:12 13:14:15", (2020, 5)),
        ("", None),
        ("    :  :     :  :  ", None),
        ("1850:01:01 00:00:00", None),
        (None, None),
    ],
)
def test_parse_exif_date(value, expected):
    assert indexer._parse_exif_date(value) == expected


# ── index_folder ──────────────────────────────────────────────────────────────


def _make_collection(root, n: int = 3):
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48)).save(root / f"IMG_2021050{i + 1}_120000.jpg")
    (root / "notas.txt").write_text("no es foto")


def _counts(r: indexer.IndexResult) -> tuple[int, int, int, int]:
    return (r.added, r.updated, r.unchanged, r.errors)


def test_indexacion_incremental(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 3)

    first = indexer.index_folder(str(col))
    assert _counts(first) == (3, 0, 0, 0)
    assert len(first.new_ids) == 3
    # Segunda vez sin cambios: no se vuelve a abrir ningún archivo
    assert _counts(indexer.index_folder(str(col))) == (0, 0, 3, 0)

    photos = db.get_photos()
    assert len(photos) == 3
    assert {p.year for p in photos} == {2021}
    assert all(p.width == 64 and p.height == 48 and p.mtime for p in photos)


def test_no_abre_archivos_sin_cambios(tmp_path, monkeypatch):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 3)
    indexer.index_folder(str(col))

    def no_abrir(*a, **k):
        raise AssertionError("no debía leer el archivo")

    monkeypatch.setattr(indexer, "read_media_info", no_abrir)
    assert indexer.index_folder(str(col)).unchanged == 3


def test_archivo_modificado_se_relee_e_invalida_md5(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 2)
    indexer.index_folder(str(col))
    target = sorted(col.glob("*.jpg"))[0]
    pid = next(p.id for p in db.get_photos() if p.path == str(target))
    db.update_photo_md5(pid, "viejo")

    Image.new("RGB", (100, 80)).save(target)  # Cambia contenido, tamaño y mtime
    os.utime(target, (time.time() + 5, time.time() + 5))
    r = indexer.index_folder(str(col))
    assert _counts(r) == (0, 1, 1, 0)

    photo = db.get_photo_by_id(pid)
    assert photo is not None
    assert (photo.width, photo.height) == (100, 80)
    assert photo.md5 is None  # El md5 viejo ya no vale


def test_md5_se_conserva_si_no_cambio(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 1)
    indexer.index_folder(str(col))
    pid = db.get_photos()[0].id
    db.update_photo_md5(pid, "abc")
    indexer.index_folder(str(col), force=True)  # Relee todo, pero el archivo es igual
    assert db.get_photo_by_id(pid).md5 == "abc"  # type: ignore[union-attr]


def test_registros_sin_mtime_solo_reciben_el_mtime(tmp_path, monkeypatch):
    """Registros de antes de v3: la primera indexación no debe releer toda la colección."""
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 3)
    for f in col.glob("*.jpg"):
        db.upsert_photo(str(f), f.name, 2000, 1, f.stat().st_size)  # mtime=None, como en v2

    monkeypatch.setattr(indexer, "read_media_info", lambda f: (_ for _ in ()).throw(AssertionError("leyó")))
    r = indexer.index_folder(str(col))
    assert _counts(r) == (0, 0, 3, 0)
    assert all(p.mtime for p in db.get_photos())
    assert {p.year for p in db.get_photos()} == {2000}  # No se tocó nada más


def test_rutas_iguales_a_las_de_versiones_anteriores(tmp_path):
    """Las rutas deben coincidir EXACTO con las viejas (str(Path)), o la DB se duplicaría."""
    db.init_db()
    col = tmp_path / "fotos" / "sub"
    _make_collection(col, 2)
    old_style = sorted(str(p) for p in (tmp_path / "fotos").rglob("*.jpg"))
    # La carpeta llega con barras normales, como la devuelve QFileDialog
    indexer.index_folder((tmp_path / "fotos").as_posix())
    assert sorted(p.path for p in db.get_photos()) == old_style


def test_lotes_grandes(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "BATCH_SIZE", 7)
    db.init_db()
    col = tmp_path / "fotos"
    col.mkdir()
    for i in range(30):
        Image.new("RGB", (8, 8)).save(col / f"{i:03d}.png")
    assert indexer.index_folder(str(col)).added == 30
    assert db.get_photo_count() == 30


def test_cancelar_indexacion_guarda_lo_leido(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 5)
    seen = []

    r = indexer.index_folder(
        str(col),
        progress_callback=lambda c, t, p: seen.append(c),
        should_stop=lambda: len(seen) >= 2,
    )
    assert r.cancelled and r.added == 2
    assert db.get_photo_count() == 2


def test_archivo_ilegible_cuenta_error_sin_detener(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 2)
    (col / "roto.jpg").write_bytes(b"no es imagen")
    r = indexer.index_folder(str(col))
    # Una imagen ilegible se indexa igual (sin dimensiones), no es un error fatal
    assert r.added == 3 and r.errors == 0
    roto = next(p for p in db.get_photos() if p.filename == "roto.jpg")
    assert roto.width is None


def test_heic_soportado():
    pytest.importorskip("pillow_heif")
    assert indexer._HEIF_AVAILABLE
