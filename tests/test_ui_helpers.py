"""Funciones de la UI que se pueden probar sin abrir ventanas (#3, #4, #8)."""

import importlib
import os
import pkgutil
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtGui import QImage, QImageReader  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

import ui  # noqa: E402
from ui import images, workers  # noqa: E402
from ui.charts import build_bar_chart_svg  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_todos_los_modulos_de_ui_importan():
    """Detecta imports rotos o circulares tras dividir main.py."""
    names = [m.name for m in pkgutil.walk_packages(ui.__path__, "ui.")]
    assert "ui.main_window" in names and "ui.dialogs.folders" in names
    for name in names:
        importlib.import_module(name)
    importlib.import_module("main")


def test_hoja_de_estilos_carga():
    from ui.style import DARK_STYLE

    assert "QPushButton" in DARK_STYLE and "#0D0D1A" in DARK_STYLE


def test_svg_escapa_nombres_de_tags():
    data = [("rock&roll", 5), ("<script>", 3), ("a\"b'c", 1)]
    svg, height = build_bar_chart_svg(data, "#4A9EFF")
    root = ET.fromstring(svg)  # Lanza si el XML está roto
    texts = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "rock&roll" in texts
    assert "<script>" in texts
    assert height > 0


def test_svg_recorta_antes_de_escapar():
    svg, _ = build_bar_chart_svg([("aaaaaaaaaaa&bbbb", 1)], "#000000")
    ET.fromstring(svg)


def test_preview_aplica_orientacion_y_escala(qapp, tmp_path):
    src = tmp_path / "celular.jpg"
    img = Image.new("RGB", (2000, 1000))
    exif = Image.Exif()
    exif[0x0112] = 6
    img.save(src, format="JPEG", exif=exif)

    pix = images.load_preview_pixmap(str(src), 500)
    assert pix is not None
    assert max(pix.width(), pix.height()) <= 500
    assert pix.height() > pix.width()  # rotada a vertical


def test_preview_formato_que_qt_no_lee_usa_pillow(qapp, tmp_path, monkeypatch):
    src = tmp_path / "foto.jpg"
    Image.new("RGB", (300, 200)).save(src)
    # Simular que Qt no sabe leerla (como HEIC)
    monkeypatch.setattr(QImageReader, "read", lambda self: QImage())
    pix = images.load_preview_pixmap(str(src), 100)
    assert pix is not None and pix.width() == 100


def test_clear_layout_quita_tambien_espaciadores(qapp):
    from PyQt6.QtWidgets import QVBoxLayout, QWidget

    from ui.widgets import clear_layout, layout_widgets

    host = QWidget()
    lay = QVBoxLayout(host)
    for _ in range(3):  # Tres "refrescos" seguidos
        clear_layout(lay)
        lay.addWidget(QLabel("a"))
        lay.addWidget(QLabel("b"))
        lay.addStretch()
    assert lay.count() == 3  # Antes se acumulaban los addStretch()
    labels = layout_widgets(lay)
    assert all(isinstance(w, QLabel) for w in labels)
    assert [w.text() for w in labels if isinstance(w, QLabel)] == ["a", "b"]


def test_clickable_row_emite_clicked(qapp):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    from ui.widgets import ClickableRow

    row = ClickableRow()
    hits = []
    row.clicked.connect(lambda: hits.append(1))
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(QPoint(1, 1)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    row.mousePressEvent(ev)
    assert hits == [1]


def test_buscar_en_etiquetado_rapido_oculta_categorias_vacias(qapp, db_path):
    import database as db
    from ui.dialogs.quick_tag import QuickTagWindow

    db.init_db()
    pid = db.upsert_photo(r"C:\no\existe.jpg", "existe.jpg", 2020, 1, 1)
    win = QuickTagWindow([p for p in db.get_photos() if p.id == pid])

    def headers() -> dict[str, bool]:
        return {
            w.text(): not w.isHidden()
            for w in win.tag_container.findChildren(QLabel)
            if w.property("cat_label")
        }

    win._filter_tags("meme")  # solo la categoría "tipo" tiene un tag así
    assert headers()["TIPO"] is True
    assert headers()["EMOCION"] is False
    win._filter_tags("")
    assert all(headers().values())
    win.close()


def test_cargador_de_miniaturas_en_paralelo_con_precarga(qapp, tmp_path, db_path):
    import database as db
    import thumbnail_cache as tc

    db.init_db()
    photos = []
    for i in range(10):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (400, 300)).save(p)
        db.upsert_photo(str(p), p.name, 2020, 1, p.stat().st_size, mtime=p.stat().st_mtime)
    photos = db.get_photos(limit=-1)
    page, nxt = photos[:6], photos[6:]

    got: dict[int, tuple[int, int]] = {}
    loader = workers.ThumbnailLoader(page, prefetch=nxt)
    loader.loaded.connect(lambda pid, img: got.__setitem__(pid, (img.width(), img.height())))
    loader.start()
    assert loader.wait(20_000)
    qapp.processEvents()

    assert set(got) == {p.id for p in page}
    assert all(max(wh) <= 200 for wh in got.values())
    # La página siguiente quedó en el caché de disco, sin emitirse
    assert all(tc.is_cached(p.path, mtime=p.mtime) for p in nxt)


def test_index_dialog_pide_indexar_y_se_cierra(qapp, tmp_path):
    from ui.dialogs.folders import IndexDialog

    dlg = IndexDialog()
    asked: list[str] = []
    dlg.start_requested.connect(asked.append)
    dlg.path_edit.setText(str(tmp_path))
    dlg._start()
    assert asked == [str(tmp_path)]
    assert dlg.result() == IndexDialog.DialogCode.Accepted
    busy = IndexDialog(busy=True)
    assert not busy.btn_start.isEnabled()


def test_barra_de_tarea(qapp):
    from ui.widgets import TaskStatusWidget

    w = TaskStatusWidget()
    hits = []
    w.cancel_clicked.connect(lambda: hits.append(1))
    w.start("Indexando…")
    assert not w.isHidden()
    w.set_progress(5, 10, "5/10")
    assert (w.bar.value(), w.bar.maximum()) == (5, 10)
    w.btn_cancel.click()
    assert hits == [1]
    w.finish("listo", hide_after_ms=1)
    assert w.btn_cancel.isHidden()


def test_retire_thread_mantiene_vivo_hasta_terminar(qapp):
    class Lento(workers.StoppableThread):
        def run(self):
            while not self.is_stopping():
                self.msleep(5)
            self.msleep(50)  # Sigue un rato después de pedirle que pare

    t = Lento()
    t.start()
    workers.retire_thread(t)
    assert t in workers._retired_threads
    del t
    workers.wait_all_threads(2000)
    qapp.processEvents()
    assert not any(th.isRunning() for th in workers._retired_threads)
