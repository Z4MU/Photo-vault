"""Funciones de la UI que se pueden probar sin abrir ventanas (#3, #4, #8)."""

import importlib
import os
import pkgutil
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtGui import QImage, QImageReader  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

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
    root  = ET.fromstring(svg)   # Lanza si el XML está roto
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
    exif = Image.Exif(); exif[0x0112] = 6
    img.save(src, format="JPEG", exif=exif)

    pix = images.load_preview_pixmap(str(src), 500)
    assert pix is not None
    assert max(pix.width(), pix.height()) <= 500
    assert pix.height() > pix.width()        # rotada a vertical


def test_preview_formato_que_qt_no_lee_usa_pillow(qapp, tmp_path, monkeypatch):
    src = tmp_path / "foto.jpg"
    Image.new("RGB", (300, 200)).save(src)
    # Simular que Qt no sabe leerla (como HEIC)
    monkeypatch.setattr(QImageReader, "read", lambda self: QImage())
    pix = images.load_preview_pixmap(str(src), 100)
    assert pix is not None and pix.width() == 100


def test_retire_thread_mantiene_vivo_hasta_terminar(qapp):
    class Lento(workers.StoppableThread):
        def run(self):
            while not self.is_stopping():
                self.msleep(5)
            self.msleep(50)   # Sigue un rato después de pedirle que pare

    t = Lento(); t.start()
    workers.retire_thread(t)
    assert t in workers._retired_threads
    del t
    workers.wait_all_threads(2000)
    qapp.processEvents()
    assert not any(th.isRunning() for th in workers._retired_threads)
