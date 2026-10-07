"""
PhotoVault - ui/sidebar.py
Paneles del sidebar: árbol de carpetas, línea de tiempo y búsquedas guardadas
(el filtro por etiquetas está en ui/tag_panel.py).
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import services
from ui.gallery import MONTHS

logger = logging.getLogger(__name__)

_TREE_STYLE = (
    "QTreeWidget{background:transparent;border:none;color:#D0D0E8;font-size:12px;}"
    "QTreeWidget::item{padding:2px 0;}"
    "QTreeWidget::item:selected{background:#4A9EFF33;color:#FFFFFF;}"
    "QTreeWidget::item:hover{background:#1E1E2E;}"
)

ItemDataRole = Qt.ItemDataRole.UserRole


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


# ─── Búsquedas guardadas ──────────────────────────────────────────────────────


class SavedSearchPanel(QWidget):
    """
    Álbumes inteligentes (fijos) y búsquedas guardadas por el usuario. Son
    consultas, no listas de fotos: siempre muestran lo que coincide ahora.
    """

    search_selected = pyqtSignal(object)  # services.GalleryQuery
    save_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        self.btn_save = QPushButton("💾  Guardar la búsqueda actual…")
        self.btn_save.setToolTip("Guarda filtros, etiquetas, carpeta, texto y orden con un nombre")
        self.btn_save.clicked.connect(self.save_requested)
        lay.addWidget(self.btn_save)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(_TREE_STYLE)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.tree)
        self._dirty = True

    def mark_dirty(self) -> None:
        self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    def refresh(self) -> None:
        """Recalcula los contadores (una consulta por álbum: solo con la pestaña visible)."""
        self._dirty = False
        self.tree.clear()
        albums = QTreeWidgetItem(["ÁLBUMES"])
        albums.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(albums)
        for name, q in services.BUILTIN_ALBUMS:
            item = QTreeWidgetItem([f"{name}  ({services.count_gallery(q):,})"])
            item.setData(0, ItemDataRole, ("builtin", q))
            albums.addChild(item)
        mine = QTreeWidgetItem(["MIS BÚSQUEDAS"])
        mine.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(mine)
        saved = services.list_saved_searches()
        for s in saved:
            q = services.GalleryQuery.from_dict(s.query)
            item = QTreeWidgetItem([f"{s.name}  ({services.count_gallery(q):,})"])
            item.setData(0, ItemDataRole, ("saved", s.id, s.name, q))
            item.setToolTip(0, "Clic derecho: renombrar o eliminar")
            mine.addChild(item)
        if not saved:
            hint = QTreeWidgetItem(["(ninguna todavía)"])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            mine.addChild(hint)
        self.tree.expandAll()

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, ItemDataRole)
        if data:
            self.search_selected.emit(data[-1])

    def _show_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        data = item.data(0, ItemDataRole) if item is not None else None
        if not data or data[0] != "saved":
            return
        _kind, sid, name, _q = data
        menu = QMenu(self)
        menu.addAction("✎  Renombrar…", lambda: self._rename(sid, name))
        menu.addAction("🗑  Eliminar", lambda: self._delete(sid, name))
        viewport = self.tree.viewport()
        if viewport is not None:
            menu.exec(viewport.mapToGlobal(pos))

    def _rename(self, sid: int, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Renombrar búsqueda", "Nombre:", text=name)
        if ok and new.strip() and new.strip() != name:
            try:
                services.rename_saved_search(sid, new)
            except ValueError as e:
                QMessageBox.warning(self, "No se pudo renombrar", str(e))
                return
            self.refresh()

    def _delete(self, sid: int, name: str) -> None:
        if (
            QMessageBox.question(
                self,
                "Eliminar búsqueda",
                f"¿Eliminar la búsqueda guardada «{name}»?\n\nNo se borra ninguna foto ni etiqueta.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            services.delete_saved_search(sid)
            self.refresh()
