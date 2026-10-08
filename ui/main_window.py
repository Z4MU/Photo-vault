"""
PhotoVault - ui/main_window.py
Ventana principal: sidebar (etiquetas, carpetas, fechas, búsquedas), filtros,
galería continua y visor. Las acciones sobre la selección están en ui/gallery_actions.py
y las tareas en segundo plano en ui/background.py. Recuerda la sesión y vigila carpetas.
Contenido oculto, bloqueo y modo pánico: ui/privacy_actions.py.
"""

import logging
from functools import partial
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QPixmapCache, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import database as db
import privacy
import services
from models import SortField, SortOrder
from ui.background import BackgroundTasksMixin
from ui.dialogs.duplicates import DuplicatesDialog
from ui.dialogs.folders import DeindexDialog, IndexDialog
from ui.dialogs.quick_tag import QuickTagWindow
from ui.dialogs.quick_tag_setup import QuickTagSetupDialog
from ui.dialogs.review import GridReviewWindow, ReviewWindow
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.stats import StatsDialog
from ui.dialogs.tags import TagManagerDialog
from ui.filter_bar import FilterBar
from ui.folder_watch import FolderWatcher
from ui.gallery import GalleryDelegate, GalleryModel, GalleryView, format_date, thumb_source_size
from ui.gallery_actions import GalleryActionsMixin
from ui.privacy_actions import TOGGLE_HIDDEN_KEY, PrivacyMixin
from ui.sidebar import FolderTreePanel, SavedSearchPanel, TimelinePanel
from ui.style import DARK_STYLE
from ui.tag_panel import TagFilterPanel
from ui.toast import ToastManager
from ui.viewer import SHORTCUTS_HELP, ViewerWindow
from ui.welcome import EmptyState
from ui.widgets import TaskStatusWidget
from ui.workers import (
    StoppableThread,
    TaskWorker,
    disconnect_all,
    retire_on_destroy,
    retire_thread,
    thumbnail_queue,
    wait_all_threads,
)

logger = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 300
# Miniaturas ya mostradas en esta sesión, en memoria (volver atrás es instantáneo).
# 200 MB ≈ 1.200 miniaturas de 200×200 o ~250 de 480×480.
PIXMAP_CACHE_KB = 200 * 1024
THUMB_STEP = 20

GALLERY_SHORTCUTS_HELP = [
    ("Doble clic  ·  Enter", "Abrir en el visor"),
    ("Clic  ·  Ctrl+clic  ·  Shift+clic", "Seleccionar  ·  agregar  ·  rango"),
    ("Ctrl+A  ·  Esc", "Seleccionar todo  ·  quitar selección"),
    ("Ctrl+T", "Etiquetar la selección"),
    ("1 … 5  ·  0", "Valorar con estrellas  ·  quitar la valoración"),
    ("F", "Marcar / quitar favorita"),
    ("Ctrl+C  ·  Ctrl+Shift+C", "Copiar rutas  ·  copiar archivos"),
    ("Ctrl+E", "Mostrar en el Explorador"),
    ("Ctrl+F  ·  Ctrl+G", "Buscar (nombre o nota)  ·  ir a una posición"),
    ("Ctrl+Shift+F", "Mostrar / ocultar la barra de filtros"),
    ("Ctrl+S", "Guardar la búsqueda actual"),
    ("Ctrl + / Ctrl − / Ctrl+0", "Miniaturas más grandes / más chicas / normales"),
    ("F5", "Recargar"),
    ("Clic derecho", "Más acciones"),
    ("Arrastrar", "Copiar las fotos a otra app (hasta 500)"),
]


