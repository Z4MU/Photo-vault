"""Fase 4: migración v3, orden estable, consultas en SQL, duplicados por tamaño, miniaturas."""

import os
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

import database as db
import services
import thumbnail_cache as tc
from models import SortField, SortOrder
from tests.conftest import make_legacy_db


@pytest.fixture
def photo(db_path):
    db.init_db()

    def add(path: str, year=2020, month=1, size=100, mtime=None) -> int:
        return db.upsert_photo(path, Path(path).name, year, month, size, mtime=mtime)

    return add


# ── Migración v3 ──────────────────────────────────────────────────────────────


def test_migracion_v3_agrega_mtime_e_indices(db_path):
    make_legacy_db(db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
        idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert "mtime" in cols
    assert {"idx_photos_date", "idx_photos_filename", "idx_photos_filesize", "idx_photos_added"} <= idx
    assert "idx_photos_year" not in idx  # reemplazado por el compuesto


def test_orden_por_fecha_usa_el_indice(photo):
    plan = (
        db.get_connection()
        .execute(
            "EXPLAIN QUERY PLAN SELECT id FROM photos p ORDER BY "
            + db.sort_to_sql(SortField.DATE, SortOrder.DESC)
            + " LIMIT 100"
        )
        .fetchall()
    )
    detail = " ".join(str(r[-1]) for r in plan)
    assert "idx_photos_date" in detail and "TEMP B-TREE" not in detail


@pytest.mark.parametrize("field", list(SortField))
@pytest.mark.parametrize("order", list(SortOrder))
def test_paginacion_estable_con_repetidos(photo, field, order):
    # Mismo nombre, mes y tamaño en carpetas distintas: sin desempate por id,
    # una foto podía salir en dos páginas y otra en ninguna.
    ids = {photo(rf"D:\c{i}\IMG.jpg") for i in range(23)}
    seen: list[int] = []
    for offset in range(0, 30, 5):
        page = services.get_gallery_page(limit=5, offset=offset, sort_field=field, sort_order=order)
        seen.extend(p.id for p in page.photos)
    assert sorted(seen) == sorted(ids)


def test_etiquetado_rapido_no_tiene_tope(photo, monkeypatch):
    for i in range(12):
        photo(rf"D:\f\{i}.jpg")
    # Antes: limit=99_999 fijo. Se comprueba que pide "sin límite".
    called = {}
    real = db.get_photos

    def spy(**kw):
        called.update(kw)
        return real(**kw)

    monkeypatch.setattr(db, "get_photos", spy)
    assert len(services.get_photos_for_tagging()) == 12
    assert called["limit"] == -1


# ── Consultas agregadas en SQL ────────────────────────────────────────────────


def test_conteo_de_carpetas_igual_a_dirname(photo):
    paths = [
        r"G:\a.jpg",
        r"G:\Fotos\b.jpg",
        r"G:\Fotos\c.jpg",
        r"G:\Fotos\2020\d.jpg",
        r"D:\x y_z%\e.jpg",
        r"\\nas\fotos\f.jpg",
    ]
    for p in paths:
        photo(p)
    expected: dict[str, int] = {}
    for p in paths:
        expected[os.path.dirname(p)] = expected.get(os.path.dirname(p), 0) + 1
    assert services.get_indexed_folders() == sorted(expected.items())


def test_conteo_de_raices(photo):
    for p in (r"G:\a.jpg", r"G:\b\c.jpg", r"D:\d.jpg", r"\\nas\fotos\e.jpg"):
        photo(p)
    roots = dict(db.get_root_counts())
    assert roots["G:\\"] == 2 and roots["D:\\"] == 1
    assert roots[Path(r"\\nas\fotos\e.jpg").anchor] == 1


def test_totales(photo):
    photo(r"G:\a.jpg")
    photo(r"G:\b.jpg")
    photos, tags = services.get_totals()
    assert photos == 2 and tags == len(db.get_all_tags())


# ── Duplicados: solo se hashean tamaños repetidos ─────────────────────────────


def test_md5_solo_de_tamanos_repetidos(tmp_path, photo, monkeypatch):
    files = {"a.jpg": b"igual", "b.jpg": b"igual", "c.jpg": b"distinto-largo"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        photo(str(tmp_path / name), size=len(data))
    hashed: list[str] = []
    real = services._md5_of_file

    def spy(p: str, chunk: int = 65536) -> str | None:
        hashed.append(Path(p).name)
        return real(p)

    monkeypatch.setattr(services, "_md5_of_file", spy)

    assert services.compute_missing_md5s() == 2
    assert sorted(hashed) == ["a.jpg", "b.jpg"]  # c.jpg tiene tamaño único: imposible duplicado
    groups = services.get_duplicate_groups()
    assert len(groups) == 1 and groups[0].size == 2


# ── Miniaturas en lote ────────────────────────────────────────────────────────


def _real_photo(tmp_path, name, photo_fixture):
    p = tmp_path / name
    Image.new("RGB", (300, 200), (10, 120, 200)).save(p)
    return photo_fixture(str(p), mtime=p.stat().st_mtime)


def test_pregenerar_miniaturas(tmp_path, photo):
    ids = [_real_photo(tmp_path, f"{i}.jpg", photo) for i in range(6)]
    (tmp_path / "roto.jpg").write_bytes(b"x")
    ids.append(photo(str(tmp_path / "roto.jpg")))

    r = services.pregenerate_thumbnails(ids)
    assert (r.generated, r.failed, r.already_cached) == (6, 1, 0)
    for p in db.get_photos_by_ids(ids[:6]):
        assert tc.is_cached(p.path, mtime=p.mtime)
    # Segunda vez: todo en caché
    assert services.pregenerate_thumbnails(ids).already_cached == 6


def test_pregenerar_se_puede_cancelar(tmp_path, photo):
    ids = [_real_photo(tmp_path, f"{i}.jpg", photo) for i in range(20)]
    r = services.pregenerate_thumbnails(ids, should_stop=lambda: True)
    assert r.generated == 0


def test_pregenerar_cancela_a_mitad_y_reporta_progreso(tmp_path, photo, monkeypatch):
    monkeypatch.setattr(services, "THUMB_WORKERS", 1)  # tandas de 8
    ids = [_real_photo(tmp_path, f"{i}.jpg", photo) for i in range(30)]
    progress: list[tuple[int, int]] = []
    r = services.pregenerate_thumbnails(
        ids, progress_callback=lambda c, t: progress.append((c, t)), should_stop=lambda: len(progress) >= 2
    )
    assert progress[0] == (0, 30)
    assert r.generated == 8  # una tanda y se detuvo


def test_purga_no_borra_miniaturas_vigentes(tmp_path, photo):
    ids = [_real_photo(tmp_path, f"{i}.jpg", photo) for i in range(3)]
    services.pregenerate_thumbnails(ids)
    assert services.purge_cache_orphans() == 0
    services.deindex_folder(str(tmp_path))
    assert services.purge_cache_orphans() == 3
