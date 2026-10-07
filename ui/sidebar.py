"""
PhotoVault - ui/sidebar.py
Paneles del sidebar: filtro por etiquetas, árbol de carpetas y línea de tiempo.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import services
from ui.gallery import MONTHS
from ui.widgets import clear_layout, layout_widgets

logger = logging.getLogger(__name__)

_TREE_STYLE = (
    "QTreeWidget{background:transparent;border:none;color:#D0D0E8;font-size:12px;}"
    "QTreeWidget::item{padding:2px 0;}"
    "QTreeWidget::item:selected{background:#4A9EFF33;color:#FFFFFF;}"
    "QTreeWidget::item:hover{background:#1E1E2E;}"
)

ItemDataRole = Qt.ItemDataRole.UserRole


# ─── Etiquetas ────────────────────────────────────────────────────────────────


class TagFilterPanel(QWidget):
    """Checkboxes de etiquetas agrupadas por categoría (filtro AND)."""

    filter_changed = pyqtSignal()
    tags_changed = pyqtSignal()  # se escondió/mostró una etiqueta del sidebar

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active: list[int] = []
        self._show_hidden = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(4)
        self.scroll_box = QScrollArea()
        self.scroll_box.setWidgetResizable(True)
        self.scroll_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.tag_vbox = QVBoxLayout(inner)
        self.tag_vbox.setContentsMargins(0, 0, 0, 0)
        self.tag_vbox.setSpacing(2)
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

    def active_tag_ids(self) -> list[int]:
        return list(self._active)

    def refresh(self) -> None:
        clear_layout(self.tag_vbox)

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
            gv.setContentsMargins(8, 0, 0, 4)
            gv.setSpacing(1)
            for tag in tags:
                existing.add(tag.id)
                row = QWidget()
                hl = QHBoxLayout(row)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)
                chk = QCheckBox(tag.name)
                # Las escondidas se ven en cursiva mientras se muestran
                italic = "font-style:italic;" if tag.sidebar_hidden else ""
                chk.setStyleSheet(f"color:{tag.color};{italic}")
                chk.setProperty("tag_id", tag.id)
                if tag.id in self._active:
                    chk.setChecked(True)
                chk.stateChanged.connect(self._on_checkbox_changed)
                hl.addWidget(chk, stretch=1)
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
            header.toggled.connect(
                lambda checked, gw=group_widget, btn=header: self._toggle_group(gw, btn, checked)
            )
            self.tag_vbox.addWidget(header)
            self.tag_vbox.addWidget(group_widget)
        self.tag_vbox.addStretch()
        # Un tag activo que se borró o se escondió deja de filtrar
        self._active = [t for t in self._active if t in existing]

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

    def checkboxes(self) -> list[QCheckBox]:
        return [
            chk
            for w in layout_widgets(self.tag_vbox)
            for chk in w.findChildren(QCheckBox)
            if chk.property("tag_id") is not None
        ]

    def _on_checkbox_changed(self) -> None:
        self._active = [chk.property("tag_id") for chk in self.checkboxes() if chk.isChecked()]
        self.filter_changed.emit()

    def clear(self) -> None:
        """Desmarca todo sin emitir filter_changed (quien llama recarga)."""
        self._active = []
        for chk in self.checkboxes():
            chk.blockSignals(True)
            chk.setChecked(False)
            chk.blockSignals(False)


# ─── Carpetas ─────────────────────────────────────────────────────────────────


class FolderTreePanel(QWidget):
    """Árbol de carpetas indexadas con el n.º de fotos (incluidas subcarpetas)."""

    folder_selected = pyqtSignal(object)  # str | None (None = todas)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.tree)

    def refresh(self, selected: str | None = None) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        roots = services.get_folder_tree()
        all_item = QTreeWidgetItem([f"Todas las carpetas  ({sum(r.total for r in roots):,})"])
        all_item.setData(0, ItemDataRole, None)
        self.tree.addTopLevelItem(all_item)
        for root in roots:
            self.tree.addTopLevelItem(self._make_item(root))
        # Abrir los niveles que no se ramifican (G:\ → Fotos → …)
        level = [self.tree.topLevelItem(i) for i in range(1, self.tree.topLevelItemCount())]
        while len(level) == 1 and level[0] is not None:
            level[0].setExpanded(True)
            level = [level[0].child(i) for i in range(level[0].childCount())]
        self.select_folder(selected)
        self.tree.blockSignals(False)

    def _make_item(self, node: services.FolderNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"{node.name}  ({node.total:,})"])
        item.setData(0, ItemDataRole, node.path)
        item.setToolTip(0, f"{node.path}\n{node.total:,} fotos ({node.count:,} directamente aquí)")
        for child in node.children:
            item.addChild(self._make_item(child))
        return item

    def select_folder(self, folder: str | None) -> None:
        def walk(item: QTreeWidgetItem):
            yield item
            for i in range(item.childCount()):
                child = item.child(i)
                if child is not None:
                    yield from walk(child)

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for item in walk(top) if top is not None else []:
                if item.data(0, ItemDataRole) == folder:
                    self.tree.setCurrentItem(item)
                    parent = item.parent()
                    while parent is not None:
                        parent.setExpanded(True)
                        parent = parent.parent()
                    return
        self.tree.clearSelection()

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        self.folder_selected.emit(item.data(0, ItemDataRole))


# ─── Línea de tiempo ──────────────────────────────────────────────────────────


class TimelinePanel(QWidget):
    """Años y meses con fotos (según los filtros actuales); clic = saltar ahí."""

    date_selected = pyqtSignal(object, object)  # año | None, mes | None

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.tree)
        self._dirty = True

    def mark_dirty(self) -> None:
        self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    def refresh(self, q: services.GalleryQuery) -> None:
        self._dirty = False
        expanded = {
            item.data(0, ItemDataRole)[0]
            for i in range(self.tree.topLevelItemCount())
            if (item := self.tree.topLevelItem(i)) is not None and item.isExpanded()
        }
        self.tree.clear()
        years: dict[int | None, QTreeWidgetItem] = {}
        buckets = services.get_date_histogram(q)
        totals: dict[int | None, int] = {}
        for b in buckets:
            totals[b.year] = totals.get(b.year, 0) + b.count
        for b in buckets:
            year_item = years.get(b.year)
            if year_item is None:
                label = str(b.year) if b.year is not None else "Sin fecha"
                year_item = QTreeWidgetItem([f"{label}  ({totals[b.year]:,})"])
                year_item.setData(0, ItemDataRole, (b.year, None))
                self.tree.addTopLevelItem(year_item)
                years[b.year] = year_item
                year_item.setExpanded(b.year in expanded)
            if b.year is not None:
                month = MONTHS[b.month - 1].capitalize() if b.month and 1 <= b.month <= 12 else "Sin mes"
                child = QTreeWidgetItem([f"{month}  ({b.count:,})"])
                child.setData(0, ItemDataRole, (b.year, b.month or services.NO_MONTH))
                year_item.addChild(child)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        year, month = item.data(0, ItemDataRole)
        self.date_selected.emit(year, month)
