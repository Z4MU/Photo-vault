"""
Fase 10: búsqueda por contenido (CLIP). Con un modelo falso (colores →
vectores) para no depender de la descarga de 217 MB; el modelo real se prueba
solo si PV_CLIP_MODELS apunta a una carpeta con los archivos.
"""

import hashlib
import io
import json
import os
import struct
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import clip_model  # noqa: E402
import database as db  # noqa: E402
import embedding_store  # noqa: E402
import privacy  # noqa: E402
import services  # noqa: E402
import smart  # noqa: E402
from services import GalleryQuery  # noqa: E402
from tests.test_gallery_ui import wait_for  # noqa: E402

COLORS = {"rojo": (230, 20, 20), "verde": (20, 230, 20), "azul": (20, 20, 230)}


def _color_vec(rgb) -> np.ndarray:
    v = np.zeros(clip_model.DIM, dtype=np.float32)
    v[:3] = np.asarray(rgb, dtype=np.float32) - 125
    v[3] = 20  # algo en común entre todas, como en CLIP
    return v / np.linalg.norm(v)


class FakeClip:
    """Una imagen → su color medio; un texto con un color → ese color (los prompts adultos = rojo)."""

    def __init__(self):
        self.images = 0

    def encode_images(self, images):
        self.images += len(images)
        return np.stack(
            [_color_vec(np.asarray(im.convert("RGB")).reshape(-1, 3).mean(axis=0)) for im in images]
        )

    def encode_texts(self, texts):
        out = []
        for t in texts:
            if t in smart.ADULT_PROMPTS:
                t = "rojo"
            elif t in smart.SAFE_PROMPTS:
                t = "azul"
            color = next((c for name, c in COLORS.items() if name in t), (125, 125, 125))
            out.append(_color_vec(color))
        return np.stack(out)


@pytest.fixture
def fake(monkeypatch):
    model = FakeClip()
    monkeypatch.setattr(clip_model, "is_installed", lambda: True)
    monkeypatch.setattr(clip_model, "get_model", lambda: model)
    monkeypatch.setattr(smart, "TEXT_MIN_Z", 0.5)  # con pocas fotos la desviación es grande
    return model


def add_photo(tmp_path: Path, name: str, color, folder="f") -> int:
    p = tmp_path / "fotos" / folder / f"{name}.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color).save(p)
    st = p.stat()
    return db.upsert_photo(str(p), p.name, 2020, 1, st.st_size, 64, 48, mtime=st.st_mtime)


@pytest.fixture
def colors(tmp_path, fake):
    """4 rojas, 3 verdes, 3 azules."""
    db.init_db()
    ids = {
        "rojo": [add_photo(tmp_path, f"r{i}", COLORS["rojo"]) for i in range(4)],
        "verde": [add_photo(tmp_path, f"v{i}", COLORS["verde"]) for i in range(3)],
        "azul": [add_photo(tmp_path, f"a{i}", COLORS["azul"], folder="g") for i in range(3)],
    }
    return ids


# ── Análisis ──────────────────────────────────────────────────────────────────


def test_analizar_es_incremental(colors, fake, tmp_path):
    assert smart.analysis_status() == (0, 10)
    assert smart.analyze_photos() == 10
    assert smart.analysis_status() == (10, 10)
    assert smart.analyze_photos() == 0 and fake.images == 10  # nada nuevo
    # Un archivo que cambió se vuelve a analizar
    pid = colors["rojo"][0]
    photo = services.get_photo(pid)
    assert photo is not None
    st = Path(photo.path).stat()
    db.upsert_photo(photo.path, photo.filename, 2020, 1, st.st_size, 64, 48, mtime=st.st_mtime + 10)
    assert smart.analyze_photos() == 1
    # Uno des-indexado se quita del almacén
    db.delete_photos([colors["verde"][0]], "test")
    smart.analyze_photos()
    assert embedding_store.count(clip_model.MODEL_ID) == 9


def test_un_archivo_ilegible_no_se_reintenta(colors, fake, tmp_path):
    bad = tmp_path / "fotos" / "roto.jpg"
    bad.write_bytes(b"no es una imagen")
    db.upsert_photo(str(bad), "roto.jpg", 2020, 1, 16, 1, 1, mtime=bad.stat().st_mtime)
    assert smart.analyze_photos() == 10
    assert embedding_store.count(clip_model.MODEL_ID) == 11  # con un vector nulo
    assert smart.analyze_photos() == 0


def test_cancelar_analisis(colors, fake):
    assert smart.analyze_photos(should_stop=lambda: True) == 0
    assert embedding_store.count(clip_model.MODEL_ID) == 0


def test_lo_oculto_solo_se_analiza_desbloqueado(colors, fake):
    secret = services.create_tag("privado")
    services.add_tag_by_id(colors["azul"][0], secret)
    services.set_tag_hidden(secret, True)
    privacy.set_pin("1234")
    privacy.lock()
    assert smart.analyze_photos() == 9
    privacy.unlock("1234")
    assert smart.analyze_photos() == 1


