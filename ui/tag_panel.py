"""
PhotoVault - ui/tag_panel.py
Filtro por etiquetas del sidebar: incluir / excluir con un clic, "todas" o
"alguna", buscador (también por alias), contadores y jerarquía.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import services
from models import Tag
from ui.widgets import clear_layout

logger = logging.getLogger(__name__)

NONE, INCLUDE, EXCLUDE = 0, 1, 2


class TagFilterButton(QPushButton):
    """Una etiqueta del sidebar. Clic: nada → incluir (✓) → excluir (✕) → nada."""

    state_changed = pyqtSignal()

    def __init__(self, tag: Tag, label: str, depth: int, parent=None):
        super().__init__(parent)
        self.tag = tag
        self._label = label
        self._depth = depth
        self.state = NONE
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{label}\nClic: incluir → excluir → quitar")
        self.clicked.connect(self._cycle)
        self._restyle()

    def _cycle(self) -> None:
        self.set_state((self.state + 1) % 3)
        self.state_changed.emit()

    def set_state(self, state: int) -> None:
        self.state = state
        self._restyle()

    def _restyle(self) -> None:
        mark = {NONE: "", INCLUDE: "✓ ", EXCLUDE: "✕ "}[self.state]
        indent = "    " * self._depth + ("└ " if self._depth else "")
        self.setText(f"{indent}{mark}{self._label}")
        color = "#FF4A4A" if self.state == EXCLUDE else self.tag.color
        bg = (
            f"{self.tag.color}33"
            if self.state == INCLUDE
            else "#FF4A4A22"
            if self.state == EXCLUDE
            else "transparent"
        )
        border = color if self.state != NONE else "transparent"
        italic = "font-style:italic;" if self.tag.sidebar_hidden else ""
        strike = "text-decoration:line-through;" if self.state == EXCLUDE else ""
        self.setStyleSheet(
            f"QPushButton{{color:{color};background:{bg};border:1px solid {border};border-radius:4px;"
            f"text-align:left;padding:1px 4px;font-size:12px;{italic}{strike}}}"
            f"QPushButton:hover{{background:#2D2D3F;}}"
        )


def ordered_tags(tags: list[Tag]) -> list[tuple[Tag, int]]:
    """
    Las etiquetas de UNA categoría en orden de árbol: cada padre seguido de sus
    hijas (con su profundidad). Una hija cuyo padre está en otra categoría
    (o escondido) va como raíz.
    """
    ids = {t.id for t in tags}
    children: dict[int, list[Tag]] = {}
    roots: list[Tag] = []
    for t in sorted(tags, key=lambda t: t.name):
        if t.parent_id in ids and t.parent_id != t.id:
            children.setdefault(t.parent_id, []).append(t)
        else:
            roots.append(t)
    out: list[tuple[Tag, int]] = []
    seen: set[int] = set()

    def walk(t: Tag, depth: int) -> None:
        if t.id in seen:
            return
        seen.add(t.id)
        out.append((t, depth))
        for c in children.get(t.id, []):
            walk(c, depth + 1)

    for r in roots:
        walk(r, 0)
    for t in tags:  # ciclos (no deberían existir): que no desaparezcan
        if t.id not in seen:
            out.append((t, 0))
    return out


class TagFilterPanel(QWidget):
    """Etiquetas agrupadas por categoría con su n.º de fotos."""

    filter_changed = pyqtSignal()
    tags_changed = pyqtSignal()  # se escondió/mostró una etiqueta del sidebar

    def __init__(self, parent=None):
        super().__init__(parent)
        self._included: list[int] = []
        self._excluded: list[int] = []
        self._show_hidden = False
        self._rows: list[tuple[QWidget, TagFilterButton, str]] = []  # (fila, botón, texto de búsqueda)
        self._groups: list[tuple[QPushButton, QWidget]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(4)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Buscar etiqueta…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_search)
        lay.addWidget(self.search_edit)

        mode_row = QHBoxLayout()
        mode_lbl = QLabel("Incluidas:")
        mode_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        mode_row.addWidget(mode_lbl)
        self.match_combo = QComboBox()
        self.match_combo.addItem("todas (Y)", False)
        self.match_combo.addItem("alguna (O)", True)
        self.match_combo.setToolTip(
            "Con varias etiquetas incluidas: la foto debe tener todas, o basta con una"
        )
        self.match_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.match_combo, stretch=1)
        lay.addLayout(mode_row)

        self.scroll_box = QScrollArea()
        self.scroll_box.setWidgetResizable(True)
        self.scroll_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.tag_vbox = QVBoxLayout(inner)
        self.tag_vbox.setContentsMargins(0, 0, 0, 0)
        self.tag_vbox.setSpacing(1)
        self.scroll_box.setWidget(inner)
        lay.addWidget(self.scroll_box, stretch=1)

        # Mostrar/ocultar las etiquetas escondidas del sidebar (para poder restaurarlas)
        self.btn_show_hidden = QPushButton("")
        self.btn_show_hidden.setCheckable(True)
        self.btn_show_hidden.setStyleSheet(
            "QPushButton{color:#8888AA;font-size:11px;padding:3px 8px;}"
            "QPushButton:checked{color:#FFD700;border-color:#FFD700;}"
        )
        self.btn_show_hidden.toggled.connect(self._on_show_hidden_toggled)
        lay.addWidget(self.btn_show_hidden)

    # ── Estado del filtro ─────────────────────────────────────────────────────

    def included_ids(self) -> list[int]:
        return list(self._included)

    def excluded_ids(self) -> list[int]:
        return list(self._excluded)

    def match_any(self) -> bool:
        return bool(self.match_combo.currentData())

    def set_filter(self, included: list[int], excluded: list[int], match_any: bool) -> None:
        """Sin emitir filter_changed (quien llama recarga). Lo usan las búsquedas guardadas."""
        self._included = list(included)
        self._excluded = [t for t in excluded if t not in included]
        self.match_combo.blockSignals(True)
        self.match_combo.setCurrentIndex(1 if match_any else 0)
        self.match_combo.blockSignals(False)
        self._sync_buttons()

    def clear(self) -> None:
        self.set_filter([], [], self.match_any())

    def buttons(self) -> list[TagFilterButton]:
        return [b for _row, b, _text in self._rows]

    def _sync_buttons(self) -> None:
        for b in self.buttons():
            b.set_state(
                INCLUDE if b.tag.id in self._included else EXCLUDE if b.tag.id in self._excluded else NONE
            )

    def _on_button_changed(self) -> None:
        self._included = [b.tag.id for b in self.buttons() if b.state == INCLUDE]
        self._excluded = [b.tag.id for b in self.buttons() if b.state == EXCLUDE]
        self.filter_changed.emit()

    def _on_mode_changed(self) -> None:
        if len(self._included) > 1:
            self.filter_changed.emit()

    # ── Lista ─────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        clear_layout(self.tag_vbox)
        self._rows.clear()
        self._groups.clear()

        n_hidden = services.count_sidebar_hidden_tags()
        self.btn_show_hidden.setVisible(n_hidden > 0 or self._show_hidden)
        self.btn_show_hidden.blockSignals(True)
        self.btn_show_hidden.setChecked(self._show_hidden)
        self.btn_show_hidden.blockSignals(False)
        self.btn_show_hidden.setText(
            f"🚫 Ocultar escondidas ({n_hidden})"
            if self._show_hidden
            else f"👁 Mostrar escondidas ({n_hidden})"
        )

        counts = services.get_tag_photo_counts()
        aliases = services.get_tag_aliases()
        paths = services.tag_path_names()
        groups = services.get_sidebar_tags(include_hidden=self._show_hidden)
        existing: set[int] = set()
        for category, tags in groups.items():
            header = QPushButton(f"▾  {category.upper()}")
            header.setCheckable(True)
            header.setChecked(True)
            header.setStyleSheet("""
                QPushButton { background:#1A1A2E;color:#6688AA;border:none;
                    border-top:1px solid #2D2D3F;border-radius:0;
                    text-align:left;padding:4px 6px;font-size:10px;
                    font-weight:bold;letter-spacing:1px; }
                QPushButton:hover { color:#88AACC;background:#1E1E2E; }
            """)
            group_widget = QWidget()
            gv = QVBoxLayout(group_widget)
            gv.setContentsMargins(4, 0, 0, 4)
            gv.setSpacing(1)
            cat_ids = {t.id for t in tags}
            for tag, depth in ordered_tags(tags):
                existing.add(tag.id)
                path = paths.get(tag.id, [tag.name])
                # Padre en otra categoría: se muestra la ruta para que se entienda
                label = (
                    " › ".join(path)
                    if depth == 0 and tag.parent_id not in cat_ids and len(path) > 1
                    else tag.name
                )
                row = QWidget()
                hl = QHBoxLayout(row)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(2)
                btn = TagFilterButton(tag, label, depth)
                btn.state_changed.connect(self._on_button_changed)
                hl.addWidget(btn, stretch=1)
                count = QLabel(f"{counts.get(tag.id, 0):,}")
                count.setStyleSheet("color:#666;font-size:10px;")
                hl.addWidget(count)
                eye = QPushButton("👁" if not tag.sidebar_hidden else "🚫")
                eye.setToolTip(
                    "Volver a mostrar en el sidebar" if tag.sidebar_hidden else "Esconder del sidebar"
                )
                eye.setFixedSize(22, 22)
                eye.setStyleSheet(
                    "QPushButton{background:transparent;border:none;font-size:11px;padding:0;}"
                    "QPushButton:hover{background:#2D2D3F;border-radius:4px;}"
                )
                eye.clicked.connect(
                    lambda _, tid=tag.id, cur=tag.sidebar_hidden: self._toggle_sidebar_hidden(tid, cur)
                )
                hl.addWidget(eye)
                gv.addWidget(row)
                search_text = " ".join([*path, *aliases.get(tag.id, [])]).lower()
                self._rows.append((row, btn, search_text))
            header.toggled.connect(
                lambda checked, gw=group_widget, b=header: self._toggle_group(gw, b, checked)
            )
            self.tag_vbox.addWidget(header)
            self.tag_vbox.addWidget(group_widget)
            self._groups.append((header, group_widget))
        self.tag_vbox.addStretch()
        # Una etiqueta activa que se borró o se escondió deja de filtrar
        self._included = [t for t in self._included if t in existing]
        self._excluded = [t for t in self._excluded if t in existing]
        self._sync_buttons()
        self._apply_search(self.search_edit.text())

    def _apply_search(self, text: str) -> None:
        text = text.strip().lower()
        for row, btn, search_text in self._rows:
            # Las activas siempre se ven, para poder quitarlas
            row.setVisible(not text or text in search_text or btn.state != NONE)
        for header, group in self._groups:
            any_visible = any(not w.isHidden() for w in group.findChildren(QWidget) if w.parent() is group)
            header.setVisible(any_visible)
            group.setVisible(any_visible and header.isChecked())

    @staticmethod
    def _toggle_group(group: QWidget, header: QPushButton, checked: bool) -> None:
        group.setVisible(checked)
        header.setText(f"{'▾' if checked else '▸'}  {header.text()[3:]}")

    def _toggle_sidebar_hidden(self, tag_id: int, currently_hidden: bool) -> None:
        services.set_tag_sidebar_hidden(tag_id, not currently_hidden)
        if currently_hidden and services.count_sidebar_hidden_tags() == 0:
            self._show_hidden = False
        self.refresh()
        self.tags_changed.emit()

    def _on_show_hidden_toggled(self, checked: bool) -> None:
        self._show_hidden = checked
        self.refresh()
