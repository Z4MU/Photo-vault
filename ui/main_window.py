"""
PhotoVault - ui/main_window.py
Ventana principal: sidebar, galería paginada, filtros.
"""

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import database as db
import services
from models import GalleryPage, SortField, SortOrder
from ui.dialogs.duplicates import DuplicatesDialog
from ui.dialogs.folders import DeindexDialog, IndexDialog
from ui.dialogs.photo import BulkTagDialog, PhotoDetailDialog
from ui.dialogs.quick_tag import QuickTagSetupDialog, QuickTagWindow
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.stats import StatsDialog
from ui.dialogs.tags import TagManagerDialog
from ui.style import DARK_STYLE
from ui.widgets import PhotoThumbnail, clear_layout, layout_widgets
from ui.workers import (
    ThumbnailLoader,
    disconnect_all,
    retire_thread,
    wait_all_threads,
)

logger = logging.getLogger(__name__)


# ─── Ventana principal ────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoVault")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(DARK_STYLE)

        self._active_tags: list[int] = []
        self._offset: int = 0
        self._page_size: int = services.get_page_size()
        self._sort_field: SortField = SortField.DATE
        self._sort_order: SortOrder = SortOrder.DESC
        self._current_page: GalleryPage | None = None
        self._thumbnails: dict[int, PhotoThumbnail] = {}
        self._pending_photos: list = []
        self._grid_cols: int = 4
        self._loader: ThumbnailLoader | None = None
        self._select_mode: bool = False
        self._selected_ids: set[int] = set()
        self._show_sidebar_hidden: bool = False

        self._build_timer = QTimer(self)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._add_next_batch)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(400)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_grid_cols = 0

        # db.init_db() corre antes, en _startup()
        self._build_ui()
        self._refresh_tags()
        QTimer.singleShot(100, self._load_photos)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background:#13131F;border-right:1px solid #2D2D3F;")
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(10, 16, 10, 16)
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
        sb.addWidget(QLabel("Filtrar por etiqueta:"))

        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_widget = QWidget()
        self.tag_vbox = QVBoxLayout(self.tag_widget)
        self.tag_vbox.setContentsMargins(0, 0, 0, 0)
        self.tag_vbox.setSpacing(2)
        self.tag_scroll.setWidget(self.tag_widget)
        sb.addWidget(self.tag_scroll, stretch=1)

        # Mostrar/ocultar las etiquetas escondidas del sidebar (para poder restaurarlas)
        self.btn_show_hidden = QPushButton("")
        self.btn_show_hidden.setCheckable(True)
        self.btn_show_hidden.setStyleSheet(
            "QPushButton{color:#8888AA;font-size:11px;padding:3px 8px;}"
            "QPushButton:checked{color:#FFD700;border-color:#FFD700;}"
        )
        self.btn_show_hidden.toggled.connect(self._on_show_hidden_toggled)
        sb.addWidget(self.btn_show_hidden)

        btn_clear = QPushButton("✕ Limpiar filtros")
        btn_clear.setStyleSheet("color:#FF4A4A;")
        btn_clear.clicked.connect(self._clear_filters)
        sb.addWidget(btn_clear)

        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#666;font-size:10px;")
        sb.addWidget(self.stats_lbl)
        root.addWidget(sidebar)

        # ── Área principal ─────────────────────────────────────────────────
        main_area = QWidget()
        ml = QVBoxLayout(main_area)
        ml.setContentsMargins(12, 12, 12, 12)
        ml.setSpacing(8)

        # Barra superior
        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Buscar por nombre…")
        self.search_edit.textChanged.connect(self._on_search)
        top_bar.addWidget(self.search_edit)

        # ── FEATURE: ordenamiento ─────────────────────────────────────────
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

        self.count_lbl = QLabel("0 fotos")
        self.count_lbl.setStyleSheet("color:#8888AA;")
        top_bar.addWidget(self.count_lbl)

        # ── FEATURE: modo selección ───────────────────────────────────────
        self.btn_select = QPushButton("☐ Seleccionar")
        self.btn_select.setCheckable(True)
        self.btn_select.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        self.btn_select.toggled.connect(self._toggle_select_mode)
        top_bar.addWidget(self.btn_select)

        self.btn_bulk_tag = QPushButton("🏷 Etiquetar selección")
        self.btn_bulk_tag.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_bulk_tag.setVisible(False)
        self.btn_bulk_tag.clicked.connect(self._open_bulk_tag)
        top_bar.addWidget(self.btn_bulk_tag)

        ml.addLayout(top_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(8)
        self.scroll_area.setWidget(self.grid_widget)
        ml.addWidget(self.scroll_area, stretch=1)

        pg_bar = QHBoxLayout()
        self.prev_btn = QPushButton("← Anterior")
        self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn = QPushButton("Siguiente →")
        self.next_btn.clicked.connect(self._next_page)
        self.page_lbl = QLabel("")
        self.page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pg_bar.addWidget(self.prev_btn)
        pg_bar.addWidget(self.page_lbl, stretch=1)
        pg_bar.addWidget(self.next_btn)
        ml.addLayout(pg_bar)

        root.addWidget(main_area, stretch=1)

    # ── Sidebar de etiquetas ──────────────────────────────────────────────────

    def _refresh_tags(self):
        clear_layout(self.tag_vbox)

        n_hidden = services.count_sidebar_hidden_tags()
        self.btn_show_hidden.setVisible(n_hidden > 0 or self._show_sidebar_hidden)
        self.btn_show_hidden.blockSignals(True)
        self.btn_show_hidden.setChecked(self._show_sidebar_hidden)
        self.btn_show_hidden.blockSignals(False)
        self.btn_show_hidden.setText(
            f"🚫 Ocultar escondidas ({n_hidden})"
            if self._show_sidebar_hidden
            else f"👁 Mostrar escondidas ({n_hidden})"
        )

        groups = services.get_sidebar_tags(include_hidden=self._show_sidebar_hidden)
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
                row = QWidget()
                hl = QHBoxLayout(row)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)
                chk = QCheckBox(tag.name)
                # Las escondidas se ven en cursiva mientras se muestran
                italic = "font-style:italic;" if tag.sidebar_hidden else ""
                chk.setStyleSheet(f"color:{tag.color};{italic}")
                chk.setProperty("tag_id", tag.id)
                if tag.id in self._active_tags:
                    chk.setChecked(True)
                chk.stateChanged.connect(self._on_tag_filter_changed)
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
                lambda checked, gw=group_widget, btn=header: (
                    gw.setVisible(checked),
                    btn.setText(f"{'▾' if checked else '▸'}  {btn.text()[2:]}"),
                )
            )
            self.tag_vbox.addWidget(header)
            self.tag_vbox.addWidget(group_widget)

        self.tag_vbox.addStretch()
        self._update_stats()

    def _toggle_sidebar_hidden(self, tag_id: int, currently_hidden: bool):
        services.set_tag_sidebar_hidden(tag_id, not currently_hidden)
        if currently_hidden and services.count_sidebar_hidden_tags() == 0:
            self._show_sidebar_hidden = False
        self._refresh_tags()

    def _on_show_hidden_toggled(self, checked: bool):
        self._show_sidebar_hidden = checked
        self._refresh_tags()

    def _sidebar_checkboxes(self) -> list[QCheckBox]:
        return [
            chk
            for w in layout_widgets(self.tag_vbox)
            for chk in w.findChildren(QCheckBox)
            if chk.property("tag_id") is not None
        ]

    def _on_tag_filter_changed(self):
        self._active_tags = [chk.property("tag_id") for chk in self._sidebar_checkboxes() if chk.isChecked()]
        self._offset = 0
        self._load_photos()

    def _clear_filters(self):
        self._active_tags = []
        for chk in self._sidebar_checkboxes():
            chk.blockSignals(True)
            chk.setChecked(False)
            chk.blockSignals(False)
        self._offset = 0
        self._load_photos()

    # ── Ordenamiento ──────────────────────────────────────────────────────────

    def _on_sort_changed(self):
        self._sort_field = self.sort_field_combo.currentData()
        self._sort_order = self.sort_order_combo.currentData()
        self._offset = 0
        self._load_photos()

    # ── Modo selección ────────────────────────────────────────────────────────

    def _toggle_select_mode(self, checked: bool):
        self._select_mode = checked
        self._selected_ids = set()
        self.btn_bulk_tag.setVisible(checked)
        self.btn_select.setText("✓ Cancelar selección" if checked else "☐ Seleccionar")
        self._load_photos()

    def _on_thumb_selected(self, photo_id: int, is_selected: bool):
        if is_selected:
            self._selected_ids.add(photo_id)
        else:
            self._selected_ids.discard(photo_id)
        n = len(self._selected_ids)
        self.btn_bulk_tag.setText(f"🏷 Etiquetar {n} fotos" if n else "🏷 Etiquetar selección")

    def _open_bulk_tag(self):
        if not self._selected_ids:
            QMessageBox.information(self, "Sin selección", "Selecciona al menos una foto.")
            return
        dlg = BulkTagDialog(list(self._selected_ids), self)
        if dlg.exec():
            self._selected_ids.clear()
            self._toggle_select_mode(False)
            self.btn_select.setChecked(False)

    # ── Carga de fotos ────────────────────────────────────────────────────────

    def _stop_loader(self):
        """Descarta el loader actual sin bloquear la UI (ver retire_thread)."""
        if self._loader is not None:
            disconnect_all(self._loader.loaded, self._loader.finished)
            retire_thread(self._loader)
            self._loader = None

    def _load_photos(self):
        self._build_timer.stop()
        self._stop_loader()
        clear_layout(self.grid_layout)
        self._thumbnails.clear()

        search = self.search_edit.text().strip() or None
        self._current_page = services.get_gallery_page(
            tag_ids=self._active_tags or None,
            search=search,
            limit=self._page_size,
            offset=self._offset,
            sort_field=self._sort_field,
            sort_order=self._sort_order,
        )

        self.count_lbl.setText(f"{self._current_page.total:,} fotos")
        self._update_pagination()

        new_cols = max(1, (self.scroll_area.width() - 30) // 218)
        self._grid_cols = self._last_grid_cols = new_cols
        self._pending_photos = list(enumerate(self._current_page.photos))
        self._build_timer.start()

    def _add_next_batch(self):
        BATCH = 10
        batch, self._pending_photos = self._pending_photos[:BATCH], self._pending_photos[BATCH:]
        for idx, photo in batch:
            thumb = PhotoThumbnail(photo, selectable=self._select_mode)
            if self._select_mode:
                thumb.selected.connect(self._on_thumb_selected)
                if photo.id in self._selected_ids:
                    thumb.set_selected(True)
            else:
                thumb.clicked.connect(self._open_photo)
            self.grid_layout.addWidget(thumb, idx // self._grid_cols, idx % self._grid_cols)
            self._thumbnails[photo.id] = thumb

        if not self._pending_photos and self._current_page is not None:
            self._build_timer.stop()
            loader = ThumbnailLoader(self._current_page.photos)
            loader.loaded.connect(self._on_thumb_loaded)
            loader.finished.connect(lambda ldr=loader: self._on_loader_finished(ldr))
            self._loader = loader
            loader.start()

    def _on_loader_finished(self, loader: ThumbnailLoader):
        if self._loader is loader:
            self._loader = None

    def _on_thumb_loaded(self, photo_id: int, img: QImage):
        # QPixmap solo se crea aquí, en el hilo de la UI
        if photo_id in self._thumbnails:
            self._thumbnails[photo_id].set_pixmap(QPixmap.fromImage(img))

    def _on_search(self):
        self._offset = 0
        self._load_photos()

    # ── Paginación ────────────────────────────────────────────────────────────

    def _update_pagination(self):
        if not self._current_page:
            return
        p = self._current_page
        self.page_lbl.setText(f"Página {p.page_number} / {p.total_pages}")
        self.prev_btn.setEnabled(p.has_prev)
        self.next_btn.setEnabled(p.has_next)

    def _prev_page(self):
        self._offset = max(0, self._offset - self._page_size)
        self._load_photos()

    def _next_page(self):
        self._offset += self._page_size
        self._load_photos()

    # ── Resize ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()

    def _on_resize_settled(self):
        new_cols = max(1, (self.scroll_area.width() - 30) // 218)
        if new_cols != self._last_grid_cols:
            self._last_grid_cols = new_cols
            self._load_photos()

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _open_photo(self, photo_id: int):
        if not self._current_page:
            return
        photo = next((p for p in self._current_page.photos if p.id == photo_id), None)
        if not photo:
            return
        dlg = PhotoDetailDialog(photo, self)
        dlg.tags_changed.connect(self._load_photos)
        dlg.exec()

    def _open_index_dialog(self):
        dlg = IndexDialog(self)
        dlg.indexing_done.connect(self._on_index_done)
        dlg.exec()

    def _on_index_done(self):
        self._refresh_tags()
        self._load_photos()

    def _open_tag_manager(self):
        TagManagerDialog(self).exec()
        self._refresh_tags()
        self._load_photos()

    def _open_settings_dialog(self):
        dlg = SettingsDialog(self._page_size, self)
        if dlg.exec():
            self._page_size = dlg.page_size
            services.set_page_size(self._page_size)
            self._offset = 0
            self._load_photos()

    def _open_deindex_dialog(self):
        DeindexDialog(self).exec()
        self._load_photos()

    def _open_stats_dialog(self):
        StatsDialog(self).exec()

    def _open_duplicates_dialog(self):
        DuplicatesDialog(self).exec()
        self._load_photos()

    def _update_stats(self):
        stats = services.get_stats()
        self.stats_lbl.setText(f"{stats.total_photos:,} fotos  •  {stats.total_tags} etiquetas")

    def _open_quick_tag(self):
        setup = QuickTagSetupDialog(self)
        if setup.exec() != QDialog.DialogCode.Accepted:
            return
        photos = services.get_photos_for_tagging(
            folder=setup.selected_folder,
            tag_ids=setup.selected_tag_ids or None,
            untagged_only=setup.untagged_only,
        )
        if not photos:
            QMessageBox.information(self, "Sin fotos", "No se encontraron fotos con ese filtro.")
            return
        win = QuickTagWindow(photos, self)
        win.done_signal.connect(self._load_photos)
        win.exec()

    def closeEvent(self, event):
        self._build_timer.stop()
        self._stop_loader()
        wait_all_threads()
        db.close_connection()
        super().closeEvent(event)
