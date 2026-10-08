"""Fase 10: sugerir etiquetas por carpeta y casi-duplicados (hash perceptual)."""

import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import database as db  # noqa: E402
import privacy  # noqa: E402
import services  # noqa: E402
import similarity  # noqa: E402
import smart  # noqa: E402
from tests.conftest import make_legacy_db  # noqa: E402
from tests.test_gallery_ui import wait_for  # noqa: E402


def pattern(seed: int, size=(640, 480)) -> Image.Image:
    """Imagen con estructura (manchas suaves): el dHash de dos semillas distintas es muy distinto."""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)
    return Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)


def add_file(path, img: Image.Image, quality: int = 90) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=quality)
    st = path.stat()
    return db.upsert_photo(
        str(path), path.name, 2020, 1, st.st_size, img.width, img.height, mtime=st.st_mtime
    )


# ── Hash perceptual ───────────────────────────────────────────────────────────


def test_migracion_v6(db_path):
    make_legacy_db(db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    finally:
        conn.close()
    assert "phash" in cols
    assert db.get_schema_version() == db.SCHEMA_VERSION >= 6


def test_phash_parecidas_cerca_y_distintas_lejos():
    a = similarity.phash_image(pattern(1))
    a_small = similarity.phash_image(pattern(1).resize((200, 150)))
    b = similarity.phash_image(pattern(2))
    assert similarity.distance(a, a_small) <= 3
    assert similarity.distance(a, b) > 12
    assert -(1 << 63) <= a < 1 << 63  # entra en un INTEGER de SQLite
    # Una imagen lisa no tiene información: no se compara con nada
    assert similarity.phash_image(Image.new("RGB", (300, 200), (0, 0, 0))) == similarity.FLAT
    assert similarity.find_similar([(1, similarity.FLAT), (2, similarity.FLAT)], 6) == []


def test_agrupar_por_distancia_y_cadenas():
    base = 0b1011 << 40
    items = [
        (1, similarity.to_signed(base)),
        (2, similarity.to_signed(base ^ 0b11)),  # 2 bits de 1
        (3, similarity.to_signed(base ^ 0b11 ^ (0b111 << 10))),  # 3 bits de 2, 5 de 1
        (4, similarity.to_signed(~base & ((1 << 64) - 1))),  # todo distinto
        (5, similarity.to_signed((1 << 63) | 12345)),  # con signo negativo
        (6, similarity.to_signed(((1 << 63) | 12345) ^ 1)),
    ]
    groups = sorted(sorted(g) for g in similarity.find_similar(items, 3))
    # 1~2 (2 bits) y 2~3 (3 bits) pero 1 y 3 difieren en 5: no se encadenan
    assert groups == [[1, 2], [5, 6]]
    assert sorted(sorted(g) for g in similarity.find_similar(items, 5)) == [[1, 2, 3], [5, 6]]
    assert sorted(sorted(g) for g in similarity.find_similar(items, 2)) == [[1, 2], [5, 6]]
    assert similarity.find_similar(items[:1], 6) == []


def test_reindexar_un_archivo_cambiado_borra_su_hash(tmp_path):
    db.init_db()
    p = tmp_path / "a.jpg"
    pid = add_file(p, pattern(1))
    db.set_phashes([(pid, 42)])
    st = p.stat()
    db.upsert_photo(str(p), p.name, 2020, 1, st.st_size, 640, 480, mtime=st.st_mtime)  # sin cambios
    assert db.get_phashes() == [(pid, 42)]
    db.upsert_photo(str(p), p.name, 2020, 1, st.st_size + 1, 640, 480, mtime=st.st_mtime + 5)
    assert db.get_phashes() == []


@pytest.fixture
def lookalikes(tmp_path):
    """a (640×480), a2 = a a 320×240 recomprimida, a3 = copia exacta de a, b y c distintas."""
    db.init_db()
    a = add_file(tmp_path / "f" / "a.jpg", pattern(1))
    a2 = add_file(tmp_path / "f" / "a2.jpg", pattern(1).resize((320, 240)), quality=60)
    b = add_file(tmp_path / "f" / "b.jpg", pattern(2))
    c = add_file(tmp_path / "g" / "c.jpg", pattern(3))
    (tmp_path / "g" / "a3.jpg").write_bytes((tmp_path / "f" / "a.jpg").read_bytes())
    st = (tmp_path / "g" / "a3.jpg").stat()
    a3 = db.upsert_photo(
        str(tmp_path / "g" / "a3.jpg"), "a3.jpg", 2020, 1, st.st_size, 640, 480, mtime=st.st_mtime
    )
    return a, a2, a3, b, c


def test_buscar_parecidas(lookalikes):
    a, a2, a3, b, c = lookalikes
    groups = smart.find_similar_photos("normal")
    assert len(groups) == 1 and groups[0].similar
    assert {p.id for p in groups[0].photos} == {a, a2, a3}
    best = groups[0].best
    assert best is not None and best.id in (a, a3)  # 640×480, no la reducida
    assert len(db.get_phashes()) == 5
    # Si las iguales tienen el mismo md5 y ya no hay otra parecida, no se repiten aquí
    for pid in (a, a3):
        db.update_photo_md5(pid, "mismo")
    db.delete_photos([a2], "test")
    assert smart.get_similar_groups("normal") == []


def test_parecidas_con_lo_oculto(lookalikes):
    a, a2, _a3, _b, _c = lookalikes
    secret = services.create_tag("privado")
    services.add_tag_by_id(a2, secret)
    services.set_tag_hidden(secret, True)
    privacy.set_pin("1234")
    privacy.lock()
    smart.compute_missing_phashes()
    assert len(db.get_phashes()) == 4  # la oculta no (bloqueado no hay miniatura)
    groups = smart.get_similar_groups("normal")
    assert all(a2 not in {p.id for p in g.photos} for g in groups)
    privacy.unlock("1234")
    smart.compute_missing_phashes()
    assert any(a2 in {p.id for p in g.photos} for g in smart.get_similar_groups("normal"))


def test_cancelar_calculo_de_hashes(lookalikes):
    assert smart.compute_missing_phashes(should_stop=lambda: True) == 0
    assert db.get_phashes() == []


# ── Sugerencias por carpeta ───────────────────────────────────────────────────


@pytest.fixture
def folders(db_path):
    """Viajes\\Playa 2019 (5 fotos, 3 con «mar»), Viajes\\Cancún (2), Otros (3)."""
    db.init_db()

    def photos(folder: str, n: int) -> list[int]:
        return [
            db.upsert_photo(rf"G:\Fotos\{folder}\{i}.jpg", f"{i}.jpg", 2019, 1, 100 + i) for i in range(n)
        ]

    playa = photos(r"Viajes\Playa 2019", 5)
    cancun = photos(r"Viajes\Cancún", 2)
    otros = photos("Otros", 3)
    mar = services.create_tag("mar")
    for pid in playa[:3]:
        services.add_tag_by_id(pid, mar)
    return playa, cancun, otros, mar


def names(suggestions) -> list[str]:
    return [s.tag.name for s in suggestions]


def test_sugiere_lo_que_tiene_la_carpeta(folders):
    playa, _cancun, otros, _mar = folders
    s = smart.suggest_tags([playa[4]])
    assert names(s) == ["mar"] and s[0].score == 0.6 and "3 de 5" in s[0].reason
    assert smart.suggest_tags([playa[0]]) == []  # ya la tiene
    assert smart.suggest_tags([otros[0]]) == []  # en otra carpeta no
    assert smart.suggest_tags([]) == []


def test_sugiere_por_el_nombre_de_la_carpeta(folders):
    playa, cancun, _otros, _mar = folders
    playa_tag = services.create_tag("playa")
    cancun_tag = services.create_tag("cancun")  # sin acento: igual coincide con «Cancún»
    services.create_tag("viaje")  # «Viajes» no es la misma palabra
    s = smart.suggest_tags([cancun[0]])
    assert names(s) == ["cancun"]
    assert "nombre de la carpeta" in s[0].reason
    assert names(smart.suggest_tags([playa[4]]))[0] == "playa"  # 0,9 > 0,6 de «mar»
    assert {playa_tag, cancun_tag} <= {t.id for t in services.get_all_tags()}


def test_sugiere_por_la_carpeta_de_arriba_con_menos_peso(folders):
    _playa, cancun, _otros, mar = folders
    # Viajes\ tiene fotos propias, 2 de 2 con «mar»
    for i in range(2):
        pid = db.upsert_photo(rf"G:\Fotos\Viajes\v{i}.jpg", f"v{i}.jpg", 2019, 1, 1)
        services.add_tag_by_id(pid, mar)
    s = smart.suggest_tags([cancun[0]])
    assert names(s) == ["mar"] and s[0].score == smart.PARENT_WEIGHT


def test_no_sugiere_etiquetas_ocultas_bloqueadas(folders):
    playa, _cancun, _otros, mar = folders
    services.set_tag_hidden(mar, True)
    assert names(smart.suggest_tags([playa[4]])) == ["mar"]  # sin PIN se ven (como en el gestor)
    privacy.set_pin("1234")
    privacy.lock()
    assert smart.suggest_tags([playa[4]]) == []


def test_sugerencias_para_una_seleccion(folders):
    playa, _cancun, otros, _mar = folders
    assert names(smart.suggest_tags([playa[3], playa[4], otros[0]])) == ["mar"]
    assert smart.suggest_tags(playa[:3]) == []  # todas la tienen


# ── UI ────────────────────────────────────────────────────────────────────────


def test_editor_de_etiquetas_muestra_sugeridas(qapp, folders):
    from ui.dialogs.photo import BulkTagDialog, TagEditor

    playa, _cancun, _otros, mar = folders
    ed = TagEditor()
    changed = []
    ed.tags_changed.connect(lambda: changed.append(1))
    ed.set_photo(playa[4])
    assert ed.suggestions() == ["mar"]
    from PyQt6.QtWidgets import QPushButton

    (btn,) = [b for b in ed.sugg_container.findChildren(QPushButton) if not b.isHidden()]
    btn.click()
    assert mar in {t.id for t in services.get_photo_tags(playa[4])} and changed
    assert ed.suggestions() == []

    dlg = BulkTagDialog([playa[3], playa[4]])
    assert dlg.suggested == ["mar"]


def test_dialogo_de_parecidas(qapp, lookalikes):
    from ui.dialogs.duplicates import DuplicatesDialog

    a, a2, a3, _b, _c = lookalikes
    dlg = DuplicatesDialog()
    assert dlg.sens_combo.isHidden()
    dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("similar"))
    assert not dlg.sens_combo.isHidden()
    dlg._start_scan()
    assert wait_for(qapp, lambda: dlg._worker is None, 30)
    assert len(dlg._groups) == 1 and {p.id for p in dlg._groups[0].photos} == {a, a2, a3}
    assert "1 grupos" in dlg.status_lbl.text()
    dlg.reject()
