"""
PhotoVault - ui/main_window.py
Ventana principal: sidebar, galería paginada, filtros.
"""

import logging
from functools import partial
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap, QPixmapCache
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
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import database as db
import indexer
import services
from models import GalleryPage, Photo, SortField, SortOrder
from ui.dialogs.duplicates import DuplicatesDialog
from ui.dialogs.folders import DeindexDialog, IndexDialog
from ui.dialogs.photo import BulkTagDialog, PhotoDetailDialog
from ui.dialogs.quick_tag import QuickTagSetupDialog, QuickTagWindow
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.stats import StatsDialog
from ui.dialogs.tags import TagManagerDialog
from ui.style import DARK_STYLE
from ui.widgets import PhotoThumbnail, TaskStatusWidget, clear_layout, layout_widgets
from ui.workers import (
    IndexWorker,
    StoppableThread,
    TaskWorker,
    ThumbnailLoader,
    disconnect_all,
    retire_thread,
    wait_all_threads,
)

logger = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 300
# Miniaturas ya mostradas en esta sesión, en memoria (volver a una página es instantáneo).
# 150 MB ≈ 900 miniaturas de 200×200.
PIXMAP_CACHE_KB = 150 * 1024


def _pixmap_key(photo: Photo) -> str:
    return f"thumb:{photo.id}:{photo.mtime}"


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
        self._photos_by_id: dict[int, Photo] = {}

        self._build_timer = QTimer(self)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._add_next_batch)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(400)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_grid_cols = 0

        # La búsqueda espera a que se deje de escribir (antes: una consulta por tecla)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._on_search)

        QPixmapCache.setCacheLimit(PIXMAP_CACHE_KB)

        # Tarea en segundo plano (una a la vez): "index" o "thumbs"
        self._bg_worker: StoppableThread | None = None
        self._bg_kind: str | None = None

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
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_edit.returnPressed.connect(self._on_search)  # Enter: buscar ya
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

        # Barra de estado: progreso de indexación / miniaturas en segundo plano
        self.task_status = TaskStatusWidget()
        self.task_status.cancel_clicked.connect(self._cancel_background)
        status_bar = QStatusBar()
        status_bar.setStyleSheet("QStatusBar{background:#13131F;border-top:1px solid #2D2D3F;}")
        status_bar.addPermanentWidget(self.task_status, 1)
        self.setStatusBar(status_bar)

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

    def _gallery_query(self) -> dict:
        return {
            "tag_ids": self._active_tags or None,
            "search": self.search_edit.text().strip() or None,
            "limit": self._page_size,
            "sort_field": self._sort_field,
            "sort_order": self._sort_order,
        }

    def _load_photos(self):
        self._search_timer.stop()
        self._build_timer.stop()
        self._stop_loader()
        clear_layout(self.grid_layout)
        self._thumbnails.clear()

        self._current_page = services.get_gallery_page(offset=self._offset, **self._gallery_query())

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
            cached = QPixmapCache.find(_pixmap_key(photo))
            if cached is not None:
                thumb.set_pixmap(cached)
            self.grid_layout.addWidget(thumb, idx // self._grid_cols, idx % self._grid_cols)
            self._thumbnails[photo.id] = thumb

        if not self._pending_photos and self._current_page is not None:
            self._build_timer.stop()
            page = self._current_page
            missing = [p for p in page.photos if QPixmapCache.find(_pixmap_key(p)) is None]
            prefetch: list[Photo] = []
            if page.has_next:
                prefetch = services.get_gallery_photos(
                    offset=page.offset + page.limit, **self._gallery_query()
                )
            self._photos_by_id = {p.id: p for p in page.photos}
            loader = ThumbnailLoader(missing, prefetch=prefetch)
            loader.loaded.connect(self._on_thumb_loaded)
            loader.finished.connect(lambda ldr=loader: self._on_loader_finished(ldr))
            self._loader = loader
            loader.start()

    def _on_loader_finished(self, loader: ThumbnailLoader):
        if self._loader is loader:
            self._loader = None

    def _on_thumb_loaded(self, photo_id: int, img: QImage):
        # QPixmap solo se crea aquí, en el hilo de la UI
        pix = QPixmap.fromImage(img)
        photo = self._photos_by_id.get(photo_id)
        if photo is not None:
            QPixmapCache.insert(_pixmap_key(photo), pix)
        if photo_id in self._thumbnails:
            self._thumbnails[photo_id].set_pixmap(pix)

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
        dlg = IndexDialog(self, busy=self._bg_kind == "index")
        dlg.start_requested.connect(self.start_indexing)
        dlg.exec()

    # ── Tareas en segundo plano (barra de estado) ─────────────────────────────

    def is_indexing(self) -> bool:
        return self._bg_kind == "index"

    def _retire_background(self) -> None:
        if self._bg_worker is not None:
            w = self._bg_worker
            signals = [getattr(w, name) for name in ("progress", "completed", "error") if hasattr(w, name)]
            disconnect_all(*signals)
            retire_thread(w)
        self._bg_worker = None
        self._bg_kind = None

    def _cancel_background(self) -> None:
        if self._bg_worker is not None:
            self.task_status.set_cancelling()
            self._bg_worker.stop()  # Termina el archivo actual y emite completed

    def start_indexing(self, folder: str) -> None:
        """Indexa en segundo plano: se puede seguir usando la app mientras tanto."""
        if self._bg_kind == "index":
            QMessageBox.information(self, "Indexación en curso", "Ya hay una indexación en curso.")
            return
        if self._bg_kind == "thumbs":
            self._retire_background()  # La indexación tiene prioridad
        w = IndexWorker(folder)
        w.progress.connect(self._on_index_progress)
        w.completed.connect(self._on_index_completed)
        w.error.connect(self._on_background_error)
        self._bg_worker, self._bg_kind = w, "index"
        self.task_status.start(f"Indexando {folder}…")
        w.start()

    def _on_index_progress(self, current: int, total: int, path: str) -> None:
        self.task_status.set_progress(current, total, f"Indexando [{current:,}/{total:,}] {Path(path).name}")

    def _on_index_completed(self, result: indexer.IndexResult) -> None:
        self._bg_worker, self._bg_kind = None, None
        txt = (
            f"{'Indexación cancelada' if result.cancelled else '✓ Indexación lista'}: "
            f"{result.added:,} nuevas, {result.updated:,} actualizadas, "
            f"{result.unchanged:,} sin cambios"
        )
        if result.errors:
            txt += f", {result.errors:,} errores (ver log)"
        self.task_status.finish(txt)
        self._refresh_tags()
        self._load_photos()
        if result.new_ids:
            # Las miniaturas de lo nuevo se generan ya, sin esperar a que se vean
            self.start_thumbnail_generation(result.new_ids, quiet_done=True)

    def start_thumbnail_generation(
        self, photo_ids: list[int] | None = None, quiet_done: bool = False
    ) -> bool:
        """Genera miniaturas en segundo plano (None = toda la colección). False si hay otra tarea."""
        if self._bg_worker is not None:
            return False
        w = TaskWorker(partial(services.pregenerate_thumbnails, photo_ids))
        w.progress.connect(self._on_thumbs_progress)
        w.completed.connect(self._on_thumbs_completed)
        w.error.connect(self._on_background_error)
        self._bg_worker, self._bg_kind = w, "thumbs"
        self.task_status.start("Preparando miniaturas…")
        w.start()
        return True

    def _on_thumbs_progress(self, current: int, total: int) -> None:
        self.task_status.set_progress(current, total, f"Generando miniaturas [{current:,}/{total:,}]")

    def _on_thumbs_completed(self, r: services.ThumbnailBatchResult) -> None:
        self._bg_worker, self._bg_kind = None, None
        txt = f"✓ Miniaturas: {r.generated:,} generadas, {r.already_cached:,} ya estaban"
        if r.failed:
            txt += f", {r.failed:,} no se pudieron generar"
        self.task_status.finish(txt)

    def _on_background_error(self, message: str) -> None:
        self._bg_worker, self._bg_kind = None, None
        self.task_status.finish(f"Error: {message}", hide_after_ms=20_000)
        QMessageBox.critical(self, "Error", message)

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
        photos, tags = services.get_totals()
        self.stats_lbl.setText(f"{photos:,} fotos  •  {tags} etiquetas")

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
        if self._bg_kind == "index":
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
        self._retire_background()
        self._build_timer.stop()
        self._stop_loader()
        wait_all_threads()
        db.close_connection()
        super().closeEvent(event)
