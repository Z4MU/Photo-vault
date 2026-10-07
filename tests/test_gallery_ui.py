"""Fase 5: galería virtualizada, paneles del sidebar, ventana principal y visor (sin ventanas: offscreen)."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtCore import QModelIndex, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QImageReader, QPixmap, QPixmapCache  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import database as db  # noqa: E402
import services  # noqa: E402
from ui import gallery, images, workers  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_pixmap_cache():
    QPixmapCache.clear()
    yield
    QPixmapCache.clear()


def wait_for(qapp, cond, timeout_s: float = 20.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_photos(tmp_path, n: int, video: bool = False) -> list[int]:
    """n fotos reales (JPEG) en tmp_path/fotos/<año>/, años 2010… en orden."""
    db.init_db()
    ids = []
    for i in range(n):
        folder = tmp_path / "fotos" / str(2010 + i % 5)
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"img{i:03d}.jpg"
        Image.new("RGB", (320, 240), (i * 7 % 255, 90, 160)).save(p)
        st = p.stat()
        ids.append(
            db.upsert_photo(str(p), p.name, 2010 + i % 5, 1 + i % 12, st.st_size, 320, 240, mtime=st.st_mtime)
        )
    return ids


@pytest.fixture
def queue(qapp):
    q = workers.thumbnail_queue()
    q.start()
    yield q
    q.stop()
    assert q.wait(5000)


# ─── Modelo ───────────────────────────────────────────────────────────────────


def test_modelo_carga_por_tramos_y_olvida_los_viejos(qapp, tmp_path, monkeypatch):
    ids = make_photos(tmp_path, 23)
    monkeypatch.setattr(gallery, "CHUNK_SIZE", 5)
    monkeypatch.setattr(gallery, "MAX_CHUNKS", 2)
    calls: list[int] = []
    real = services.get_gallery_chunk

    def spy(q, offset, limit):
        calls.append(offset)
        return real(q, offset, limit)

    monkeypatch.setattr(services, "get_gallery_chunk", spy)
    model = gallery.GalleryModel()
    model.set_query(
        services.GalleryQuery(sort_field=services.SortField.FILENAME, sort_order=services.SortOrder.ASC)
    )
    assert model.rowCount() == 23 and calls == []  # contar no carga fotos

    names = [model.photo_at(r).filename for r in range(23)]  # type: ignore[union-attr]
    assert names == sorted(names) and len(set(names)) == 23
    assert calls == [0, 5, 10, 15, 20]  # una consulta por tramo
    model.photo_at(22)
    assert calls[-1] == 20  # tramo en memoria: no vuelve a consultar
    assert model.row_of(model.photo_at(0).id) == 0  # type: ignore[union-attr]
    assert len(model._chunks) == 2
    assert model.photo_at(23) is None and model.photo_at(-1) is None

    # Rangos grandes van por ids sin cargar fotos
    assert sorted(model.ids_in_ranges([(0, 22)])) == sorted(ids)
    assert model.ids_in_ranges([(1, 2)]) == [model.photo_at(1).id, model.photo_at(2).id]  # type: ignore[union-attr]


def test_modelo_pide_miniaturas_y_avisa_cuando_estan(qapp, tmp_path, queue):
    make_photos(tmp_path, 3)
    (tmp_path / "roto.jpg").write_bytes(b"no es una imagen")
    db.upsert_photo(str(tmp_path / "roto.jpg"), "roto.jpg", 2030, 1, 16)
    model = gallery.GalleryModel(queue)
    model.set_query(services.GalleryQuery())
    changed: list[int] = []
    model.dataChanged.connect(lambda a, _b, _roles: changed.append(a.row()))

    rows = range(model.rowCount())
    assert all(model.data(model.index(r), Qt.ItemDataRole.DecorationRole) is None for r in rows)
    assert wait_for(qapp, lambda: len(changed) == 4)
    broken_row = next(r for r in rows if model.photo_at(r).filename == "roto.jpg")  # type: ignore[union-attr]
    for r in rows:
        pix = model.data(model.index(r), Qt.ItemDataRole.DecorationRole)
        if r == broken_row:
            assert pix is None and model.thumbnail_failed(model.photo_at(r))  # type: ignore[arg-type]
        else:
            assert isinstance(pix, QPixmap) and max(pix.width(), pix.height()) <= 200
    # Un fallo no se vuelve a pedir en cada repintado
    model.data(model.index(broken_row), Qt.ItemDataRole.DecorationRole)
    assert queue.pending_count() == 0


def test_modelo_ignora_miniaturas_de_una_consulta_anterior(qapp, tmp_path):
    make_photos(tmp_path, 2)
    model = gallery.GalleryModel()
    model.set_query(services.GalleryQuery())
    photo = model.photo_at(0)
    assert photo is not None
    key = gallery.thumb_key(photo, 200)
    model.set_query(services.GalleryQuery(search="img"))  # nadie pidió `key` en esta consulta
    model._on_thumb_loaded(key, QImage(10, 10, QImage.Format.Format_RGB32))
    assert QPixmapCache.find(key) is None


def test_modelo_mime_para_arrastrar(qapp, tmp_path):
    make_photos(tmp_path, 2)
    model = gallery.GalleryModel()
    model.set_query(services.GalleryQuery())
    md = model.mimeData([model.index(0), model.index(1)])
    paths = {u.toLocalFile().replace("/", "\\") for u in md.urls()}
    assert paths == {p.path for p in (model.photo_at(0), model.photo_at(1)) if p}
    assert model.flags(model.index(0)) & Qt.ItemFlag.ItemIsDragEnabled
    assert model.flags(QModelIndex()) == Qt.ItemFlag.NoItemFlags


def test_formatos():
    assert gallery.format_size(None) == "—"
    assert gallery.format_size(512) == "512 B"
    assert gallery.format_size(2048) == "2 KB"
    assert gallery.format_size(5 * 1024**2) == "5.0 MB"
    assert gallery.format_date(2021, 3) == "marzo 2021"
    assert gallery.format_date(2021, None) == "2021"
    assert gallery.format_date(None, None) == "Sin fecha"
    assert gallery.thumb_source_size(150) == 200 and gallery.thumb_source_size(300) == 480


# ─── Vista ────────────────────────────────────────────────────────────────────


def _view(model):
    view = gallery.GalleryView()
    delegate = gallery.GalleryDelegate(view)
    view.setModel(model)
    view.setItemDelegate(delegate)
    view.apply_cell_size(delegate.cell_size())
    view.resize(900, 600)
    view.show()
    return view


def test_vista_seleccion_por_rangos_y_pintado(qapp, tmp_path, queue, monkeypatch):
    ids = make_photos(tmp_path, 30)
    db.upsert_photo(str(tmp_path / "v.mp4"), "v.mp4", 2020, 1, 10, media_type="video", duration=75)
    model = gallery.GalleryModel(queue)
    model.set_query(services.GalleryQuery())
    view = _view(model)
    try:
        view.grab()  # pinta (incluido el video y las que aún cargan) sin errores
        view.selectAll()
        assert view.selected_count() == 31
        assert view.selected_ranges() == [(0, 30)]
        assert sorted(model.ids_in_ranges(view.selected_ranges()))[: len(ids)] == sorted(ids)
        view.select_rows(3, 5)
        assert view.selected_ranges() == [(3, 5)]
        view.select_row(10)
        assert view.selected_ranges() == [(10, 10)] and view.currentIndex().row() == 10
        assert view.first_visible_row() is not None

        refused: list[int] = []
        view.drag_refused.connect(refused.append)
        monkeypatch.setattr(gallery, "MAX_DRAG", 2)
        view.selectAll()
        view.startDrag(Qt.DropAction.CopyAction)
        assert refused == [31]

        opened: list[int] = []
        view.open_requested.connect(opened.append)
        view.select_row(4)
        from PyQt6.QtGui import QKeyEvent

        view.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        )
        assert opened == [4]
        view.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        )
        assert view.selected_count() == 0
        wait_for(qapp, lambda: queue.pending_count() == 0)
        view.grab()  # ya con miniaturas
    finally:
        view.close()


# ─── Paneles del sidebar ──────────────────────────────────────────────────────


def test_panel_de_carpetas(qapp, tmp_path):
    from ui.sidebar import FolderTreePanel

    make_photos(tmp_path, 10)
    panel = FolderTreePanel()
    got: list[object] = []
    panel.folder_selected.connect(got.append)
    panel.refresh()
    top = panel.tree.topLevelItem(0)
    assert top is not None and "(10)" in top.text(0)  # "Todas las carpetas"
    folder = str(tmp_path / "fotos" / "2012")
    panel.select_folder(folder)
    item = panel.tree.currentItem()
    assert item is not None and item.data(0, Qt.ItemDataRole.UserRole) == folder
    assert "(2)" in item.text(0)
    panel._on_item_clicked(item, 0)
    panel._on_item_clicked(top, 0)
    assert got == [folder, None]


def test_panel_de_fechas(qapp, tmp_path):
    from ui.sidebar import TimelinePanel

    make_photos(tmp_path, 10)
    db.upsert_photo(r"Z:\sin\fecha.jpg", "fecha.jpg", None, None, 1)
    db.upsert_photo(r"Z:\sin\mes.jpg", "mes.jpg", 2012, None, 1)
    panel = TimelinePanel()
    assert panel.is_dirty()
    panel.refresh(services.GalleryQuery())
    assert not panel.is_dirty()
    years = [panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())]  # type: ignore[union-attr]
    assert years[0].startswith("2014") and years[-1].startswith("Sin fecha")
    y2012 = next(
        panel.tree.topLevelItem(i)
        for i in range(panel.tree.topLevelItemCount())
        if years[i].startswith("2012")
    )
    assert y2012 is not None and "(3)" in y2012.text(0)
    months = [y2012.child(i).text(0) for i in range(y2012.childCount())]  # type: ignore[union-attr]
    assert months[-1].startswith("Sin mes")
    got: list[tuple] = []
    panel.date_selected.connect(lambda y, m: got.append((y, m)))
    panel._on_item_clicked(y2012, 0)
    panel._on_item_clicked(y2012.child(y2012.childCount() - 1), 0)  # type: ignore[arg-type]
    assert got == [(2012, None), (2012, services.NO_MONTH)]


def test_panel_de_etiquetas(qapp, tmp_path):
    from ui.sidebar import TagFilterPanel

    ids = make_photos(tmp_path, 2)
    services.add_tag(ids[0], "playa")
    panel = TagFilterPanel()
    panel.refresh()
    hits: list[int] = []
    panel.filter_changed.connect(lambda: hits.append(1))
    chk = next(c for c in panel.checkboxes() if c.text() == "playa")
    chk.setChecked(True)
    assert hits == [1] and panel.active_tag_ids() == [chk.property("tag_id")]
    panel.clear()
    assert panel.active_tag_ids() == [] and hits == [1]
    # Un tag activo que se borra deja de filtrar
    chk = next(c for c in panel.checkboxes() if c.text() == "playa")
    chk.setChecked(True)
    services.delete_tag(chk.property("tag_id"))
    panel.refresh()
    assert panel.active_tag_ids() == []


# ─── Ventana principal ────────────────────────────────────────────────────────


@pytest.fixture
def window(qapp, tmp_path):
    from ui.main_window import MainWindow

    make_photos(tmp_path, 40)
    win = MainWindow()
    win.resize(1300, 800)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()


def test_ventana_carga_y_filtra_por_carpeta(qapp, window, tmp_path):
    assert window.model.count() == 40
    assert "40 fotos" in window.count_lbl.text()
    folder = str(tmp_path / "fotos" / "2013")
    window.set_folder_filter(folder)
    assert window.model.count() == 8
    assert window.folder_chip.isVisible() and "2013" in window.folder_chip.text()
    window.set_folder_filter(None)
    assert window.model.count() == 40 and not window.folder_chip.isVisible()


def test_ventana_salta_a_una_fecha(qapp, window):
    window.sort_field_combo.setCurrentIndex(window.sort_field_combo.findData(services.SortField.FILENAME))
    window.go_to_date(2012, 3)
    # Cambió a orden por fecha y seleccionó la primera foto de marzo 2012
    assert window.sort_field_combo.currentData() == services.SortField.DATE
    photo = window.model.photo_at(window.view.currentIndex().row())
    assert photo is not None and (photo.year, photo.month) == (2012, 3)


def test_ventana_seleccion_menu_y_copiar(qapp, window):
    window.view.selectAll()
    assert len(window.selected_ids()) == 40
    assert window.btn_bulk_tag.isEnabled() and "40" in window.btn_bulk_tag.text()
    window.view.select_rows(0, 1)
    menu = window.build_context_menu(0)
    texts = [a.text() for a in menu.actions() if a.text()]
    assert any("Abrir en el visor" in t for t in texts)
    assert any("Mostrar en el Explorador" in t for t in texts)
    assert any("Etiquetar 2 fotos" in t for t in texts)
    window._copy_selection(as_files=False)
    clip = QApplication.clipboard()
    assert clip is not None and len(clip.text().splitlines()) == 2


def test_ventana_tamano_de_miniatura_persiste(qapp, window):
    window._step_thumb_size(+3)
    assert window.size_slider.value() == 260 and window.delegate.thumb == 260
    assert window.model._thumb_size == 480  # miniaturas grandes del caché
    window.close()
    assert services.get_thumb_display_size() == 260


def test_ventana_recarga_sin_perder_la_posicion(qapp, window):
    window.view.select_row(30)
    photo = window.model.photo_at(30)
    assert photo is not None
    services.add_tag(photo.id, "x")
    window.reload_keep_position()
    assert window.view.currentIndex().row() == 30


# ─── Visor ────────────────────────────────────────────────────────────────────


@pytest.fixture
def media(tmp_path):
    """Una foto grande, un GIF animado, un archivo roto y un video corto."""
    db.init_db()
    out = {}
    big = tmp_path / "grande.jpg"
    Image.new("RGB", (1600, 1200), (200, 30, 30)).save(big)
    gif = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (60, 40), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=50, loop=0)
    broken = tmp_path / "roto.jpg"
    broken.write_bytes(b"x")
    video = tmp_path / "clip.mp4"
    import cv2
    import numpy as np

    vw = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))  # type: ignore[attr-defined]
    for i in range(10):
        vw.write(np.full((48, 64, 3), i * 20, dtype=np.uint8))
    vw.release()
    for name, p, kind in (
        ("big", big, "image"),
        ("gif", gif, "image"),
        ("broken", broken, "image"),
        ("video", video, "video"),
    ):
        pid = db.upsert_photo(
            str(p), p.name, 2020, 1, p.stat().st_size, 1600, 1200, media_type=kind, mtime=p.stat().st_mtime
        )
        out[name] = db.get_photo_by_id(pid)
    return out


def test_visor_navega_zoom_rotar_e_info(qapp, media):
    from ui.viewer import ListSequence, ViewerWindow

    seq = ListSequence([media["big"], media["gif"], media["broken"], media["video"]])
    v = ViewerWindow(seq, 0, fullscreen=False)
    v.resize(1000, 700)
    v.show()
    try:
        iv = v.image_view
        assert wait_for(qapp, lambda: iv.has_image() and not iv.is_preview() and v.status_lbl.text() == "")
        assert iv.is_fit() and iv.zoom() < 1.0  # 1600×1200 en una ventana más chica
        iv.zoom_by(2)
        assert not iv.is_fit()
        v.rotate(90)
        assert iv.rotation() == 90
        iv.fit()
        assert iv.is_fit()

        v.toggle_info()
        values = v.info.form_values()
        assert values["Nombre"] == "grande.jpg" and "1600 × 1200" in values["Resolución"]

        v.next()  # GIF animado
        assert v.stack.currentWidget() is v.movie_label and v._movie is not None
        v.next()  # archivo roto
        assert wait_for(qapp, lambda: v.stack.currentWidget() is v.message)
        assert "No se pudo abrir" in v.message.text()
        v.next()  # video
        assert v.stack.currentWidget() is v.video and v.counter_lbl.text() == "4 / 4"
        v.next()  # ya es la última
        assert v.current_row == 3
        v.show_row(0)
        assert v.current_row == 0 and v.stack.currentWidget() is iv
        assert iv.rotation() == 0  # la rotación es por foto
    finally:
        v.close()
    qapp.processEvents()
    assert not v._queue.isRunning() or v._queue in workers._retired_threads


def test_visor_etiquetas_desde_el_panel(qapp, media):
    from ui.viewer import ListSequence, ViewerWindow

    v = ViewerWindow(ListSequence([media["big"]]), 0, fullscreen=False)
    try:
        v.toggle_info()
        editor = v.info.tag_editor
        editor.tag_combo.setEditText("Vacaciones")
        editor._add_tag()
        assert [t.name for t in services.get_photo_tags(media["big"].id)] == ["vacaciones"]
        assert v.tags_were_changed
        tid = services.get_photo_tags(media["big"].id)[0].id
        editor._remove_tag(tid)
        assert services.get_photo_tags(media["big"].id) == []
    finally:
        v.close()


def test_visor_muestra_la_resolucion_si_la_db_no_la_tiene(qapp, tmp_path):
    from ui.viewer import ListSequence, ViewerWindow

    db.init_db()
    p = tmp_path / "sin_tamano.jpg"
    Image.new("RGB", (300, 200)).save(p)
    pid = db.upsert_photo(str(p), p.name, 2020, 1, p.stat().st_size, mtime=p.stat().st_mtime)
    photo = db.get_photo_by_id(pid)
    assert photo is not None and photo.width is None
    v = ViewerWindow(ListSequence([photo]), 0, fullscreen=False)
    try:
        assert wait_for(qapp, lambda: "Resolución" in v.info.form_values())
        assert v.info.form_values()["Resolución"] == "300 × 200"
    finally:
        v.close()


def test_visor_sin_fotos(qapp):
    from ui.viewer import ListSequence, ViewerWindow

    v = ViewerWindow(ListSequence([]), 0, fullscreen=False)
    assert v.stack.currentWidget() is v.message
    v.close()


def test_imagen_completa_con_pillow_si_qt_no_puede(qapp, tmp_path, monkeypatch):
    src = tmp_path / "celular.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # rotada 90°
    Image.new("RGB", (800, 400)).save(src, exif=exif)
    monkeypatch.setattr(QImageReader, "read", lambda self: QImage())  # como con HEIC
    img = images.load_full_image(str(src), 500)
    assert img is not None and (img.width(), img.height()) == (250, 500)
    assert images.load_full_image(str(tmp_path / "no-existe.jpg"), 500) is None


def test_gif_animado(tmp_path):
    gif = tmp_path / "a.gif"
    frames = [Image.new("RGB", (10, 10), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=50)
    still = tmp_path / "b.gif"
    Image.new("RGB", (10, 10)).save(still)
    assert images.is_animated(str(gif)) and not images.is_animated(str(still))