# ── Búsqueda ──────────────────────────────────────────────────────────────────


def test_buscar_por_texto_en_la_galeria(colors, fake):
    smart.analyze_photos()
    q = GalleryQuery(semantic="algo rojo")
    assert set(services.get_gallery_ids(q)) == set(colors["rojo"])
    assert services.count_gallery(q) == 4
    chunk = services.get_gallery_chunk(q, 1, 2)
    assert [p.id for p in chunk] == services.get_gallery_ids(q)[1:3]
    # Se combina con los demás filtros (la carpeta de las azules: nada rojo)
    blue = services.get_photo(colors["azul"][0])
    assert blue is not None
    assert services.get_gallery_ids(replace(q, folder=str(Path(blue.path).parent))) == []
    assert services.gallery_row_of_date(q, 2020) == 0
    # Se guarda como búsqueda
    assert GalleryQuery.from_dict(q.to_dict()) == q


def test_parecidas_a_una_foto(colors, fake):
    smart.analyze_photos()
    ranked = smart.semantic_ranking(f"@foto:{colors['verde'][0]}")
    assert set(ranked) == set(colors["verde"])
    assert smart.semantic_ranking("@foto:999999") == []


def test_ordenar_por_parecido_con_una_etiqueta_y_sugerirla(colors, fake):
    smart.analyze_photos()
    tag = services.create_tag("tomates")
    for pid in colors["rojo"][:3]:
        services.add_tag_by_id(pid, tag)
    # Con menos de TAG_MIN_EXAMPLES fotos se usa el nombre (sin color: no sugiere nada)
    assert smart.content_tag_suggestions(colors["rojo"][3]) == []
    services.add_tag_by_id(colors["rojo"][3], tag)
    extra = add_photo(Path(services.get_photo(colors["rojo"][0]).path).parents[2], "r9", COLORS["rojo"])  # type: ignore[union-attr]
    services.add_tag_by_id(extra, tag)
    nueva = add_photo(Path(services.get_photo(colors["rojo"][0]).path).parents[2], "r10", COLORS["rojo"])  # type: ignore[union-attr]
    smart.analyze_photos()
    s = smart.content_tag_suggestions(nueva)
    assert [x.tag.id for x in s] == [tag] and "Se parece" in s[0].reason
    assert smart.content_tag_suggestions(colors["azul"][0]) == []
    # Revisión: las pendientes, de la más parecida a la etiqueta a la menos
    review = services.review_query(GalleryQuery(semantic=f"@etiqueta:{tag}"), tag)
    ids = services.get_gallery_ids(review)
    assert ids[0] == nueva and len(ids) == 7


def test_contenido_adulto_primero(colors, fake):
    smart.analyze_photos()
    ids = smart.semantic_ranking("@adulto")
    assert set(ids[:4]) == set(colors["rojo"]) and len(ids) == 10


def test_sin_modelo_no_hay_busqueda(db_path):
    db.init_db()
    assert not smart.content_model_installed()
    assert smart.semantic_ranking("perro") == []
    assert services.get_gallery_ids(GalleryQuery(semantic="perro")) == []


def test_borrar_el_analisis(colors, fake):
    smart.analyze_photos()
    assert embedding_store.size_mb() >= 0
    smart.forget_content_analysis()
    assert embedding_store.count(clip_model.MODEL_ID) == 0
    assert smart.semantic_ranking("rojo") == []


# ── Modelo: descarga y preprocesado ───────────────────────────────────────────


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_descarga_verifica_el_hash(db_path, monkeypatch):
    payload = b"x" * 3_000_000
    good = clip_model.ModelFile(
        "a.bin", "https://example.test/a", hashlib.sha256(payload).hexdigest(), len(payload)
    )
    monkeypatch.setattr(clip_model, "FILES", (good,))
    monkeypatch.setattr(clip_model, "DOWNLOAD_SIZE", len(payload))
    monkeypatch.setattr(clip_model.urllib.request, "urlopen", lambda req, timeout: FakeResponse(payload))
    seen = []
    assert clip_model.download(lambda c, t: seen.append((c, t)))
    assert clip_model.is_installed() and seen[-1] == (len(payload), len(payload))
    # Cancelar: no queda nada a medias
    clip_model.remove()
    assert clip_model.download(should_stop=lambda: True) is False
    assert not list(clip_model.model_dir().glob("*"))
    # Hash distinto: error y sin archivo
    bad = replace(good, sha256="0" * 64)
    monkeypatch.setattr(clip_model, "FILES", (bad,))
    with pytest.raises(ValueError):
        clip_model.download()
    assert not clip_model.is_installed()


