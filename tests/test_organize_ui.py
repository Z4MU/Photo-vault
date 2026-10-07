"""Fase 6 en la UI: barra de filtros, álbumes, estrellas/favoritas, notas, gestor de etiquetas."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

import database as db  # noqa: E402
import services  # noqa: E402
from services import GalleryQuery  # noqa: E402
from tests.test_gallery_ui import make_photos, wait_for  # noqa: E402


def test_barra_de_filtros(qapp):
    from ui.filter_bar import FilterBar

    bar = FilterBar()
    hits: list[int] = []
    bar.changed.connect(lambda: hits.append(1))
    assert GalleryQuery(**bar.values()) == GalleryQuery()
    bar.dur_combo.setCurrentIndex(1)  # < 30 s
    assert bar.values()["media_type"] == "video" and bar.values()["max_duration"] == 30.0
    q = GalleryQuery(
        media_type="image", year_from=2015, year_to=2020, orientation="portrait", min_megapixels=12.0,
        min_rating=3, favorites_only=True, untagged_only=True, has_note=True,
    )  # fmt: skip
    n = len(hits)
    bar.set_values(q)
    assert len(hits) == n  # mostrar una búsqueda guardada no dispara recargas
    assert GalleryQuery(**bar.values()) == q
    bar.clear_and_emit()
    assert GalleryQuery(**bar.values()) == GalleryQuery() and len(hits) == n + 1


@pytest.fixture
def window(qapp, tmp_path):
    from ui.main_window import MainWindow

    make_photos(tmp_path, 20)
    win = MainWindow()
    win.resize(1300, 800)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()


def test_ventana_estrellas_favoritas_y_filtros(qapp, window):
    window.view.select_rows(0, 2)
    window.set_rating_selection(4)
    window.view.select_rows(1, 1)
    window.toggle_favorite_selection()
    assert window.model.photo_at(0).rating == 4
    assert window.model.photo_at(1).favorite

    window.btn_filters.setChecked(True)
    assert not window.filter_bar.isHidden()
    window.filter_bar.rating_combo.setCurrentIndex(4)  # ★★★★ o más
    assert window.model.count() == 3 and "(1)" in window.btn_filters.text()
    window.filter_bar.chk_fav.setChecked(True)
    assert window.model.count() == 1
    window._clear_filters()
    assert window.model.count() == 20 and window.btn_filters.text() == "⚙ Filtros"

    menu = window.build_context_menu(0)
    texts = [a.text() for a in menu.actions()]
    assert any("Valoración" in t for t in texts) and any("favorita" in t for t in texts)


def test_ventana_guardar_y_abrir_busqueda(qapp, window, monkeypatch):
    window.filter_bar.year_from.setValue(2012)
    window.filter_bar.year_to.setValue(2013)
    window.sort_order_combo.setCurrentIndex(1)  # asc
    expected = window.model.count()
    assert expected == 8
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Años 12-13", True))
    window.save_current_search()
    window._clear_filters()
    window.sort_order_combo.setCurrentIndex(0)
    assert window.model.count() == 20

    window.tabs.setCurrentWidget(window.saved_panel)
    tree = window.saved_panel.tree
    mine = tree.topLevelItem(1)
    assert mine is not None and mine.child(0).text(0).startswith("Años 12-13  (8)")
    window.saved_panel._on_item_clicked(mine.child(0), 0)
    assert window.model.count() == 8
    assert window.filter_bar.year_from.value() == 2012 and window.btn_filters.isChecked()
    assert window.sort_order_combo.currentData() == services.SortOrder.ASC

    albums = tree.topLevelItem(0)
    assert albums is not None and albums.childCount() == len(services.BUILTIN_ALBUMS)


def test_ventana_atajos_de_estrellas(qapp, window):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    window.view.setFocus()
    window.view.select_row(5)
    QTest.keyClick(window.view, Qt.Key.Key_3)  # type: ignore[call-overload]
    QTest.keyClick(window.view, Qt.Key.Key_F)  # type: ignore[call-overload]
    p = window.model.photo_at(5)
    assert p is not None and p.rating == 3 and p.favorite
    QTest.keyClick(window.view, Qt.Key.Key_0)  # type: ignore[call-overload]
    assert window.model.photo_at(5).rating == 0


def test_visor_estrellas_favorita_y_nota(qapp, tmp_path):
    from ui.viewer import ListSequence, ViewerWindow

    ids = make_photos(tmp_path, 2)
    photos = db.get_photos_by_ids(ids)
    v = ViewerWindow(ListSequence(photos), 0, fullscreen=False)
    v.show()
    try:
        v.set_rating(5)
        v.toggle_favorite()
        assert v.marks_lbl.text() == "★★★★★" and v.btn_fav.text() == "♥" and v.tags_were_changed
        assert db.get_photo_by_id(photos[0].id).favorite  # type: ignore[union-attr]
        v.toggle_info()
        v.info.note_edit.setPlainText("una nota")
        v.next()  # cambiar de foto guarda la nota de la anterior
        assert db.get_photo_by_id(photos[0].id).note == "una nota"  # type: ignore[union-attr]
        assert v.info.note_edit.toPlainText() == ""
        v.info.note_edit.setPlainText("otra")
    finally:
        v.close()
    assert db.get_photo_by_id(photos[1].id).note == "otra"  # type: ignore[union-attr]  # al cerrar también


def test_visor_tecla_z_alterna_zoom(qapp, tmp_path):
    from ui.viewer import ListSequence, ViewerWindow

    db.init_db()
    p = tmp_path / "grande.jpg"
    Image.new("RGB", (2400, 1600)).save(p)
    pid = db.upsert_photo(str(p), p.name, 2020, 1, p.stat().st_size, 2400, 1600, mtime=p.stat().st_mtime)
    v = ViewerWindow(ListSequence([db.get_photo_by_id(pid)]), 0, fullscreen=False)  # type: ignore[list-item]
    v.resize(900, 600)
    v.show()
    try:
        assert wait_for(qapp, lambda: v.image_view.has_image() and not v.image_view.is_preview())
        v.toggle_zoom()
        assert v.image_view.zoom() == 1.0 and not v.image_view.is_fit()
        v.toggle_zoom()
        assert v.image_view.is_fit()
    finally:
        v.close()


def test_gestor_de_etiquetas_fusionar_y_editar(qapp, tmp_path, monkeypatch):
    from ui.dialogs.tags import EditTagDialog, TagManagerDialog

    ids = make_photos(tmp_path, 2)
    a = services.add_tag(ids[0], "perro")
    b = services.add_tag(ids[1], "perros")
    dlg = TagManagerDialog()
    dlg.search_edit.setText("perro")
    assert sum(not row.isHidden() for row, _h in dlg._rows) == 2
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **k: (args[3][args[3].index("perro")], True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg._merge(b)
    assert db.get_tag(b.id) is None and db.resolve_tag_name("perros") == a.id
    assert services.count_photos_with_tag(a.id) == 2

    gato = services.create_tag("animales")
    tag = db.get_tag(a.id)
    assert tag is not None
    edit = EditTagDialog(tag)
    edit.parent_combo.setCurrentIndex(edit.parent_combo.findData(gato))
    assert edit.alias_edit.text() == "perros"  # el nombre fusionado ya es alias
    edit.alias_edit.setText(edit.alias_edit.text() + ", perrito, can")
    edit._save()
    tag = db.get_tag(a.id)
    assert tag is not None and tag.parent_id == gato
    assert services.get_tag_aliases()[a.id] == ["can", "perrito", "perros"]
    # El padre no puede ser una descendiente (no aparece en la lista)
    animales = db.get_tag(gato)
    assert animales is not None and EditTagDialog(animales).parent_combo.findData(a.id) == -1
    dlg.close()
