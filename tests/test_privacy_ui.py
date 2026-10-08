"""Fase 9 en la UI: mostrar/bloquear lo oculto, pedir el PIN, modo pánico, bloqueo automático, Configuración."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QDialog  # noqa: E402

import database as db  # noqa: E402
import privacy  # noqa: E402
import services  # noqa: E402
from tests.test_gallery_ui import make_photos, wait_for  # noqa: E402


@pytest.fixture
def world(qapp, tmp_path):
    """8 fotos; 'privado' (oculta) la tienen 3."""
    ids = make_photos(tmp_path, 8)
    secret = services.create_tag("privado")
    for pid in ids[:3]:
        services.add_tag_by_id(pid, secret)
    services.set_tag_hidden(secret, True)
    return ids, secret


@pytest.fixture
def window(qapp, world, monkeypatch):
    import ui.privacy_actions

    # En vez del diálogo del PIN
    monkeypatch.setattr(
        ui.privacy_actions, "ensure_unlocked", lambda parent, reason="": privacy.unlock("1234")
    )
    from ui.main_window import MainWindow

    privacy.set_pin("1234")
    privacy.lock()
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    assert wait_for(qapp, lambda: w.model.count() == 5, 10)
    yield w
    w.close()
    qapp.processEvents()


def test_mostrar_y_bloquear_lo_oculto(qapp, window):
    w = window
    assert w.btn_hidden.text() == "🔒"
    assert "privado" not in {t.name for t in services.get_all_tags()}
    w.toggle_hidden_content()
    assert privacy.is_unlocked() and w.model.count() == 8
    assert "Oculto visible" in w.btn_hidden.text()
    assert any("visible" in t for t in w.toasts.texts())
    w.toggle_hidden_content()
    assert not privacy.is_unlocked() and w.model.count() == 5
    assert any("bloqueado" in t for t in w.toasts.texts())


def test_atajo_mostrar_oculto(qapp, window):
    w = window
    w.activateWindow()
    assert wait_for(qapp, lambda: qapp.activeWindow() is w, 5)
    QTest.keySequence(w, QKeySequence("Ctrl+Shift+H"))  # type: ignore[call-overload]
    assert wait_for(qapp, lambda: w.model.count() == 8, 5)


def test_panico_desde_un_dialogo_modal(qapp, window):
    w = window
    privacy.set_options(privacy.PrivacyOptions(panic_key="F12", panic_action="minimize"))
    w.apply_privacy_options()
    w.toggle_hidden_content()
    assert w.model.count() == 8
    dlg = QDialog(w)  # como el visor: un diálogo modal con su propio bucle (exec)
    dlg.resize(300, 200)

    def press():
        dlg.activateWindow()
        QTest.keyClick(dlg, Qt.Key.Key_F12)  # type: ignore[call-overload]

    QTimer.singleShot(200, press)
    QTimer.singleShot(5000, dlg.accept)  # por si el modo pánico no funcionara
    t = time.monotonic()
    result = dlg.exec()
    assert result == QDialog.DialogCode.Rejected and time.monotonic() - t < 4
    assert wait_for(qapp, lambda: w.isMinimized(), 5)
    assert not privacy.is_unlocked() and w.model.count() == 5
    assert w.toasts.texts() == [] or all("bloqueado" in t for t in w.toasts.texts())


def test_panico_cerrar_la_app(qapp, window):
    w = window
    privacy.set_options(privacy.PrivacyOptions(panic_key="Ctrl+Alt+P", panic_action="close"))
    w.apply_privacy_options()
    w.activateWindow()
    QTest.keySequence(w.view, QKeySequence("Ctrl+Alt+P"))  # type: ignore[call-overload]
    assert wait_for(qapp, lambda: not w.isVisible(), 5)
    assert w._closing


def test_bloqueo_por_inactividad_y_al_minimizar(qapp, window):
    w = window
    privacy.set_options(privacy.PrivacyOptions(autolock_minutes=1))
    w.apply_privacy_options()
    w.toggle_hidden_content()
    w.privacy_guard.check_idle()
    assert privacy.is_unlocked()  # todavía no pasó el minuto
    w.privacy_guard._last_activity -= 61
    w.privacy_guard.check_idle()
    assert not privacy.is_unlocked() and w.model.count() == 5
    assert any("sin uso" in t for t in w.toasts.texts())

    w.toggle_hidden_content()
    w.showMinimized()
    assert wait_for(qapp, lambda: not privacy.is_unlocked(), 5)
    w.showNormal()
    privacy.set_options(privacy.PrivacyOptions(lock_on_minimize=False))
    w.toggle_hidden_content()
    w.showMinimized()
    qapp.processEvents()
    assert privacy.is_unlocked()


def test_dialogo_pin(qapp, db_path):
    from ui.dialogs.privacy import NewPinDialog, PinDialog

    db.init_db()
    privacy.set_pin("1234")
    privacy.lock()
    d = PinDialog()
    d.edit.setText("9999")
    d._try()
    assert d.error_lbl.text() == "PIN incorrecto." and d.result() != QDialog.DialogCode.Accepted
    d.edit.setText("1234")
    d._try()
    assert d.result() == QDialog.DialogCode.Accepted and privacy.is_unlocked()

    n = NewPinDialog()
    n.edit1.setText("abcd")
    n.edit2.setText("abce")
    n._accept()
    assert "no coinciden" in n.error_lbl.text() and n.pin == ""
    n.edit2.setText("abcd")
    n._accept()
    assert n.pin == "abcd"


def test_dialogo_pin_espera_tras_muchos_intentos(qapp, db_path):
    from ui.dialogs.privacy import PinDialog

    db.init_db()
    privacy.set_pin("1234")
    privacy.lock()
    for _ in range(privacy.MAX_ATTEMPTS):
        privacy.unlock("0")
    d = PinDialog()
    assert not d.btn_ok.isEnabled() and "Espera" in d.error_lbl.text()


def test_configuracion_privacidad(qapp, window):
    from ui.dialogs.settings import SettingsDialog

    dlg = SettingsDialog(window)
    panel = dlg.privacy_panel
    assert panel.btn_change.isVisibleTo(panel) and not panel.btn_create.isVisibleTo(panel)
    panel.key_edit.setKeySequence(QKeySequence("P"))
    dlg._apply()
    assert dlg.result() != QDialog.DialogCode.Accepted and "Modo pánico" in panel.error_lbl.text()
    assert dlg.tabs.currentWidget() is panel
    panel.key_edit.setKeySequence(QKeySequence("F9"))
    panel.spin_idle.setValue(0)
    dlg._apply()
    assert dlg.result() == QDialog.DialogCode.Accepted
    opts = privacy.get_options()
    assert (opts.panic_key, opts.autolock_minutes) == ("F9", 0)
    assert window.privacy_guard._panic_key == "F9"


def test_gestor_de_etiquetas_avisa_de_las_ocultas(qapp, window, monkeypatch):
    from PyQt6.QtWidgets import QLabel

    import ui.dialogs.tags
    from ui.dialogs.tags import TagManagerDialog

    monkeypatch.setattr(ui.dialogs.tags, "ensure_unlocked", lambda parent, reason="": privacy.unlock("1234"))

    dlg = TagManagerDialog(window)
    texts = [lbl.text() for lbl in dlg.container.findChildren(QLabel)]
    assert any("1 etiqueta(s) oculta(s)" in t for t in texts)
    assert not any("privado" in t for t in texts)
    dlg._unlock_hidden()
    texts = [lbl.text() for lbl in dlg.container.findChildren(QLabel)]
    assert any("privado" in t for t in texts)
    dlg.reject()
