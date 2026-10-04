"""
PhotoVault - ui/dialogs/quick_tag.py
Modo etiquetado rápido (configuración + ventana de teclado).
"""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
import services
import thumbnail_cache
from models import Photo, Tag
from ui.style import DARK_STYLE
from ui.widgets import ClickableRow, clear_layout, layout_widgets

logger = logging.getLogger(__name__)


# ─── Dialog: Configuración del modo etiquetado rápido ────────────────────────


class QuickTagSetupDialog(QDialog):
    """
    Permite al usuario elegir qué fotos incluir en el modo etiquetado rápido:
    - Carpeta indexada (opcional)
    - Etiquetas que deben tener (opcional)
    - Solo fotos sin etiquetar
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modo etiquetado rápido — configurar")
        self.setFixedSize(480, 380)
        self.setStyleSheet(DARK_STYLE)
        self.selected_folder: str | None = None
        self.selected_tag_ids: list[int] = []
        self.untagged_only: bool = False
        self._build_ui()
        self._refresh_count()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(
            QLabel(
                "<b>¿Qué fotos quieres etiquetar?</b><br>"
                "<span style='color:#888;font-size:11px;'>"
                "Combina los filtros para acotar el conjunto.</span>"
            )
        )

        # ── Filtro por carpeta ─────────────────────────────────────────────
        box_folder = QGroupBox("Carpeta indexada (opcional)")
        fl = QHBoxLayout(box_folder)
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("— Todas las carpetas —", userData=None)
        for folder, count in services.get_indexed_folders():
            label = f"{Path(folder).name}  ({count:,} archivos)"
            self.folder_combo.addItem(label, userData=folder)
        self.folder_combo.currentIndexChanged.connect(self._refresh_count)
        fl.addWidget(self.folder_combo)
        layout.addWidget(box_folder)

        # ── Filtro por etiquetas ───────────────────────────────────────────
        box_tags = QGroupBox("Solo fotos que tengan estas etiquetas (opcional)")
        tl = QVBoxLayout(box_tags)
        tl.setSpacing(4)

        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFixedHeight(100)
        tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_inner = QWidget()
        tag_grid = QVBoxLayout(tag_inner)
        tag_grid.setContentsMargins(4, 4, 4, 4)
        tag_grid.setSpacing(2)

        self._tag_checks: list[tuple[QCheckBox, int]] = []
        for tag in services.get_all_tags(include_sidebar_hidden=True):
            chk = QCheckBox(f"{tag.name}  [{tag.category}]")
            chk.setStyleSheet(f"color:{tag.color};")
            chk.stateChanged.connect(self._refresh_count)
            tag_grid.addWidget(chk)
            self._tag_checks.append((chk, tag.id))
        tag_grid.addStretch()

        tag_scroll.setWidget(tag_inner)
        tl.addWidget(tag_scroll)
        layout.addWidget(box_tags)

        # ── Solo sin etiquetar ─────────────────────────────────────────────
        self.chk_untagged = QCheckBox("Solo fotos sin ninguna etiqueta")
        self.chk_untagged.setStyleSheet("color:#FFD700;")
        self.chk_untagged.stateChanged.connect(self._refresh_count)
        layout.addWidget(self.chk_untagged)

        # ── Contador de resultados ─────────────────────────────────────────
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#4A9EFF;font-size:12px;")
        layout.addWidget(self.count_lbl)

        layout.addStretch()

        # ── Botones ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)

        self.btn_start = QPushButton("▶  Iniciar etiquetado")
        self.btn_start.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_start.clicked.connect(self._start)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_start)
        layout.addLayout(btn_row)

    def _refresh_count(self):
        folder = self.folder_combo.currentData()
        tag_ids = [tid for chk, tid in self._tag_checks if chk.isChecked()]
        untagged = self.chk_untagged.isChecked()
        n = services.count_photos_for_tagging(folder=folder, tag_ids=tag_ids or None, untagged_only=untagged)
        self.count_lbl.setText(f"{n:,} foto{'s' if n != 1 else ''} coinciden con este filtro")
        self.btn_start.setEnabled(n > 0)

    def _start(self):
        self.selected_folder = self.folder_combo.currentData()
        self.selected_tag_ids = [tid for chk, tid in self._tag_checks if chk.isChecked()]
        self.untagged_only = self.chk_untagged.isChecked()
        self.accept()


# ─── Ventana: Modo etiquetado rápido ─────────────────────────────────────────


class QuickTagWindow(QDialog):
    """
    Vista de pantalla completa para etiquetar fotos una por una con teclado.

    Controles:
      ← / →     foto anterior / siguiente
      Space      avanzar sin cambios
      1–9        activar/desactivar etiqueta por atajo
      Ctrl+Z     deshacer última acción
      Esc        salir y volver a la galería
    """

    done_signal = pyqtSignal()  # emitido al cerrar para que galería recargue

    def __init__(self, photos: list[Photo], parent=None):
        super().__init__(parent)
        self.photos = photos
        self.index = 0
        self._history: list[tuple[int, int, bool]] = []  # (photo_id, tag_id, was_added)
        self._tag_shortcuts: dict[int, Tag] = {}  # tecla 1-9 → Tag

        self.setWindowTitle("Etiquetado rápido")
        self.setMinimumSize(1000, 640)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._load_current()

    # ── Construcción de UI ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barra superior
        topbar = QWidget()
        topbar.setStyleSheet("background:#13131F;border-bottom:1px solid #2D2D3F;")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 8, 14, 8)

        title = QLabel("⚡ Etiquetado rápido")
        title.setStyleSheet("color:#4A9EFF;font-size:13px;font-weight:bold;")
        tb.addWidget(title)

        for key, desc in [
            ("←→", "navegar"),
            ("1–9", "etiqueta"),
            ("Space", "saltar"),
            ("Ctrl+Z", "deshacer"),
            ("Esc", "salir"),
        ]:
            tb.addWidget(self._kbd(key))
            lbl = QLabel(desc)
            lbl.setStyleSheet("color:#555;font-size:11px;")
            tb.addWidget(lbl)
            tb.addSpacing(10)

        tb.addStretch()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color:#8888AA;font-size:12px;")
        tb.addWidget(self.progress_lbl)

        btn_exit = QPushButton("✕  Salir")
        btn_exit.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;padding:4px 10px;")
        btn_exit.clicked.connect(self.close)
        tb.addWidget(btn_exit)
        root.addWidget(topbar)

        # Cuerpo principal
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ── Zona de foto ──────────────────────────────────────────────────
        photo_area = QWidget()
        photo_area.setStyleSheet("background:#0D0D1A;")
        pal = QVBoxLayout(photo_area)
        pal.setAlignment(Qt.AlignmentFlag.AlignCenter)

        nav_row = QHBoxLayout()
        self.btn_prev = QPushButton("‹")
        self.btn_prev.setFixedSize(44, 44)
        self.btn_prev.setStyleSheet(
            "QPushButton{background:#1E1E2E99;border:0.5px solid #3A3A5A;"
            "border-radius:22px;font-size:22px;color:#D0D0E8;}"
            "QPushButton:hover{background:#2A2A3E;border-color:#4A9EFF;}"
            "QPushButton:disabled{color:#333;border-color:#222;}"
        )
        self.btn_prev.clicked.connect(self._prev)

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setMinimumSize(500, 500)
        self.img_label.setStyleSheet("background:transparent;")

        self.btn_next = QPushButton("›")
        self.btn_next.setFixedSize(44, 44)
        self.btn_next.setStyleSheet(
            "QPushButton{background:#1E1E2E99;border:0.5px solid #3A3A5A;"
            "border-radius:22px;font-size:22px;color:#D0D0E8;}"
            "QPushButton:hover{background:#2A2A3E;border-color:#4A9EFF;}"
            "QPushButton:disabled{color:#333;border-color:#222;}"
        )
        self.btn_next.clicked.connect(self._next)

        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.img_label, stretch=1)
        nav_row.addWidget(self.btn_next)
        pal.addLayout(nav_row)

        # Chips de etiquetas activas bajo la foto
        self.chips_widget = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_widget)
        self.chips_layout.setContentsMargins(0, 8, 0, 0)
        self.chips_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chips_layout.setSpacing(6)
        pal.addWidget(self.chips_widget)

        # Nombre del archivo
        self.filename_lbl = QLabel("")
        self.filename_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.filename_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filename_lbl.setStyleSheet("color:#555;font-size:11px;margin-top:4px;")
        pal.addWidget(self.filename_lbl)

        bl.addWidget(photo_area, stretch=1)

        # ── Panel de etiquetas ────────────────────────────────────────────
        panel = QWidget()
        panel.setFixedWidth(270)
        panel.setStyleSheet("background:#13131F;border-left:1px solid #2D2D3F;")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        # Búsqueda
        search_bar = QWidget()
        search_bar.setStyleSheet("background:#13131F;padding:8px;")
        sl = QHBoxLayout(search_bar)
        sl.setContentsMargins(10, 8, 10, 8)
        self.tag_search = QLineEdit()
        self.tag_search.setPlaceholderText("Buscar etiqueta…")
        self.tag_search.textChanged.connect(self._filter_tags)
        sl.addWidget(self.tag_search)
        pl.addWidget(search_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        pl.addWidget(sep)

        # Lista de etiquetas
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_container = QWidget()
        self.tag_vbox = QVBoxLayout(self.tag_container)
        self.tag_vbox.setContentsMargins(8, 4, 8, 4)
        self.tag_vbox.setSpacing(1)
        self.tag_scroll.setWidget(self.tag_container)
        pl.addWidget(self.tag_scroll, stretch=1)

        # Pie del panel
        footer = QWidget()
        footer.setStyleSheet("background:#0D0D1A;border-top:1px solid #2D2D3F;padding:6px;")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(4)
        for key, desc in [("Space", "saltar sin cambios"), ("Ctrl+Z", "deshacer")]:
            row = QHBoxLayout()
            row.addWidget(self._kbd(key))
            lbl = QLabel(desc)
            lbl.setStyleSheet("color:#8888AA;font-size:11px;")
            row.addWidget(lbl)
            row.addStretch()
            fl.addLayout(row)
        pl.addWidget(footer)

        bl.addWidget(panel)
        root.addWidget(body, stretch=1)

        # Construir lista de etiquetas (estática, se filtra por visibilidad)
        self._build_tag_list()

    def _kbd(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:4px;"
            "color:#8888AA;font-size:11px;padding:2px 6px;"
        )
        return lbl

    def _build_tag_list(self):
        """Construye los widgets de etiquetas una sola vez."""
        self._tag_rows: list[tuple[QWidget, Tag, QLabel, QLabel]] = []
        shortcut_n = 1

        all_tags = services.get_all_tags(include_sidebar_hidden=True)
        self._tags_by_id: dict[int, Tag] = {t.id: t for t in all_tags}

        # Agrupar por categoría
        from collections import OrderedDict

        groups: OrderedDict[str, list[Tag]] = OrderedDict()
        for tag in all_tags:
            groups.setdefault(tag.category or "general", []).append(tag)

        for category, tags in groups.items():
            cat_lbl = QLabel(category.upper())
            cat_lbl.setStyleSheet(
                "color:#4A6A88;font-size:10px;font-weight:500;padding:8px 4px 2px;letter-spacing:1px;"
            )
            cat_lbl.setProperty("cat_label", True)
            self.tag_vbox.addWidget(cat_lbl)

            for tag in tags:
                row = ClickableRow()
                row.setStyleSheet("border-radius:5px;")
                hl = QHBoxLayout(row)
                hl.setContentsMargins(4, 3, 6, 3)
                hl.setSpacing(7)

                dot = QLabel("●")
                dot.setStyleSheet(f"color:{tag.color};font-size:11px;")
                dot.setFixedWidth(14)
                hl.addWidget(dot)

                name_lbl = QLabel(tag.name)
                name_lbl.setStyleSheet("color:#D0D0E8;font-size:12px;")
                hl.addWidget(name_lbl, stretch=1)

                # Atajo de teclado 1-9
                kbd_lbl = QLabel("")
                kbd_lbl.setStyleSheet(
                    "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:3px;"
                    "color:#8888AA;font-size:10px;padding:1px 5px;"
                )
                kbd_lbl.setFixedWidth(22)
                kbd_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if shortcut_n <= 9:
                    kbd_lbl.setText(str(shortcut_n))
                    self._tag_shortcuts[shortcut_n] = tag
                    shortcut_n += 1
                hl.addWidget(kbd_lbl)

                # Check activo
                check_lbl = QLabel("✓")
                check_lbl.setStyleSheet("color:#4A9EFF;font-size:14px;")
                check_lbl.setFixedWidth(16)
                check_lbl.setVisible(False)
                hl.addWidget(check_lbl)

                row.clicked.connect(lambda t=tag: self._toggle_tag(t))
                row.setCursor(Qt.CursorShape.PointingHandCursor)

                self.tag_vbox.addWidget(row)
                self._tag_rows.append((row, tag, name_lbl, check_lbl))

        self.tag_vbox.addStretch()

    # ── Carga de foto ─────────────────────────────────────────────────────────

    def _load_current(self):
        if not self.photos:
            return

        photo = self.photos[self.index]
        total = len(self.photos)

        # Progreso
        self.progress_lbl.setText(f"{self.index + 1} / {total}")
        self.filename_lbl.setText(photo.filename)

        # Botones de navegación
        self.btn_prev.setEnabled(self.index > 0)
        self.btn_next.setEnabled(self.index < total - 1)

        # Imagen
        self.img_label.clear()
        if photo.is_video:
            jpeg = thumbnail_cache.get_video_thumbnail(photo.path, size=config.THUMB_SIZE_LARGE)
        else:
            # Para el modo rápido mostramos imagen a mayor resolución
            jpeg = thumbnail_cache.get_thumbnail(photo.path, size=config.THUMB_SIZE_LARGE)

        if jpeg:
            pix = QPixmap()
            pix.loadFromData(jpeg)
            if not pix.isNull():
                pix = pix.scaled(
                    520, 520, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self.img_label.setPixmap(pix)
        else:
            self.img_label.setText("No se pudo cargar la imagen")

        # Actualizar estado de etiquetas
        self._current_tag_ids = {t.id for t in services.get_photo_tags(photo.id)}
        self._refresh_tag_ui()

    def _refresh_tag_ui(self):
        """Actualiza checkmarks y chips sin recargar toda la lista."""
        for row, tag, _name_lbl, check_lbl in self._tag_rows:
            active = tag.id in self._current_tag_ids
            check_lbl.setVisible(active)
            row.setStyleSheet("border-radius:5px;background:#1E2E4E;" if active else "border-radius:5px;")

        # Chips bajo la imagen
        clear_layout(self.chips_layout)

        for tag_id in self._current_tag_ids:
            chip_tag = self._tags_by_id.get(tag_id)
            if chip_tag is None:
                continue
            chip = QLabel(chip_tag.name)
            chip.setTextFormat(Qt.TextFormat.PlainText)
            chip.setStyleSheet(
                f"background:{chip_tag.color}22;color:{chip_tag.color};"
                f"border:1px solid {chip_tag.color};border-radius:10px;"
                f"padding:2px 8px;font-size:11px;"
            )
            self.chips_layout.addWidget(chip)

    def _filter_tags(self, text: str):
        """Muestra/oculta filas según búsqueda."""
        text = text.lower()
        for row, tag, _name_lbl, _check_lbl in self._tag_rows:
            row.setVisible(not text or text in tag.name)
        # Ocultar headers de categoría si todos sus tags están ocultos.
        # isHidden() (no isVisible()) para que funcione aunque la ventana aún no se muestre.
        header: QWidget | None = None
        header_has_rows = False
        for w in layout_widgets(self.tag_vbox):
            if w.property("cat_label"):
                if header is not None:
                    header.setVisible(header_has_rows)
                header, header_has_rows = w, False
            elif not w.isHidden():
                header_has_rows = True
        if header is not None:
            header.setVisible(header_has_rows)

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _toggle_tag(self, tag: Tag):
        photo = self.photos[self.index]
        if tag.id in self._current_tag_ids:
            services.remove_tag(photo.id, tag.id)
            self._current_tag_ids.discard(tag.id)
            self._history.append((photo.id, tag.id, False))  # False = se quitó
        else:
            services.add_tag_by_id(photo.id, tag.id)
            self._current_tag_ids.add(tag.id)
            self._history.append((photo.id, tag.id, True))  # True = se agregó
        self._refresh_tag_ui()

    def _undo(self):
        if not self._history:
            return
        photo_id, tag_id, was_added = self._history.pop()
        if was_added:
            # Se había agregado → quitar
            services.remove_tag(photo_id, tag_id)
        else:
            # Se había quitado → volver a agregar
            services.add_tag_by_id(photo_id, tag_id)
        # Si el undo fue en la foto actual, refrescar UI
        if self.photos[self.index].id == photo_id:
            self._current_tag_ids = {t.id for t in services.get_photo_tags(photo_id)}
            self._refresh_tag_ui()

    def _prev(self):
        if self.index > 0:
            self.index -= 1
            self._load_current()

    def _next(self):
        if self.index < len(self.photos) - 1:
            self.index += 1
            self._load_current()
        else:
            self._finish()

    def _finish(self):
        QMessageBox.information(
            self, "¡Listo!", f"Etiquetado completado.\n{len(self.photos):,} fotos procesadas."
        )
        self.close()

    # ── Teclado ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Left:
            self._prev()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self._next()
        elif mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Z:
            self._undo()
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            n = key - Qt.Key.Key_0
            tag = self._tag_shortcuts.get(n)
            if tag:
                self._toggle_tag(tag)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.done_signal.emit()
        super().closeEvent(event)
