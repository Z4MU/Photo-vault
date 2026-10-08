"""Fase 8: sesión, avisos, bienvenida, carpetas vigiladas y cola de indexación."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import database as db  # noqa: E402
import services  # noqa: E402
import thumbnail_cache  # noqa: E402
from services import GalleryQuery  # noqa: E402
from tests.test_gallery_ui import make_photos, wait_for  # noqa: E402


def new_images(folder, n: int, prefix: str = "n") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (i * 30 % 255, 80, 120)).save(folder / f"{prefix}{i}.jpg")


# ── Servicios ─────────────────────────────────────────────────────────────────


def test_sesion_y_consulta_restaurable(tmp_path):
    make_photos(tmp_path, 4)
    assert services.get_session() == {} and services.is_restore_session_enabled()
    services.save_session({"query": GalleryQuery(min_rating=0, year_from=2011).to_dict(), "tab": 2})
    state = services.get_session()
    assert state["tab"] == 2
    assert services.restorable_query(state) == GalleryQuery(year_from=2011)
    # Una consulta que ya no muestra nada (o la de por defecto) no se restaura
    assert services.restorable_query({"query": GalleryQuery(folder=r"Z:\no").to_dict()}) is None
    assert services.restorable_query({"query": GalleryQuery().to_dict()}) is None
    assert services.restorable_query({"query": "basura"}) is None
    services.set_restore_session_enabled(False)
    assert not services.is_restore_session_enabled()


def test_carpetas_vigiladas(tmp_path):
    make_photos(tmp_path, 10)  # tmp/fotos/2010 … 2014
    root = str(tmp_path / "fotos")
    services.set_watched_folders([root, root + os.sep, "", str(tmp_path / "no-existe")])
    assert services.get_watched_folders() == [os.path.normpath(root), str(tmp_path / "no-existe")]
    assert services.available_watched_folders() == [os.path.normpath(root)]
    dirs = services.watch_dirs([root])
    assert dirs[0] == os.path.normpath(root) and len(dirs) == 6  # la raíz + 5 subcarpetas indexadas
    assert services.watch_dirs([str(tmp_path / "fotos" / "2012")]) == [str(tmp_path / "fotos" / "2012")]
    services.set_watch_options(on_start=False, live=True)
    assert (services.is_watch_on_start(), services.is_watch_live()) == (False, True)


def test_limpiar_cache(tmp_path):
    p = tmp_path / "a.jpg"
    Image.new("RGB", (300, 200)).save(p)
    assert thumbnail_cache.get_thumbnail(str(p), size=200)
    assert thumbnail_cache.CACHE_DIR.exists()
    thumbnail_cache.clear_cache()
    assert not thumbnail_cache.CACHE_DIR.exists()
    thumbnail_cache.clear_cache()  # sin caché: no falla


# ── Avisos y estado vacío ─────────────────────────────────────────────────────


def test_avisos(qapp):
    from PyQt6.QtWidgets import QWidget

    from ui.toast import MAX_TOASTS, ToastManager

    host = QWidget()
    host.resize(800, 600)
    host.show()
    tm = ToastManager(host)
    for i in range(MAX_TOASTS + 2):
        tm.show(f"aviso {i}", "success", ms=60_000)
    assert len(tm.toasts) == MAX_TOASTS and tm.texts()[-1] == f"aviso {MAX_TOASTS + 1}"
    newest = tm.toasts[-1]
    assert newest.geometry().right() <= host.width() and newest.y() > tm.toasts[0].y()  # el nuevo, abajo
    newest.dismiss()
    assert wait_for(qapp, lambda: newest not in tm.toasts, 3)
    short = tm.show("<b>no es html</b>", ms=50)
    assert short.textFormat().name == "PlainText"
    assert wait_for(qapp, lambda: short not in tm.toasts, 3)
    host.close()


def test_estado_vacio(qapp):
    from ui.welcome import EmptyState

    e = EmptyState()
    e.show_welcome()
    assert e.mode == "welcome" and not e.btn_index.isHidden() and e.btn_clear.isHidden()
    e.show_no_results()
    assert e.mode == "no_results" and e.btn_index.isHidden() and not e.btn_clear.isHidden()


# ── Ventana principal ─────────────────────────────────────────────────────────


@pytest.fixture
def make_window(qapp):
    windows = []

    def make():
        from ui.main_window import MainWindow

        w = MainWindow()
        w.resize(1300, 800)
        w.show()
        qapp.processEvents()
        windows.append(w)
        return w

    yield make
    for w in windows:
        w.close()
    qapp.processEvents()


def test_bienvenida_y_sin_resultados(qapp, tmp_path, make_window):
    db.init_db()
    w = make_window()
    assert w.gallery_stack.currentWidget() is w.empty and w.empty.mode == "welcome"
    w.close()
    make_photos(tmp_path, 3)
    w = make_window()
    assert w.gallery_stack.currentWidget() is w.view
    w.search_edit.setText("no-coincide-nada")
    w._on_search()
    assert w.gallery_stack.currentWidget() is w.empty and w.empty.mode == "no_results"
    w.empty.btn_clear.click()
    assert w.gallery_stack.currentWidget() is w.view and w.model.count() == 3


def test_recordar_la_sesion(qapp, tmp_path, make_window):
    make_photos(tmp_path, 40)
    w = make_window()
    w.filter_bar.year_from.setValue(2011)
    w.btn_filters.setChecked(True)
    w.tabs.setCurrentWidget(w.timeline_panel)
    w.view.scroll_to_row(20)
    row = w.view.first_visible_row()
    w.close()
    w2 = make_window()
    assert w2.model.query().year_from == 2011 and w2.filter_bar.year_from.value() == 2011
    assert w2.btn_filters.isChecked() and w2.tabs.currentWidget() is w2.timeline_panel
    assert row and abs((w2.view.first_visible_row() or 0) - row) <= 6
    # Una consulta que ya no muestra nada: se abre con todo y avisa
    w2.close()  # guarda su sesión; abajo se reemplaza la consulta por una sin resultados
    services.save_session({**services.get_session(), "query": GalleryQuery(folder=r"Z:\no").to_dict()})
    w3 = make_window()
    assert w3.model.count() == 40 and any("ya no muestran" in t for t in w3.toasts.texts())
    # Con la opción desactivada no se restaura nada
    w3.filter_bar.year_from.setValue(2013)
    services.set_restore_session_enabled(False)
    w3.close()
    w4 = make_window()
    assert w4.model.query().year_from is None


def test_cola_de_indexacion_y_carpeta_vigilada(qapp, tmp_path, make_window):
    db.init_db()
    a, b = tmp_path / "a", tmp_path / "b"
    new_images(a, 3)
    new_images(b, 2)
    w = make_window()
    w.start_indexing(str(a))
    w.start_indexing(str(b))  # espera su turno
    w.start_indexing(str(b))  # repetida: no se encola dos veces
    assert [r.folder for r in w._index_queue] == [str(b)]
    assert any("cuando termine" in t for t in w.toasts.texts())
    assert wait_for(qapp, lambda: w._bg_kind is None and not w._index_queue and w.model.count() == 5, 30)

    # Revisión automática sin cambios: sin avisos
    n_toasts = len(w.toasts.texts())
    w.scan_watched_folders([str(a)])
    assert wait_for(qapp, lambda: w._bg_kind is None, 30)
    assert len(w.toasts.texts()) == n_toasts
    # Con fotos nuevas: se indexan y avisa
    new_images(a, 2, prefix="extra")
    w.scan_watched_folders([str(a)])
    assert wait_for(qapp, lambda: w.model.count() == 7, 30)
    assert any("📥" in t for t in w.toasts.texts())


def test_vigilante_avisa_cuando_algo_cambia(qapp, tmp_path):
    from ui.folder_watch import FolderWatcher

    make_photos(tmp_path, 5)
    root = str(tmp_path / "fotos")
    fw = FolderWatcher(settle_ms=150)
    got: list[str] = []
    fw.changed.connect(got.append)
    fw.set_roots([root])
    assert len(fw.watched_dirs()) == 6
    time.sleep(0.05)
    Image.new("RGB", (10, 10)).save(tmp_path / "fotos" / "2012" / "nueva.jpg")  # en una subcarpeta
    assert wait_for(qapp, lambda: got == [os.path.normpath(root)], 10)
    fw.set_roots([])
    assert fw.watched_dirs() == []
    fw.stop()


def test_configuracion_carpetas_vigiladas(qapp, tmp_path, make_window, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from ui.dialogs.settings import SettingsDialog

    db.init_db()
    folder = tmp_path / "vigilada"
    new_images(folder, 2)
    w = make_window()
    dlg = SettingsDialog(w)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    dlg._add_watched()
    dlg._add_watched()  # repetida: no se agrega dos veces
    assert dlg.watched_folders() == [str(folder)]
    dlg.chk_session.setChecked(False)
    dlg._apply()
    assert services.get_watched_folders() == [str(folder)] and not services.is_restore_session_enabled()
    assert wait_for(qapp, lambda: w.watcher.roots() == [str(folder)], 10)  # se monta en un hilo
    # La carpeta recién agregada se revisa ya
    assert wait_for(qapp, lambda: w.model.count() == 2, 30)


def test_ventana_cerrada_ignora_avisos_tardios(qapp, tmp_path, make_window):
    """Antes: un 'indexación terminada' en camino al cerrar arrancaba miniaturas y mostraba un error."""
    import indexer

    make_photos(tmp_path, 2)
    w = make_window()
    w.close()
    late = indexer.IndexResult(added=1, new_ids=[1])
    w._on_index_completed(late)
    w._on_background_error("la base ya no existe")  # sin diálogo (conftest falla si aparece uno)
    assert w._bg_worker is None and not w.start_thumbnail_generation([1])
    w.start_indexing(str(tmp_path))
    assert w._bg_worker is None
