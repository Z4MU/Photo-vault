"""Fase 7 en la UI: modos sí / no y cuadrícula, varias etiquetas, teclas remapeables, configuración."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import database as db  # noqa: E402
import services  # noqa: E402
from services import GalleryQuery  # noqa: E402
from tests.test_gallery_ui import make_photos, wait_for  # noqa: E402


@pytest.fixture
def world(qapp, tmp_path):
    ids = make_photos(tmp_path, 14)
    tag = services.get_tag(services.create_tag("playa"))
    assert tag is not None
    q = GalleryQuery(sort_field=services.SortField.FILENAME, sort_order=services.SortOrder.ASC)
    return ids, tag, q


def key(widget, k) -> None:
    QTest.keyClick(widget, k)  # type: ignore[call-overload]


def show_active(qapp, w) -> None:
    """Los atajos de ventana solo funcionan en la ventana activa (en la app real lo está)."""
    w.show()
    w.activateWindow()
    assert wait_for(qapp, lambda: qapp.activeWindow() is w, 5)


def tagged(tag_id: int, ids: list[int]) -> set[int]:
    return db.get_ids_with_tag(tag_id, ids)


# ─── Sí / no ──────────────────────────────────────────────────────────────────


def test_si_no_con_teclas_saltar_y_deshacer(qapp, world):
    from ui.dialogs.review import ReviewWindow

    ids, tag, q = world
    lst = services.photo_list(services.review_query(q, tag.id))
    w = ReviewWindow(tag, lst)
    show_active(qapp, w)
    try:
        first, second, third = (lst.photo_at(i).id for i in range(3))  # type: ignore[union-attr]
        key(w, Qt.Key.Key_Right)  # sí
        key(w, Qt.Key.Key_A)  # no (segunda tecla de "no")
        key(w, Qt.Key.Key_Space)  # saltar
        assert (w.yes_count, w.no_count, w.skip_count, w.position) == (1, 1, 1, 3)
        assert tagged(tag.id, ids) == {first}
        assert db.get_rejected_ids(tag.id, ids) == {second}
        QTest.keyClick(w, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)  # type: ignore[call-overload]
        assert w.position == 2 and w.skip_count == 0  # deshacer el salto vuelve a la tercera
        w.undo()
        assert w.position == 1 and db.get_rejected_ids(tag.id, ids) == set()  # deshizo el "no"
        assert w.current() is not None and w.current().id == second  # type: ignore[union-attr]
        assert wait_for(qapp, w.stage.is_loaded)
        w.position = lst.count() - 1
        w.answer(True)
        assert "Listo" in w.stage.message.text() and third in lst.ids
    finally:
        w.close()


def test_teclas_remapeadas(qapp, world):
    from ui.dialogs.review import ReviewWindow

    ids, tag, q = world
    services.set_keymap({**services.get_keymap(), "review.yes": ["J"], "review.no": ["K"]})
    lst = services.photo_list(services.review_query(q, tag.id))
    w = ReviewWindow(tag, lst)
    show_active(qapp, w)
    try:
        key(w, Qt.Key.Key_Right)  # ya no es "sí"
        assert w.yes_count == 0
        key(w, Qt.Key.Key_J)
        key(w, Qt.Key.Key_K)
        assert (w.yes_count, w.no_count) == (1, 1)
    finally:
        w.close()


def test_sin_pendientes(qapp, world):
    from ui.dialogs.review import ReviewWindow

    _ids, tag, _q = world
    w = ReviewWindow(tag, services.PhotoList([]))
    show_active(qapp, w)
    assert "No hay fotos pendientes" in w.stage.message.text()
    w.answer(True)  # no hace nada
    w.close()


# ─── Cuadrícula ───────────────────────────────────────────────────────────────


def test_cuadricula_marcar_confirmar_deshacer(qapp, world):
    from ui.dialogs.review import GridReviewWindow

    ids, tag, q = world
    lst = services.photo_list(services.review_query(q, tag.id))
    w = GridReviewWindow(tag, lst, grid_size=9)
    show_active(qapp, w)
    try:
        page1 = [p.id for p in w.page_photos()]
        assert len(page1) == 9
        key(w, Qt.Key.Key_2)  # marca la celda 2
        w.toggle(4)
        assert w.marked_ids() == [page1[1], page1[4]]
        key(w, Qt.Key.Key_Return)  # confirmar
        assert tagged(tag.id, ids) == {page1[1], page1[4]}
        assert db.get_rejected_ids(tag.id, ids) == set(page1) - {page1[1], page1[4]}
        assert len(w.page_photos()) == 5 and (w.yes_count, w.no_count) == (2, 7)
        w.skip_page()
        assert w.page_photos() == [] and w.skip_count == 5
        w.undo()  # deshace el salto
        w.undo()  # deshace la página 1: vuelve con sus marcas
        assert [p.id for p in w.page_photos()] == page1 and w.marked_ids() == [page1[1], page1[4]]
        assert tagged(tag.id, ids) == set() and db.get_rejected_ids(tag.id, ids) == set()
        w.toggle_all()
        assert len(w.marked_ids()) == 9
        w.toggle_all()
        assert w.marked_ids() == []
        assert wait_for(qapp, lambda: any(c._pixmap is not None for c in w.cells))  # miniaturas
    finally:
        w.close()


# ─── Varias etiquetas ─────────────────────────────────────────────────────────


def test_varias_etiquetas_teclas_propias_repetir_y_retomar(qapp, world, monkeypatch):
    from ui.dialogs.quick_tag import QuickTagWindow

    ids, playa, q = world
    gato = services.create_tag("gato")
    services.set_tag_keys({"Q": playa.id, "Shift+W": gato})
    lst = services.photo_list(q)
    w = QuickTagWindow(lst, q)
    show_active(qapp, w)
    key(w, Qt.Key.Key_Q)
    QTest.keyClick(w, Qt.Key.Key_W, Qt.KeyboardModifier.ShiftModifier)  # type: ignore[call-overload]
    first = lst.ids[0]
    assert {t.id for t in services.get_photo_tags(first)} == {playa.id, gato}
    key(w, Qt.Key.Key_Right)
    QTest.keyClick(w, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)  # type: ignore[call-overload]  # repetir
    assert {t.id for t in services.get_photo_tags(lst.ids[1])} == {playa.id, gato}
    key(w, Qt.Key.Key_Right)
    w._undo()  # vuelve a la foto 2 (#53)
    assert w.index == 1 and services.get_photo_tags(lst.ids[1]) == []  # un Ctrl+Z deshace toda la repetición
    w.close()
    assert services.resume_position(q) == lst.ids[1]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w2 = QuickTagWindow(services.photo_list(q), q)
    assert w2.index == 1  # retomó
    w2.close()


# ─── Teclas y configuración ───────────────────────────────────────────────────


def test_dialogo_de_teclas(qapp, world, monkeypatch):
    from ui.dialogs.keymap import KeymapDialog, display_keys, normalize_key

    _ids, tag, _q = world
    assert normalize_key("ctrl+z") == "Ctrl+Z" and display_keys(["Right", "D"]) == "→ / D"
    dlg = KeymapDialog()
    dlg._edits["review.yes"][0].setKeySequence(QKeySequence("L"))
    dlg._edits["review.no"][0].setKeySequence(QKeySequence("L"))  # choca con "sí"
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text: warned.append(text))
    dlg._save()
    assert warned and dlg.result() != dlg.DialogCode.Accepted
    dlg._edits["review.no"][0].setKeySequence(QKeySequence("N"))
    dlg._tag_edits[tag.id].setKeySequence(QKeySequence("P"))
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted
    km = services.get_keymap()
    assert km["review.yes"] == ["L", "D"] and km["review.no"][0] == "N"
    assert services.get_tag_keys()["P"] == tag.id
    dlg2 = KeymapDialog()
    dlg2._reset()
    km2, _tags = dlg2.values()
    assert km2["review.yes"] == ["Right", "D"]


def test_configuracion_cuenta_y_recuerda(qapp, world):
    from ui.dialogs.quick_tag_setup import QuickTagSetupDialog

    ids, tag, q = world
    services.review_answer(tag.id, [ids[0]], [ids[1], ids[2]])
    dlg = QuickTagSetupDialog(q)
    dlg.tag_combo.setCurrentIndex(dlg.tag_combo.findData(tag.id))
    dlg.mode_buttons["grid"].setChecked(True)
    assert "Faltan 11 de 14" in dlg.count_lbl.text()
    dlg.scope_combo.setCurrentIndex(dlg.scope_combo.findData("gallery"))
    dlg.grid_combo.setCurrentIndex(dlg.grid_combo.findData(16))
    dlg._start()
    assert dlg.mode == "grid" and dlg.grid_size == 16
    assert len(services.get_gallery_ids(dlg.query)) == 11
    again = QuickTagSetupDialog(q)  # recuerda todo
    assert again.current_mode() == "grid" and again.current_tag_id() == tag.id
    assert again.scope_combo.currentData() == "gallery" and again.grid_combo.currentData() == 16
    again.mode_buttons["multi"].setChecked(True)
    assert not again.box_tag.isVisibleTo(again) and "14 fotos" in again.count_lbl.text()


def test_escenario_carga_y_precarga(qapp, world):
    from ui.photo_stage import PhotoStage

    _ids, _tag, q = world
    lst = services.photo_list(q)
    stage = PhotoStage()
    stage.resize(800, 600)
    stage.show()
    try:
        photos = [lst.photo_at(i) for i in range(4)]
        stage.show_photo(photos[0], [p for p in photos[1:] if p])
        assert wait_for(qapp, lambda: stage.is_loaded() and len(stage._images) == 4)
        stage.show_photo(photos[1], [])
        assert stage.is_loaded()  # ya estaba precargada: al instante
    finally:
        stage.shutdown()


def test_configuracion_se_construye_sin_errores_en_slots(qapp, world, monkeypatch):
    """Antes, marcar el modo por defecto disparaba _refresh con la ventana a medio armar."""
    import sys

    from ui.dialogs.quick_tag_setup import QuickTagSetupDialog

    errors: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda _t, e, _tb: errors.append(e))
    for mode in ("review", "grid", "multi"):
        services.set_quick_tag_setup({"mode": mode})
        dlg = QuickTagSetupDialog(world[2])
        qapp.processEvents()
        assert dlg.current_mode() == mode
    assert errors == []
