"""
Mantenimiento: filtro de carpeta exacto (#7), des-indexar, archivos
faltantes con protección de unidades (#1), duplicados a la Papelera (#2).
"""

from pathlib import Path

import pytest

import database as db
import services


@pytest.fixture
def photos(db_path):
    db.init_db()

    def add(path: str) -> int:
        return db.upsert_photo(path, Path(path).name, 2020, 1, 100)

    return add


def _paths(**filters) -> set[str]:
    return {p.path for p in db.get_photos(limit=1000, **filters)}


# ── Filtro de carpeta (#7) ────────────────────────────────────────────────────

def test_filtro_carpeta_no_incluye_hermanas(photos):
    photos(r"D:\Fotos\a.jpg")
    photos(r"D:\Fotos\sub\b.jpg")
    photos(r"D:\Fotos2\c.jpg")
    photos(r"D:\FotosViejas\d.jpg")
    assert _paths(folder=r"D:\Fotos") == {r"D:\Fotos\a.jpg", r"D:\Fotos\sub\b.jpg"}
    assert db.get_photo_count(folder=r"D:\Fotos") == 2


def test_filtro_carpeta_acepta_barra_final_y_barras_normales(photos):
    photos(r"D:\Fotos\a.jpg")
    photos(r"D:\Fotos2\c.jpg")
    assert _paths(folder="D:\\Fotos\\") == {r"D:\Fotos\a.jpg"}
    assert _paths(folder="D:/Fotos") == {r"D:\Fotos\a.jpg"}


def test_comodines_en_la_ruta_son_literales(photos):
    photos(r"D:\mis_fotos\a.jpg")
    photos(r"D:\misXfotos\b.jpg")      # "_" como comodín coincidiría con esto
    photos(r"D:\100%\c.jpg")
    photos(r"D:\100abc\d.jpg")         # "%" como comodín coincidiría con esto
    assert _paths(folder=r"D:\mis_fotos") == {r"D:\mis_fotos\a.jpg"}
    assert _paths(folder=r"D:\100%") == {r"D:\100%\c.jpg"}


def test_raiz_de_unidad(photos):
    photos(r"G:\a.jpg")
    photos(r"G:\Fotos\b.jpg")
    photos(r"H:\c.jpg")
    assert _paths(folder="G:\\") == {r"G:\a.jpg", r"G:\Fotos\b.jpg"}


def test_deindex_folder_no_toca_hermanas(photos):
    a = photos(r"D:\Fotos\a.jpg")
    photos(r"D:\Fotos2\c.jpg")
    tag = db.create_tag("playa")
    db.add_tag_to_photo(a, tag)

    assert services.preview_deindex_folder(r"D:\Fotos") == (1, 1)
    assert services.deindex_folder(r"D:\Fotos") == 1
    assert _paths() == {r"D:\Fotos2\c.jpg"}


# ── Archivos faltantes (#1) ───────────────────────────────────────────────────

def test_find_missing_no_borra_nada(tmp_path, photos):
    real = tmp_path / "existe.jpg"; real.write_bytes(b"x")
    photos(str(real))
    photos(str(tmp_path / "borrada.jpg"))

    report = services.find_missing_files()
    assert report.count == 1
    assert db.get_photo_count() == 2          # Solo buscar no borra


def test_delete_missing_borra_solo_los_faltantes(tmp_path, photos):
    real = tmp_path / "existe.jpg"; real.write_bytes(b"x")
    keep = photos(str(real))
    gone = photos(str(tmp_path / "borrada.jpg"))
    db.add_tag_to_photo(gone, db.create_tag("perdida"))

    report = services.find_missing_files()
    assert report.tagged == 1
    assert services.delete_missing(report) == 1
    assert [p.id for p in db.get_photos()] == [keep]


def test_unidad_desconectada_se_protege(tmp_path, photos, monkeypatch):
    real = tmp_path / "existe.jpg"; real.write_bytes(b"x")
    photos(str(real))
    for i in range(30):
        photos(rf"Q:\Fotos\{i}.jpg")         # Unidad que no existe

    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists",
                        lambda self: False if str(self).upper().startswith("Q:") else real_exists(self))

    report = services.find_missing_files()
    assert report.count == 0
    assert [(s.root.upper(), s.count) for s in report.skipped] == [("Q:\\", 30)]
    services.delete_missing(report)
    assert db.get_photo_count() == 31


def test_unidad_donde_faltan_todos_se_protege(tmp_path, photos):
    # La unidad existe (es la de tmp_path) pero TODOS sus archivos faltan:
    # probablemente otro disco con la misma letra → no borrar.
    for i in range(services.SUSPICIOUS_ROOT_MIN):
        photos(str(tmp_path / "desaparecida" / f"{i}.jpg"))

    report = services.find_missing_files()
    assert report.count == 0
    assert len(report.skipped) == 1
    assert "faltan todos" in report.skipped[0].reason


def test_pocos_faltantes_en_unidad_sin_otros_si_se_borran(tmp_path, photos):
    # Por debajo del umbral se considera normal (p. ej. 3 fotos borradas)
    for i in range(3):
        photos(str(tmp_path / f"{i}.jpg"))
    assert services.find_missing_files().count == 3


def test_busqueda_cancelada_no_borra(tmp_path, photos):
    photos(str(tmp_path / "borrada.jpg"))
    report = services.find_missing_files(should_stop=lambda: True)
    assert report.cancelled
    assert services.delete_missing(report) == 0
    assert db.get_photo_count() == 1


# ── Duplicados (#2, #12) ──────────────────────────────────────────────────────

def test_duplicados_agrupa_por_md5(tmp_path, photos):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / name).write_bytes(b"mismo contenido" if name != "c.jpg" else b"otro")
        photos(str(tmp_path / name))

    services.compute_missing_md5s()
    groups = services.get_duplicate_groups()
    assert len(groups) == 1
    assert {Path(p.path).name for p in groups[0].photos} == {"a.jpg", "b.jpg"}
    assert all(p.md5 for p in groups[0].photos)


def test_md5_cancelable(tmp_path, photos):
    for i in range(5):
        (tmp_path / f"{i}.jpg").write_bytes(bytes([i]))
        photos(str(tmp_path / f"{i}.jpg"))
    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    assert services.compute_missing_md5s(should_stop=stop_after_two) == 2
    assert sum(1 for p in db.get_all_photos_for_duplicates() if p.md5) == 2


def test_borrar_duplicado_va_a_la_papelera(tmp_path, photos, monkeypatch):
    f = tmp_path / "copia.jpg"; f.write_bytes(b"x")
    pid = photos(str(f))
    trashed = []
    monkeypatch.setattr(services, "send2trash", lambda p: trashed.append(p))

    assert services.delete_photo_file(pid) is True
    assert trashed == [str(f)]
    assert db.get_photo_by_id(pid) is None


def test_si_la_papelera_falla_no_se_borra_el_registro(tmp_path, photos, monkeypatch):
    f = tmp_path / "copia.jpg"; f.write_bytes(b"x")
    pid = photos(str(f))

    def falla(p):
        raise OSError("acceso denegado")

    monkeypatch.setattr(services, "send2trash", falla)
    assert services.delete_photo_file(pid) is False
    assert db.get_photo_by_id(pid) is not None
    assert f.exists()
