"""
PhotoVault - ui/dialogs/keymap.py
Teclas del etiquetado rápido: helpers para usarlas y diálogo para remapearlas.
"""

import logging
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import services
from ui.style import DARK_STYLE

logger = logging.getLogger(__name__)

MODES = [
    ("review", "Sí / no"),
    ("grid", "Cuadrícula"),
    ("multi", "Varias etiquetas"),
    ("common", "En todos los modos"),
]
KEYS_PER_ACTION = 2


def normalize_key(text: str) -> str:
    """Texto canónico de una tecla ("ctrl+z" → "Ctrl+Z"); "" si no es válida."""
    return QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)


def display_keys(keys: list[str]) -> str:
    """Para mostrar en la ventana: "→ / D"."""
    native = [QKeySequence(k).toString(QKeySequence.SequenceFormat.NativeText) for k in keys]
    arrows = {"Right": "→", "Left": "←", "Up": "↑", "Down": "↓", "Space": "Espacio", "Return": "Enter"}
    return " / ".join(arrows.get(k, k) for k in native if k)


def bind_keys(parent: QWidget, keys: list[str], slot: Callable[[], object]) -> list[QShortcut]:
    """Un QShortcut por tecla (con contexto de ventana). Devuelve los creados."""
    out = []
    for k in keys:
        seq = QKeySequence(k)
        if not seq.isEmpty():
            sc = QShortcut(seq, parent)
            sc.activated.connect(slot)
            out.append(sc)
    return out


class KeymapDialog(QDialog):
    """Remapear las teclas de los modos de etiquetado y las teclas de cada etiqueta."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Teclas del etiquetado rápido")
        self.setMinimumSize(620, 560)
        self.setStyleSheet(DARK_STYLE)
        self._edits: dict[str, list[QKeySequenceEdit]] = {}
        self._tag_edits: dict[int, QKeySequenceEdit] = {}
        self._tag_rows: list[tuple[QWidget, str]] = []
        self._build_ui()
        self._load(services.get_keymap(), services.get_tag_keys())

    @staticmethod
    def _key_edit() -> QKeySequenceEdit:
        e = QKeySequenceEdit()
        e.setMaximumSequenceLength(1)  # una tecla (con modificadores), no secuencias tipo "Ctrl+K, Ctrl+S"
        e.setClearButtonEnabled(True)
        e.setFixedWidth(130)
        return e

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        info = QLabel(
            "Haz clic en una casilla y pulsa la tecla. Cada acción puede tener dos teclas. "
            "En la cuadrícula, 1–9 siempre marcan las celdas."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#8888AA;font-size:11px;")
        lay.addWidget(info)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, stretch=1)

        for mode, title in MODES:
            page = QWidget()
            grid = QGridLayout(page)
            grid.setVerticalSpacing(6)
            row = 0
            for action, (desc, _keys) in services.KEY_ACTIONS.items():
                if not action.startswith(mode + "."):
                    continue
                lbl = QLabel(desc)
                lbl.setWordWrap(True)
                grid.addWidget(lbl, row, 0)
                edits = [self._key_edit() for _ in range(KEYS_PER_ACTION)]
                for col, e in enumerate(edits, start=1):
                    grid.addWidget(e, row, col)
                self._edits[action] = edits
                row += 1
            grid.setRowStretch(row, 1)
            if mode == "multi":
                grid.addWidget(self._build_tag_keys(), row + 1, 0, 1, 3)
                grid.setRowStretch(row + 1, 4)
            self.tabs.addTab(page, title)

        btns = QHBoxLayout()
        btn_reset = QPushButton("Restaurar de fábrica")
        btn_reset.clicked.connect(self._reset)
        btns.addWidget(btn_reset)
        btns.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Guardar")
        btn_save.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_save.clicked.connect(self._save)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

    def _build_tag_keys(self) -> QWidget:
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 8, 0, 0)
        title = QLabel("<b>Tecla de cada etiqueta</b> (cualquier tecla; no hay límite de 9)")
        bl.addWidget(title)
        self.tag_search = QLineEdit()
        self.tag_search.setPlaceholderText("Buscar etiqueta…")
        self.tag_search.textChanged.connect(self._filter_tags)
        bl.addWidget(self.tag_search)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setSpacing(2)
        paths = services.tag_path_names()
        for tag in sorted(
            services.get_all_tags(), key=lambda t: (t.category, " › ".join(paths.get(t.id, [t.name])))
        ):
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(2, 0, 2, 0)
            name = " › ".join(paths.get(tag.id, [tag.name]))
            lbl = QLabel(f"● {name}  [{tag.category}]")
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setStyleSheet(f"color:{tag.color};")
            hl.addWidget(lbl, stretch=1)
            edit = self._key_edit()
            hl.addWidget(edit)
            il.addWidget(row)
            self._tag_edits[tag.id] = edit
            self._tag_rows.append((row, name.lower()))
        il.addStretch()
        scroll.setWidget(inner)
        bl.addWidget(scroll, stretch=1)
        return box

    def _filter_tags(self, text: str) -> None:
        text = text.strip().lower()
        for row, name in self._tag_rows:
            row.setVisible(not text or text in name)

    # ── Datos ─────────────────────────────────────────────────────────────────

    def _load(self, keymap: dict[str, list[str]], tag_keys: dict[str, int]) -> None:
        for action, edits in self._edits.items():
            keys = keymap.get(action, [])
            for i, e in enumerate(edits):
                e.setKeySequence(QKeySequence(keys[i]) if i < len(keys) else QKeySequence())
        by_tag = {tid: key for key, tid in tag_keys.items()}
        for tid, e in self._tag_edits.items():
            e.setKeySequence(QKeySequence(by_tag.get(tid, "")))

    def values(self) -> tuple[dict[str, list[str]], dict[str, int]]:
        keymap = {
            action: [k for e in edits if (k := normalize_key(e.keySequence().toString()))]
            for action, edits in self._edits.items()
        }
        tag_keys: dict[str, int] = {}
        for tid, e in self._tag_edits.items():
            k = normalize_key(e.keySequence().toString())
            if k:
                tag_keys[k] = tid
        return keymap, tag_keys

    def _reset(self) -> None:
        defaults = {a: list(keys) for a, (_d, keys) in services.KEY_ACTIONS.items()}
        tags = services.get_all_tags()[: services.DEFAULT_TAG_KEYS]
        self._load(defaults, {str(i + 1): t.id for i, t in enumerate(tags)})

    def _save(self) -> None:
        keymap, tag_keys = self.values()
        # Dos etiquetas con la misma tecla: values() se quedaría con una sola
        used: dict[str, int] = {}
        for tid, e in self._tag_edits.items():
            k = normalize_key(e.keySequence().toString())
            if k and k in used and used[k] != tid:
                QMessageBox.warning(self, "Teclas repetidas", f"«{k}» está asignada a dos etiquetas.")
                return
            used[k] = tid
        problems = services.keymap_conflicts(keymap, tag_keys)
        if problems:
            QMessageBox.warning(
                self, "Teclas repetidas", "Corrige esto antes de guardar:\n\n" + "\n".join(problems)
            )
            return
        services.set_keymap(keymap)
        services.set_tag_keys(tag_keys)
        self.accept()