def test_preprocesado_y_capa_densa(tmp_path):
    arr = clip_model.preprocess(Image.new("RGB", (640, 300), (255, 255, 255)))
    assert arr.shape == (3, 224, 224) and arr.dtype == np.float32
    assert np.allclose(arr[0], (1 - 0.48145466) / 0.26862954, atol=1e-4)
    w = np.arange(512 * 768, dtype=np.float32).reshape(512, 768)
    header = json.dumps(
        {"linear.weight": {"dtype": "F32", "shape": [512, 768], "data_offsets": [0, w.nbytes]}}
    ).encode()
    p = tmp_path / "d.safetensors"
    p.write_bytes(struct.pack("<Q", len(header)) + header + w.tobytes())
    assert np.array_equal(clip_model._load_dense(p), w)


@pytest.mark.skipif(not os.environ.get("PV_CLIP_MODELS"), reason="modelo real no disponible (PV_CLIP_MODELS)")
def test_modelo_real(monkeypatch):
    monkeypatch.setattr(clip_model, "MODELS_DIR", Path(os.environ["PV_CLIP_MODELS"]))
    model = clip_model.ClipModel()
    t = model.encode_texts(["perro", "dog", "coche"])
    assert t.shape == (3, 512) and np.allclose(np.linalg.norm(t, axis=1), 1, atol=1e-3)
    assert t[0] @ t[1] > t[0] @ t[2]  # "perro" ≈ "dog" (multilingüe)
    i = model.encode_images([Image.new("RGB", (300, 200), (200, 30, 30))])
    assert i.shape == (1, 512)


# ── UI ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def window(qapp, colors, fake):
    smart.analyze_photos()
    from ui.main_window import MainWindow

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    assert wait_for(qapp, lambda: w.model.count() == 10, 10)
    yield w
    w.close()
    qapp.processEvents()


def test_buscador_por_contenido(qapp, window, colors):
    w = window
    w.btn_semantic.setChecked(True)
    assert "Describe" in w.search_edit.placeholderText()
    w.search_edit.setText("azul")
    w._apply_query()
    shown = [w.model.photo_at(i) for i in range(w.model.count())]
    assert {p.id for p in shown if p is not None} == set(colors["azul"])
    assert "por parecido" in w.count_lbl.text() and not w.sort_field_combo.isEnabled()
    assert w._query().search is None
    # Parecidas a una foto (menú contextual) → chip
    photo = services.get_photo(colors["verde"][1])
    assert photo is not None
    w.show_similar_content(photo)
    assert w.semantic_chip.isVisible() and w.model.count() == 3
    w.set_semantic_special(None)
    w.btn_semantic.setChecked(False)
    w._clear_filters()
    assert w.model.count() == 10 and w.sort_field_combo.isEnabled()


def test_busqueda_por_contenido_guardada(qapp, window, colors):
    w = window
    w.apply_gallery_query(GalleryQuery(semantic="rojo"))
    assert w.btn_semantic.isChecked() and w.search_edit.text() == "rojo" and w.model.count() == 4
    w.apply_gallery_query(GalleryQuery(semantic=f"@foto:{colors['azul'][0]}"))
    assert w.semantic_chip.isVisible() and w.model.count() == 3


def test_sin_modelo_el_boton_ofrece_configuracion(qapp, window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    w = window
    monkeypatch.setattr(clip_model, "is_installed", lambda: False)
    asked: list[tuple] = []

    def question(*args, **_kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    w.btn_semantic.setChecked(True)
    assert asked and not w.btn_semantic.isChecked()


def test_configuracion_ia_y_analisis_en_segundo_plano(qapp, window, colors, tmp_path):
    from ui.dialogs.settings import SettingsDialog

    w = window
    dlg = SettingsDialog(w)
    panel = dlg.ai_panel
    assert "10 de 10" in panel.analysis_lbl.text() and not panel.btn_analyze.isEnabled()
    panel.chk_auto.setChecked(False)
    dlg._apply()
    assert not smart.is_auto_analyze()
    add_photo(tmp_path, "nueva", COLORS["verde"])
    assert w.start_content_analysis()
    assert wait_for(qapp, lambda: w._bg_kind is None, 20)
    assert smart.analysis_status() == (11, 11)


def test_etiquetado_rapido_ordenado_por_parecido(qapp, window, colors):
    from ui.dialogs.quick_tag_setup import QuickTagSetupDialog

    tag = services.create_tag("cielo")
    dlg = QuickTagSetupDialog(GalleryQuery())
    dlg.tag_combo.setCurrentIndex(dlg.tag_combo.findData(tag))
    assert dlg.order_combo.isVisibleTo(dlg)
    dlg.order_combo.setCurrentIndex(dlg.order_combo.findData("adult"))
    assert dlg.final_query().semantic == "@adulto"
    assert set(services.get_gallery_ids(dlg.final_query())[:4]) == set(colors["rojo"])
    dlg.reject()


def test_editor_sugiere_por_contenido(qapp, colors, fake, tmp_path):
    from ui.dialogs.photo import TagEditor

    tag = services.create_tag("hojas")
    for i in range(5):
        services.add_tag_by_id(add_photo(tmp_path, f"x{i}", COLORS["verde"], folder="otra"), tag)
    smart.analyze_photos()
    ed = TagEditor()
    ed.set_photo(colors["verde"][0])
    assert "hojas" in ed.suggestions()
