"""Indexador: fecha EXIF (#5), conteo nuevas/actualizadas (#12), cancelación, HEIC."""

import pytest
from PIL import Image

import database as db
import indexer


def _jpeg_with_dates(path, original: str | None = None, modified: str | None = None) -> None:
    img  = Image.new("RGB", (40, 30))
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


@pytest.mark.parametrize("value, expected", [
    ("2020:05:12 13:14:15", (2020, 5)),
    (b"2020:05:12 13:14:15", (2020, 5)),
    ("", None),
    ("    :  :     :  :  ", None),
    ("1850:01:01 00:00:00", None),
    (None, None),
])
def test_parse_exif_date(value, expected):
    assert indexer._parse_exif_date(value) == expected


# ── index_folder ──────────────────────────────────────────────────────────────

def _make_collection(root, n: int = 3):
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48)).save(root / f"IMG_2021050{i + 1}_120000.jpg")
    (root / "notas.txt").write_text("no es foto")


def test_cuenta_nuevas_y_actualizadas(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 3)

    assert indexer.index_folder(str(col)) == (3, 0, 0)
    assert indexer.index_folder(str(col)) == (0, 3, 0)

    photos = db.get_photos()
    assert len(photos) == 3
    assert {p.year for p in photos} == {2021}
    assert all(p.width == 64 and p.height == 48 for p in photos)


def test_cancelar_indexacion(tmp_path):
    db.init_db()
    col = tmp_path / "fotos"
    _make_collection(col, 5)
    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    added, updated, errors = indexer.index_folder(str(col), should_stop=stop_after_two)
    assert added == 2
    assert db.get_photo_count() == 2


def test_heic_soportado():
    pytest.importorskip("pillow_heif")
    assert indexer._HEIF_AVAILABLE
