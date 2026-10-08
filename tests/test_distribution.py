"""Fase 11: aviso de versión nueva, Acerca de, etiquetas de ejemplo, avisos con acción, empaquetado."""

import io
import json
import os
import re
import urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import config  # noqa: E402
import database as db  # noqa: E402
import services  # noqa: E402
import updates  # noqa: E402
from tests.test_gallery_ui import wait_for  # noqa: E402
from updates import fetch_latest as real_fetch_latest  # noqa: E402  (conftest la reemplaza en cada test)

ROOT = Path(__file__).resolve().parent.parent


# ── Versiones ─────────────────────────────────────────────────────────────────


def test_comparar_versiones():
    assert updates.parse_version("v3.1.0") == (3, 1, 0)
    assert updates.parse_version("3.1") == (3, 1, 0)
    assert updates.parse_version("3.1.0-beta") is None
    assert updates.is_newer("3.0.1", "3.0.0") and updates.is_newer("v10.0", "9.9.9")
    assert not updates.is_newer("3.0.0", "3.0.0") and not updates.is_newer("2.9", "3.0.0")
    assert not updates.is_newer("basura", "3.0.0")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _serve(monkeypatch, payload=None, error=None):
    def urlopen(req, timeout):
        assert req.full_url == config.RELEASES_API and "PhotoVault" in req.get_header("User-agent")
        if error is not None:
            raise error
        return FakeResp(json.dumps(payload).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)


def test_leer_la_ultima_version(monkeypatch):
    _serve(monkeypatch, {"tag_name": "v9.1.0", "html_url": "https://x/r", "name": "Nueva", "body": "cambios"})
    r = real_fetch_latest()
    assert r == updates.Release("9.1.0", "https://x/r", "Nueva", "cambios")
    _serve(monkeypatch, {"tag_name": "v9.2.0", "prerelease": True})
    assert real_fetch_latest() is None
    _serve(monkeypatch, error=urllib.error.HTTPError(config.RELEASES_API, 404, "no", {}, io.BytesIO()))  # type: ignore[arg-type]
    assert real_fetch_latest() is None  # todavía no hay versiones publicadas
    _serve(monkeypatch, error=urllib.error.URLError("sin red"))
    assert real_fetch_latest() is None


def test_revisar_una_vez_al_dia_y_saltar_version(db_path, monkeypatch):
    db.init_db()
    release = updates.Release("99.0.0", "https://x/r", "n", "")
    calls: list[int] = []

    def fake_fetch(url=None):
        calls.append(1)
        return release

    monkeypatch.setattr(updates, "fetch_latest", fake_fetch)
    assert updates.is_enabled() and updates.is_due()
    assert updates.check_for_update() == release
    assert updates.check_for_update() is None and len(calls) == 1  # hasta mañana no
    assert updates.check_for_update(force=True) == release
    updates.skip_version("99.0.0")
    db.set_setting(updates.LAST_CHECK_SETTING, "0")
    assert updates.check_for_update() is None
    updates.set_enabled(False)
    assert not updates.is_due()
    # La misma versión instalada no es "nueva"
    monkeypatch.setattr(
        updates, "fetch_latest", lambda url=None: updates.Release(config.APP_VERSION, "u", "n", "")
    )
    assert updates.check_for_update(force=True) is None


# ── Etiquetas de ejemplo ──────────────────────────────────────────────────────


def test_etiquetas_de_ejemplo(db_path):
    db.init_db()
    assert db.get_all_tags() == []
    n = services.add_starter_tags(["tipo", "privado", "no-existe"])
    assert n == len(services.STARTER_TAGS["tipo"]) + len(services.STARTER_TAGS["privado"])
    assert {"tipo", "privado"} <= set(db.get_all_categories())
    # No duplica (tampoco si ya hay una con ese nombre como alias)
    other = services.create_tag("animales")
    services.update_tag(other, "animales", "general", "#FFFFFF", aliases=["mascotas"])
    assert services.add_starter_tags(["tipo"]) == 0
    assert services.add_starter_tags(["temas"]) == len(services.STARTER_TAGS["temas"]) - 1
    assert set(services.STARTER_DESCRIPTIONS) == set(services.STARTER_TAGS)