class MainWindow(QMainWindow, GalleryActionsMixin, BackgroundTasksMixin, PrivacyMixin):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoVault")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(DARK_STYLE)

        self._folder: str | None = None
        self._sort_field: SortField = SortField.DATE
        self._sort_order: SortOrder = SortOrder.DESC

        # La búsqueda espera a que se deje de escribir (antes: una consulta por tecla)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._on_search)

        self._thumb_save_timer = QTimer(self)
        self._thumb_save_timer.setSingleShot(True)
        self._thumb_save_timer.setInterval(800)
        self._thumb_save_timer.timeout.connect(self._save_thumb_size)

        QPixmapCache.setCacheLimit(PIXMAP_CACHE_KB)

        # Tarea en segundo plano (una a la vez): "index" o "thumbs"; las indexaciones esperan en cola
        self._bg_worker: StoppableThread | None = None
        self._bg_kind: str | None = None
        self._index_queue = []
        self._current_index = None
        self._pending_thumb_ids = []
        self._closing = False

        # Cola de miniaturas de la galería (vive lo que la ventana)
        self._thumb_queue = thumbnail_queue()
        self._thumb_queue.start()

        # db.init_db() corre antes, en _startup()
        self._build_ui()
        self._build_shortcuts()
        self.toasts = ToastManager(self, bottom_offset=34)
        self.watcher = FolderWatcher(self)
        self._watch_worker: TaskWorker | None = None
        self._pending_watch_startup: bool | None = None
        self.watcher.changed.connect(lambda root: self.start_indexing(root, auto=True))
        self._init_privacy(self.status_bar)
        self.tag_panel.refresh()
        self.folder_panel.refresh()
        self._update_stats()
        self._session = services.get_session() if services.is_restore_session_enabled() else {}
        self._restore_geometry()
        QTimer.singleShot(0, self._initial_load)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        main_area = QWidget()
        ml = QVBoxLayout(main_area)
        ml.setContentsMargins(12, 12, 12, 6)
        ml.setSpacing(8)
        ml.addLayout(self._build_top_bar())

        self.model = GalleryModel(self._thumb_queue, self)
        self.delegate = GalleryDelegate(self)
        self.view = GalleryView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.open_requested.connect(self.open_viewer)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.view.drag_refused.connect(self._on_drag_refused)
        sm = self.view.selectionModel()
        if sm is not None:
            sm.selectionChanged.connect(self._on_selection_changed)
        self.view.vbar().valueChanged.connect(self._update_position_label)
        # Sin fotos que mostrar: bienvenida (colección vacía) o "sin resultados"
        self.empty = EmptyState()
        self.empty.index_requested.connect(self._open_index_dialog)
        self.empty.clear_filters_requested.connect(self._clear_filters)
        self.empty.shortcuts_requested.connect(self.show_shortcuts)
        self.gallery_stack = QStackedWidget()
        self.gallery_stack.addWidget(self.view)
        self.gallery_stack.addWidget(self.empty)
        ml.addWidget(self.gallery_stack, stretch=1)
        self._set_thumb_size(services.get_thumb_display_size(), save=False)

        root.addWidget(main_area, stretch=1)

        # Barra de estado: progreso de indexación / miniaturas en segundo plano
        self.task_status = TaskStatusWidget()
        self.task_status.cancel_clicked.connect(self._cancel_background)
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet("QStatusBar{background:#13131F;border-top:1px solid #2D2D3F;}")
        self.status_bar.addPermanentWidget(self.task_status, 1)
        self.setStatusBar(self.status_bar)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(250)
        sidebar.setStyleSheet("background:#13131F;border-right:1px solid #2D2D3F;")
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(10, 16, 10, 12)
        sb.setSpacing(8)

        logo = QLabel("📸 PhotoVault")
        logo.setStyleSheet("font-size:18px;font-weight:bold;color:#4A9EFF;margin-bottom:8px;")
        sb.addWidget(logo)

        for label, slot in [
            ("＋ Indexar carpeta", self._open_index_dialog),
            ("🏷  Gestionar etiquetas", self._open_tag_manager),
            ("🗂  Carpetas", self._open_deindex_dialog),
            ("📊  Estadísticas", self._open_stats_dialog),
            ("🔍  Duplicados", self._open_duplicates_dialog),
            ("⚙  Configuración", self._open_settings_dialog),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            sb.addWidget(btn)

        # Botón de etiquetado rápido — destacado visualmente
        btn_qt = QPushButton("⚡  Etiquetado rápido")
        btn_qt.setStyleSheet(
            "QPushButton{background:#4A9EFF22;color:#4A9EFF;"
            "border:1px solid #4A9EFF;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#4A9EFF44;}"
        )
        btn_qt.clicked.connect(self._open_quick_tag)
        sb.addWidget(btn_qt)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        sb.addWidget(sep)

        self.tag_panel = TagFilterPanel()
        self.tag_panel.filter_changed.connect(self._apply_query)
        self.tag_panel.tags_changed.connect(self._update_stats)
        self.folder_panel = FolderTreePanel()
        self.folder_panel.folder_selected.connect(self.set_folder_filter)
        self.timeline_panel = TimelinePanel()
        self.timeline_panel.date_selected.connect(self.go_to_date)
        self.saved_panel = SavedSearchPanel()
        self.saved_panel.search_selected.connect(self.apply_gallery_query)
        self.saved_panel.save_requested.connect(self.save_current_search)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:none;}"
            "QTabBar::tab{background:#1E1E2E;color:#8888AA;padding:5px 6px;border:none;font-size:11px;"
            "border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px;}"
            "QTabBar::tab:selected{background:#2D2D3F;color:#D0D0E8;}"
        )
        for panel, text, tip in (
            (self.tag_panel, "🏷 Etiq.", "Filtrar por etiquetas"),
            (self.folder_panel, "📁 Carp.", "Árbol de carpetas"),
            (self.timeline_panel, "📅 Fechas", "Línea de tiempo"),
            (self.saved_panel, "⭐ Álbum", "Álbumes y búsquedas guardadas"),
        ):
            self.tabs.setTabToolTip(self.tabs.addTab(panel, text), tip)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        sb.addWidget(self.tabs, stretch=1)

        btn_clear = QPushButton("✕ Limpiar filtros")
        btn_clear.setStyleSheet("color:#FF4A4A;")
        btn_clear.clicked.connect(self._clear_filters)
        sb.addWidget(btn_clear)

        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#666;font-size:10px;")
        sb.addWidget(self.stats_lbl)
        return sidebar

    def _build_top_bar(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Buscar por nombre o nota…  (Ctrl+F)")
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_edit.returnPressed.connect(self._on_search)  # Enter: buscar ya
        top_bar.addWidget(self.search_edit, stretch=1)

        self.btn_filters = QPushButton("⚙ Filtros")
        self.btn_filters.setCheckable(True)
        self.btn_filters.setToolTip(
            "Tipo, años, orientación, resolución, duración, estrellas… (Ctrl+Shift+F)"
        )
        self.btn_filters.setStyleSheet("QPushButton:checked{color:#4A9EFF;border-color:#4A9EFF;}")
        self.btn_filters.toggled.connect(self._toggle_filter_bar)
        top_bar.addWidget(self.btn_filters)

        top_bar.addWidget(QLabel("Ordenar:"))
        self.sort_field_combo = QComboBox()
        for f in SortField:
            self.sort_field_combo.addItem(f.value, userData=f)
        self.sort_field_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_bar.addWidget(self.sort_field_combo)

        self.sort_order_combo = QComboBox()
        self.sort_order_combo.addItem("↓ desc", userData=SortOrder.DESC)
        self.sort_order_combo.addItem("↑ asc", userData=SortOrder.ASC)
        self.sort_order_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_bar.addWidget(self.sort_order_combo)

        size_lbl = QLabel("🔎")
        size_lbl.setToolTip("Tamaño de las miniaturas (Ctrl + / Ctrl −)")
        top_bar.addWidget(size_lbl)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(services.THUMB_DISPLAY_MIN, services.THUMB_DISPLAY_MAX)
        self.size_slider.setSingleStep(THUMB_STEP)
        self.size_slider.setPageStep(THUMB_STEP * 2)
        self.size_slider.setFixedWidth(110)
        self.size_slider.setToolTip("Tamaño de las miniaturas (Ctrl + / Ctrl −)")
        self.size_slider.valueChanged.connect(self._set_thumb_size)
        top_bar.addWidget(self.size_slider)

        self.btn_bulk_tag = QPushButton("🏷 Etiquetar selección")
        self.btn_bulk_tag.setToolTip("Agregar o quitar una etiqueta a las fotos seleccionadas (Ctrl+T)")
        self.btn_bulk_tag.setStyleSheet(
            "QPushButton{background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;}"
            "QPushButton:disabled{background:transparent;color:#555;border-color:#2D2D3F;}"
        )
        self.btn_bulk_tag.setEnabled(False)
        self.btn_bulk_tag.clicked.connect(self._open_bulk_tag)
        top_bar.addWidget(self.btn_bulk_tag)
        box.addLayout(top_bar)

        self.filter_bar = FilterBar()
        self.filter_bar.changed.connect(self._apply_query)
        self.filter_bar.setVisible(False)
        box.addWidget(self.filter_bar)

        info_bar = QHBoxLayout()
        self.count_lbl = QLabel("0 fotos")
        self.count_lbl.setStyleSheet("color:#8888AA;")
        info_bar.addWidget(self.count_lbl)
        self.folder_chip = QPushButton("")
        self.folder_chip.setToolTip("Quitar el filtro de carpeta")
        self.folder_chip.setStyleSheet(
            "QPushButton{background:#FFD70022;color:#FFD700;border:1px solid #FFD700;"
            "border-radius:10px;padding:1px 10px;font-size:11px;}"
        )
        self.folder_chip.clicked.connect(lambda: self.set_folder_filter(None))
        self.folder_chip.setVisible(False)
        info_bar.addWidget(self.folder_chip)
        info_bar.addStretch()
        self.position_lbl = QLabel("")
        self.position_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        info_bar.addWidget(self.position_lbl)
        btn_help = QPushButton("⌨")
        btn_help.setToolTip("Atajos de teclado (F1)")
        btn_help.setFixedWidth(32)
        btn_help.clicked.connect(self.show_shortcuts)
        info_bar.addWidget(btn_help)
        box.addLayout(info_bar)
        return box

    def _build_shortcuts(self) -> None:
        window_keys = {
            "Ctrl+F": self._focus_search,
            "Ctrl+G": self._go_to_position,
            "F5": self.reload_keep_position,
            "F1": self.show_shortcuts,
            "Ctrl++": lambda: self._step_thumb_size(1),
            "Ctrl+=": lambda: self._step_thumb_size(1),
            "Ctrl+-": lambda: self._step_thumb_size(-1),
            "Ctrl+0": lambda: self.size_slider.setValue(services.THUMB_DISPLAY_DEFAULT),
            "Ctrl+Shift+F": self.btn_filters.toggle,
            "Ctrl+S": self.save_current_search,
            TOGGLE_HIDDEN_KEY: self.toggle_hidden_content,
        }
        for key, slot in window_keys.items():
            QShortcut(QKeySequence(key), self, slot)
        # Estas solo con la galería enfocada (Ctrl+C en el buscador copia texto)
        gallery_keys = {
            "Ctrl+T": self._open_bulk_tag,
            "Ctrl+C": lambda: self._copy_selection(as_files=False),
            "Ctrl+Shift+C": lambda: self._copy_selection(as_files=True),
            "Ctrl+E": self._reveal_current,
            "F": self.toggle_favorite_selection,
        }
        for r in range(6):
            gallery_keys[str(r)] = partial(self.set_rating_selection, r)
        for key, slot in gallery_keys.items():
            QShortcut(QKeySequence(key), self.view, slot, context=Qt.ShortcutContext.WidgetShortcut)

    # ── Consulta de la galería ────────────────────────────────────────────────

    def _query(self) -> services.GalleryQuery:
        return services.GalleryQuery(
            tag_ids=tuple(self.tag_panel.included_ids()),
            exclude_tag_ids=tuple(self.tag_panel.excluded_ids()),
            match_any=self.tag_panel.match_any(),
            search=self.search_edit.text().strip() or None,
            folder=self._folder,
            sort_field=self._sort_field,
            sort_order=self._sort_order,
            **self.filter_bar.values(),
        )

    def apply_gallery_query(self, q: services.GalleryQuery) -> None:
        """Muestra una consulta completa (búsqueda guardada o álbum): pone cada control como corresponde."""
        self.tag_panel.set_filter(list(q.tag_ids), list(q.exclude_tag_ids), q.match_any)
        self.filter_bar.set_values(q)
        if q.attribute_filter_count():
            self.btn_filters.setChecked(True)
        self.search_edit.blockSignals(True)
        self.search_edit.setText(q.search or "")
        self.search_edit.blockSignals(False)
        for combo, value in ((self.sort_field_combo, q.sort_field), (self.sort_order_combo, q.sort_order)):
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(value))
            combo.blockSignals(False)
        self._sort_field, self._sort_order = q.sort_field, q.sort_order
        self.set_folder_filter(q.folder)  # aplica la consulta

    def save_current_search(self) -> None:
        q = self._query()
        name, ok = QInputDialog.getText(self, "Guardar búsqueda", "Nombre de la búsqueda:")
        name = name.strip()
        if not ok or not name:
            return
        if services.saved_search_exists(name) and (
            QMessageBox.question(
                self,
                "Ya existe",
                f"Ya hay una búsqueda llamada «{name}». ¿Reemplazarla?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        services.save_search(name, q)
        self.saved_panel.refresh()
        self.toast(f"💾 Búsqueda «{name}» guardada (pestaña ⭐ Álbum)", "success")

    def _toggle_filter_bar(self, visible: bool) -> None:
        self.filter_bar.setVisible(visible)

    def _update_filter_button(self) -> None:
        n = self.model.query().attribute_filter_count()
        self.btn_filters.setText(f"⚙ Filtros ({n})" if n else "⚙ Filtros")

    def _apply_query(self) -> None:
        """Nueva consulta (filtros/orden cambiaron): vuelve al principio."""
        self._search_timer.stop()
        self.model.set_query(self._query())
        self.view.scrollToTop()
        self._after_model_reset()

    def reload_keep_position(self) -> None:
        """Recarga la misma consulta (cambiaron etiquetas, se indexó…) sin perder el lugar."""
        self.saved_panel.mark_dirty()
        scroll = self.view.vbar().value()
        current = self.view.currentIndex().row()
        self.model.set_query(self._query())
        if 0 <= current < self.model.count():
            self.view.setCurrentIndex(self.model.index(current, 0))
        self.view.vbar().setValue(scroll)
        self._after_model_reset()

    def _after_model_reset(self) -> None:
        if self.model.count():
            self.gallery_stack.setCurrentWidget(self.view)
        else:
            if services.get_totals()[0] == 0:
                self.empty.show_welcome()
            else:
                self.empty.show_no_results()
            self.gallery_stack.setCurrentWidget(self.empty)
        self._update_count()
        self._update_position_label()
        self._update_filter_button()
        self.timeline_panel.mark_dirty()
        if self.tabs.currentWidget() is self.timeline_panel:
            self.timeline_panel.refresh(self.model.query())

    def _update_count(self) -> None:
        n_sel = self.view.selected_count()
        txt = f"{self.model.count():,} fotos"
        if n_sel:
            txt += f"  ·  {n_sel:,} seleccionadas"
        self.count_lbl.setText(txt)

    def _update_position_label(self, *_args) -> None:
        row = self.view.first_visible_row()
        photo = self.model.photo_at(row) if row is not None else None
        if photo is None:
            self.position_lbl.setText("")
            return
        where = f"{(row or 0) + 1:,} / {self.model.count():,}"
        if self._sort_field == SortField.DATE:
            where = f"{format_date(photo.year, photo.month).capitalize()}  ·  {where}"
        self.position_lbl.setText(where)

    def _on_tab_changed(self, _index: int) -> None:
        if self.tabs.currentWidget() is self.timeline_panel and self.timeline_panel.is_dirty():
            self.timeline_panel.refresh(self.model.query())
        if self.tabs.currentWidget() is self.saved_panel and self.saved_panel.is_dirty():
            self.saved_panel.refresh()

    # ── Filtros ───────────────────────────────────────────────────────────────

    def set_folder_filter(self, folder: str | None) -> None:
        self._folder = folder or None
        if self._folder:
            name = Path(self._folder).name or self._folder
            self.folder_chip.setText(f"📁 {name}  ✕")
            self.folder_chip.setToolTip(f"{self._folder}\n(clic para quitar el filtro de carpeta)")
        self.folder_chip.setVisible(self._folder is not None)
        self.folder_panel.select_folder(self._folder)
        self._apply_query()

    def _clear_filters(self) -> None:
        self.tag_panel.clear()
        self.filter_bar.clear()
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self.set_folder_filter(None)

    def _on_search(self) -> None:
        self._apply_query()

    def _focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _on_sort_changed(self) -> None:
        self._sort_field = self.sort_field_combo.currentData()
        self._sort_order = self.sort_order_combo.currentData()
        self._apply_query()

    # ── Saltar (línea de tiempo / posición) ───────────────────────────────────

    def go_to_date(self, year: int | None, month: int | None) -> None:
        if self._sort_field != SortField.DATE:
            # Saltar a una fecha solo tiene sentido ordenando por fecha
            self.sort_field_combo.blockSignals(True)
            self.sort_field_combo.setCurrentIndex(self.sort_field_combo.findData(SortField.DATE))
            self.sort_field_combo.blockSignals(False)
            self._sort_field = SortField.DATE
            self._apply_query()
        row = services.gallery_row_of_date(self.model.query(), year, month)
        if row < self.model.count():
            self.view.scroll_to_row(row)
            self.view.select_row(row)
            self.view.scroll_to_row(row)

    def _go_to_position(self) -> None:
        total = self.model.count()
        if not total:
            return
        current = (self.view.first_visible_row() or 0) + 1
        n, ok = QInputDialog.getInt(self, "Ir a", f"Foto n.º (1 – {total:,}):", current, 1, total)
        if ok:
            self.view.scroll_to_row(n - 1)
            self.view.select_row(n - 1)
            self.view.scroll_to_row(n - 1)

    # ── Tamaño de miniaturas ──────────────────────────────────────────────────

    def _set_thumb_size(self, size: int, save: bool = True) -> None:
        size = max(services.THUMB_DISPLAY_MIN, min(services.THUMB_DISPLAY_MAX, int(size)))
        if self.size_slider.value() != size:
            self.size_slider.blockSignals(True)
            self.size_slider.setValue(size)
            self.size_slider.blockSignals(False)
        first = self.view.first_visible_row()
        self.delegate.set_thumb_size(size)
        self.model.set_thumb_size(thumb_source_size(size))
        self.view.apply_cell_size(self.delegate.cell_size())
        if first is not None:
            self.view.scroll_to_row(first)
        if save:
            self._thumb_save_timer.start()

    def _step_thumb_size(self, direction: int) -> None:
        self.size_slider.setValue(self.size_slider.value() + direction * THUMB_STEP)

    def _save_thumb_size(self) -> None:
        services.set_thumb_display_size(self.size_slider.value())

    # ── Visor ─────────────────────────────────────────────────────────────────

    def open_viewer(self, row: int) -> None:
        if not 0 <= row < self.model.count():
            return
        viewer = ViewerWindow(self.model, row, self)
        viewer.exec()
        if viewer.tags_were_changed:
            self._refresh_after_tag_change()
        self.view.select_row(viewer.current_row)

    def _refresh_after_tag_change(self) -> None:
        self.tag_panel.refresh()
        self._update_stats()
        self.reload_keep_position()

    def show_shortcuts(self) -> None:
        def table(rows: list[tuple[str, str]]) -> str:
            cells = "".join(
                f"<tr><td style='color:#4A9EFF;padding-right:14px;'>{k}</td><td>{v}</td></tr>"
                for k, v in rows
            )
            return f"<table cellspacing='3'>{cells}</table>"

        box = QMessageBox(self)
        box.setWindowTitle("Atajos de teclado")
        box.setTextFormat(Qt.TextFormat.RichText)
        panic_key = privacy.get_options().panic_key
        privacy_rows = [(TOGGLE_HIDDEN_KEY, "Mostrar / bloquear el contenido oculto")]
        if panic_key:
            privacy_rows.append((panic_key, "Modo pánico (desde cualquier ventana)"))
        box.setText(
            "<b>Galería</b>"
            + table(GALLERY_SHORTCUTS_HELP)
            + "<br><b>Visor</b>"
            + table(SHORTCUTS_HELP)
            + "<br><b>Privacidad</b>"
            + table(privacy_rows)
        )
        box.setStyleSheet(DARK_STYLE)
        box.exec()

    # ── Diálogos ──────────────────────────────────────────────────────────────

    def _open_index_dialog(self):
        dlg = IndexDialog(self, busy=self._bg_kind == "index")
        dlg.start_requested.connect(self.start_indexing)
        dlg.exec()

    def _reload_all(self) -> None:
        """Después de cambios grandes (indexar, des-indexar, reubicar…)."""
        self.tag_panel.refresh()
        self.folder_panel.refresh(self._folder)
        self._update_stats()
        self.reload_keep_position()

    def _open_tag_manager(self):
        before = self._privacy_state()
        TagManagerDialog(self).exec()
        if self._privacy_state() != before:
            self.privacy_changed()
        else:
            self._refresh_after_tag_change()

    def _open_settings_dialog(self):
        before = self._privacy_state()
        SettingsDialog(self).exec()
        if self._privacy_state() != before:
            self.privacy_changed()

    @staticmethod
    def _privacy_state() -> tuple[bool, bool]:
        return privacy.has_pin(), privacy.is_unlocked()

    def _open_deindex_dialog(self):
        DeindexDialog(self).exec()
        self._reload_all()

    def _open_stats_dialog(self):
        StatsDialog(self).exec()

    def _open_duplicates_dialog(self):
        DuplicatesDialog(self).exec()
        self._reload_all()

    def _update_stats(self):
        photos, tags = services.get_totals()
        self.stats_lbl.setText(f"{photos:,} fotos  •  {tags} etiquetas")

    def _open_quick_tag(self):
        setup = QuickTagSetupDialog(self._query(), self)
        if setup.exec() != QDialog.DialogCode.Accepted:
            return
        photos = services.photo_list(setup.query)
        if not photos.count():
            QMessageBox.information(self, "Sin fotos", "No hay fotos para etiquetar con esa configuración.")
            return
        tag = services.get_tag(setup.tag_id) if setup.tag_id is not None else None
        win: QDialog
        if setup.mode == "multi" or tag is None:
            win = QuickTagWindow(photos, setup.query, self)
        elif setup.mode == "grid":
            win = GridReviewWindow(tag, photos, setup.grid_size, self)
        else:
            win = ReviewWindow(tag, photos, self)
        win.setWindowState(Qt.WindowState.WindowMaximized)
        win.exec()
        if isinstance(win, (ReviewWindow, GridReviewWindow)) and (win.yes_count or win.no_count):
            self.toast(win.summary(), "success", ms=8000)
        self._refresh_after_tag_change()

    # ── Avisos, sesión y carpetas vigiladas (fase 8) ──────────────────────────

    def toast(self, text: str, kind: str = "info", ms: int = 4000) -> None:
        self.toasts.show(text, kind, ms)

    def _restore_geometry(self) -> None:
        raw = self._session.get("geometry")
        if isinstance(raw, str) and raw:
            self.restoreGeometry(QByteArray.fromBase64(raw.encode("ascii")))
        tab = self._session.get("tab")
        if isinstance(tab, int) and 0 <= tab < self.tabs.count():
            self.tabs.setCurrentIndex(tab)

    def _initial_load(self) -> None:
        """Primera carga: la consulta de la última sesión (si sigue mostrando algo) y su posición."""
        state = self._session
        q = services.restorable_query(state)
        if q is not None:
            self.apply_gallery_query(q)
            self.btn_filters.setChecked(
                bool(state.get("filters_visible")) or bool(q.attribute_filter_count())
            )
            row = state.get("row")
            if isinstance(row, int) and 0 < row < self.model.count():
                self.view.scroll_to_row(row)
                self._update_position_label()
        else:
            self._apply_query()
            if isinstance(state.get("query"), dict) and state["query"] != services.GalleryQuery().to_dict():
                self.toast(
                    "Los filtros de la última vez ya no muestran ninguna foto (¿disco desconectado?): "
                    "se abre con toda la colección.",
                    "warning",
                    ms=7000,
                )
        self.apply_watch_settings(startup=True)

    def session_state(self) -> dict:
        return {
            "geometry": bytes(self.saveGeometry().toBase64().data()).decode("ascii"),
            "query": self._query().to_dict(),
            "tab": self.tabs.currentIndex(),
            "filters_visible": self.btn_filters.isChecked(),
            "row": self.view.first_visible_row() or 0,
        }

    def apply_watch_settings(self, startup: bool = False) -> None:
        """
        Vigilar (o dejar de vigilar) según Configuración; al abrir, revisar las
        carpetas una vez. Ver qué carpetas existen va en un hilo: con un disco
        USB dormido cada comprobación espera a que arranque (9 s con la ventana
        congelada).
        """
        if self._watch_worker is not None:
            self._pending_watch_startup = self._pending_watch_startup or startup
            return
        w = TaskWorker(lambda progress_callback=None, should_stop=None: services.available_watched_folders())
        w.completed.connect(lambda folders, s=startup: self._on_watch_folders(folders, s))
        self._watch_worker = w
        w.start()
        retire_on_destroy(self, w)

    def _on_watch_folders(self, folders: list[str], startup: bool) -> None:
        retire_thread(self._watch_worker)
        self._watch_worker = None
        self.watcher.set_roots(folders if services.is_watch_live() else [])
        if startup and folders and services.is_watch_on_start():
            # Unos segundos después de abrir: que la galería aparezca primero
            QTimer.singleShot(3000, lambda: self.scan_watched_folders(folders))
        if self._pending_watch_startup is not None:
            again, self._pending_watch_startup = self._pending_watch_startup, None
            self.apply_watch_settings(startup=again)

    def scan_watched_folders(self, folders: list[str] | None = None) -> None:
        for folder in folders if folders is not None else services.available_watched_folders():
            self.start_indexing(folder, auto=True)

    def _after_auto_index(self) -> None:
        if services.is_watch_live():
            self.watcher.set_roots(services.available_watched_folders())  # subcarpetas nuevas

    def changeEvent(self, event):
        super().changeEvent(event)
        if event is not None:
            self._privacy_change_event(event)

    def closeEvent(self, event):
        if self._bg_kind == "index" and not self._panic_close:
            if (
                QMessageBox.question(
                    self,
                    "Indexación en curso",
                    "Hay una indexación en curso. ¿Cancelarla y salir?\n\nLo ya indexado se conserva.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                event.ignore()
                return
        self._closing = True
        self._shutdown_privacy()
        if self._thumb_save_timer.isActive():
            self._thumb_save_timer.stop()
            self._save_thumb_size()
        if services.is_restore_session_enabled():
            services.save_session(self.session_state())
        self.watcher.stop()
        if self._watch_worker is not None:
            disconnect_all(self._watch_worker.completed)
            retire_thread(self._watch_worker)
            self._watch_worker = None
        self._retire_background()
        disconnect_all(self._thumb_queue.loaded)
        retire_thread(self._thumb_queue)
        wait_all_threads()
        db.close_connection()
        super().closeEvent(event)
