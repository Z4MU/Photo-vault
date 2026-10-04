"""Arranque: main.py no debe cargar la UI antes de configurar el logging."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_importar_main_no_carga_qt_ni_la_ui():
    # Si PyQt o la UI se importaran arriba de todo, un fallo ahí cerraría el
    # .exe (sin consola) sin dejar nada en el log.
    loaded = _run(
        "import sys, main; print(sorted(m for m in ('PyQt6', 'ui', 'PIL', 'database') if m in sys.modules))"
    )
    assert loaded == "[]"


def test_fallo_al_importar_la_ui_queda_en_el_log(tmp_path):
    # Simula que falta un módulo de la UI y comprueba que main() lo registra
    code = (
        "import sys, builtins, logging_setup, main\n"
        f"logging_setup.LOG_DIR = __import__('pathlib').Path(r'{tmp_path}')\n"
        "logging_setup.LOG_FILE = logging_setup.LOG_DIR / 'photovault.log'\n"
        "real_import = builtins.__import__\n"
        "def fake(name, *a, **k):\n"
        "    if name == 'ui.main_window':\n"
        "        raise ImportError('simulado')\n"
        "    return real_import(name, *a, **k)\n"
        "builtins.__import__ = fake\n"
        "print(main.main())\n"
    )
    assert _run(code) == "2"
    log = (tmp_path / "photovault.log").read_text(encoding="utf-8")
    assert "No se pudo cargar la aplicación" in log and "simulado" in log