def test_dialogo_etiquetas_de_ejemplo(qapp, db_path):
    from ui.dialogs.starter_tags import DEFAULT_GROUPS, StarterTagsDialog

    db.init_db()
    dlg = StarterTagsDialog()
    assert dlg.selected_groups() == list(DEFAULT_GROUPS)
    dlg.checks["personas"].setChecked(False)
    dlg._add()
    assert dlg.created == len(services.STARTER_TAGS["tipo"]) + len(services.STARTER_TAGS["temas"])


# ── UI ────────────────────────────────────────────────────────────────────────


def test_aviso_con_accion(qapp):
    from PyQt6.QtWidgets import QWidget

    from ui.toast import ToastManager

    host = QWidget()
    host.resize(600, 400)
    clicks = []
    toast = ToastManager(host).show("hay versión", on_click=lambda: clicks.append(1))
    toast.mousePressEvent(None)
    toast.mousePressEvent(None)
    assert clicks == [1]


def test_aviso_de_version_nueva_al_abrir(qapp, db_path, monkeypatch):
    import ui.update_notice
    from ui.main_window import MainWindow

    db.init_db()
    opened = []
    monkeypatch.setattr(ui.update_notice, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(ui.update_notice, "UPDATE_CHECK_DELAY_MS", 0)
    monkeypatch.setattr(
        updates, "fetch_latest", lambda url=None: updates.Release("99.0.0", "https://x/r", "n", "")
    )
    w = MainWindow()
    w.show()
    try:
        assert wait_for(qapp, lambda: any("99.0.0" in t for t in w.toasts.texts()), 10)
        toast = next(t for t in w.toasts.toasts if "99.0.0" in t.text())
        toast.mousePressEvent(None)
        assert opened == ["https://x/r"]
    finally:
        w.close()
        qapp.processEvents()


def test_acerca_de(qapp, db_path, monkeypatch):
    from ui.dialogs.about import AboutDialog

    db.init_db()
    monkeypatch.setattr(
        updates, "fetch_latest", lambda url=None: updates.Release("99.0.0", "https://x/r", "n", "")
    )
    dlg = AboutDialog()
    dlg._check()
    assert wait_for(qapp, lambda: dlg._worker is None, 10)
    assert "99.0.0" in dlg.update_lbl.text() and dlg.btn_open_release.isVisibleTo(dlg)
    monkeypatch.setattr(
        updates, "fetch_latest", lambda url=None: updates.Release(config.APP_VERSION, "u", "n", "")
    )
    dlg._check()
    assert wait_for(qapp, lambda: dlg._worker is None, 10)
    assert "última versión" in dlg.update_lbl.text()
    dlg.reject()


def test_configuracion_actualizaciones(qapp, db_path):
    from ui.dialogs.settings import SettingsDialog

    db.init_db()
    dlg = SettingsDialog()
    assert dlg.chk_updates.isChecked()
    dlg.chk_updates.setChecked(False)
    dlg._apply()
    assert not updates.is_enabled()


# ── Empaquetado ───────────────────────────────────────────────────────────────


def test_icono_y_version_para_el_exe():
    assert config.ICON_PATH.exists() and (ROOT / "assets" / "icon.ico").exists()
    spec = (ROOT / "PhotoVault.spec").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION = "([^"]+)"', (ROOT / "config.py").read_text(encoding="utf-8"))
    assert m and m.group(1) == config.APP_VERSION  # la regex del .spec encuentra la versión
    assert "assets/icon.png" in spec and "COLLECT(" in spec
    iss = (ROOT / "installer" / "PhotoVault.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss and "dist\\PhotoVault\\*" in iss


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_workflows_de_github(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert 'python-version: "3.14"' in text and "pytest" in text
