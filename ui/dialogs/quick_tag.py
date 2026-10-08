"""
PhotoVault - ui/dialogs/quick_tag.py
Etiquetado rápido con varias etiquetas: una foto a la vez y cada tecla pone o
quita su etiqueta. (La configuración está en quick_tag_setup.py y los modos de
una sola etiqueta en review.py.)
"""

import logging
from collections import OrderedDict
from functools import partial

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import services
from models import Photo, Tag
from services import GalleryQuery
from ui.dialogs.keymap import bind_keys, display_keys
from ui.photo_stage import PhotoStage
from ui.style import DARK_STYLE
from ui.viewer import ViewerWindow
from ui.widgets import ClickableRow, clear_layout, layout_widgets

logger = logging.getLogger(__name__)

PRELOAD_AHEAD = 3


def _kbd(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:4px;"
        "color:#8888AA;font-size:11px;padding:2px 6px;"
    )
    return lbl


class QuickTagWindow(QDialog):
    """
    Una foto a la vez; cada etiqueta tiene su tecla (configurable, sin límite
    de 9). Deshacer vuelve a la foto afectada. Al salir recuerda dónde quedó
    para ofrecer continuar ahí la próxima vez con la misma consulta.
    """

    done_signal = pyqtSignal()  # al cerrar, para que la galería recargue

    def __init__(self, photos: services.PhotoList, query: GalleryQuery | None = None, parent=None):
        super().__init__(parent)
        self.photos = photos
        self.query = query
        self.index = 0
        # Una entrada por acción (repetir agrega varias de una vez):
        # (índice, [(photo_id, tag_id, se agregó)])
        self._history: list[tuple[int, list[tuple[int, int, bool]]]] = []
        self.keymap = services.get_keymap()
        self._tag_keys = services.get_tag_keys()  # tecla → tag_id
        self._current_tag_ids: set[int] = set()

        self.setWindowTitle("Etiquetado rápido — varias etiquetas")
        self.setMinimumSize(1000, 640)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        for action, slot in (
            ("multi.next", self._next),
            ("multi.prev", self._prev),
            ("multi.repeat", self.repeat_previous),
            ("multi.undo", self._undo),
            ("common.exit", self.close),
            ("common.viewer", self._open_viewer),
        ):
            bind_keys(self, self.keymap.get(action, []), slot)
        for key, tag_id in self._tag_keys.items():
            tag = self._tags_by_id.get(tag_id)
            if tag is not None:
                bind_keys(self, [key], partial(self._toggle_tag, tag))
        self._maybe_resume()
        self._load_current()

    # ── Construcción de UI ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        topbar = QWidget()
        topbar.setStyleSheet("background:#13131F;border-bottom:1px solid #2D2D3F;")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 8, 14, 8)
        title = QLabel("⚡ Etiquetado rápido")
        title.setStyleSheet("color:#4A9EFF;font-size:13px;font-weight:bold;")
        tb.addWidget(title)
        for action, desc in (
            ("multi.prev", "anterior"),
            ("multi.next", "siguiente"),
            ("multi.repeat", "repetir las de la anterior"),
            ("multi.undo", "deshacer"),
            ("common.exit", "salir"),
        ):
            keys = display_keys(self.keymap.get(action, []))
            if keys:
                tb.addWidget(_kbd(keys))
                lbl = QLabel(desc)
                lbl.setStyleSheet("color:#555;font-size:11px;")
                tb.addWidget(lbl)
                tb.addSpacing(8)
        tb.addStretch()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color:#8888AA;font-size:12px;")
        tb.addWidget(self.progress_lbl)
        btn_exit = QPushButton("✕  Salir")
        btn_exit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_exit.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;padding:4px 10px;")
        btn_exit.clicked.connect(self.close)
        tb.addWidget(btn_exit)
        root.addWidget(topbar)

        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        photo_area = QWidget()
        photo_area.setStyleSheet("background:#0D0D1A;")
        pal = QVBoxLayout(photo_area)
        pal.setContentsMargins(0, 0, 0, 6)
        self.stage = PhotoStage()
        pal.addWidget(self.stage, stretch=1)
        self.chips_widget = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_widget)
        self.chips_layout.setContentsMargins(0, 8, 0, 0)
        self.chips_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chips_layout.setSpacing(6)
        pal.addWidget(self.chips_widget)
        self.filename_lbl = QLabel("")
        self.filename_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.filename_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filename_lbl.setStyleSheet("color:#555;font-size:11px;margin-top:4px;")
        pal.addWidget(self.filename_lbl)
        bl.addWidget(photo_area, stretch=1)

        panel = QWidget()
        panel.setFixedWidth(280)
        panel.setStyleSheet("background:#13131F;border-left:1px solid #2D2D3F;")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        search_bar = QWidget()
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
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_container = QWidget()
        self.tag_vbox = QVBoxLayout(self.tag_container)
        self.tag_vbox.setContentsMargins(8, 4, 8, 4)
        self.tag_vbox.setSpacing(1)
        self.tag_scroll.setWidget(self.tag_container)
        pl.addWidget(self.tag_scroll, stretch=1)
        hint = QLabel("Las teclas de cada etiqueta se cambian en «⌨ Teclas…» al configurar.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666;font-size:10px;padding:6px 10px;")
        pl.addWidget(hint)
        bl.addWidget(panel)
        root.addWidget(body, stretch=1)
        self._build_tag_list()

    def _build_tag_list(self):
        """Construye los widgets de etiquetas una sola vez."""
        self._tag_rows: list[tuple[QWidget, Tag, QLabel, QLabel]] = []
        all_tags = services.get_all_tags(include_sidebar_hidden=True)
        self._tags_by_id: dict[int, Tag] = {t.id: t for t in all_tags}
        key_of = {tid: key for key, tid in self._tag_keys.items()}
        aliases = services.get_tag_aliases()
        self._search_text = {t.id: " ".join([t.name, *aliases.get(t.id, [])]).lower() for t in all_tags}

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
                name_lbl.setTextFormat(Qt.TextFormat.PlainText)
                name_lbl.setStyleSheet("color:#D0D0E8;font-size:12px;")
                hl.addWidget(name_lbl, stretch=1)
                kbd_lbl = QLabel(display_keys([key_of[tag.id]]) if tag.id in key_of else "")
                kbd_lbl.setStyleSheet(
                    "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:3px;"
                    "color:#8888AA;font-size:10px;padding:1px 5px;"
                )
                kbd_lbl.setVisible(tag.id in key_of)
                hl.addWidget(kbd_lbl)
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

    def current(self) -> Photo | None:
        return self.photos.photo_at(self.index)

    def _load_current(self):
        total = self.photos.count()
        photo = self.current()
        if photo is None:
            self.stage.show_message("No hay fotos para etiquetar.")
            self.progress_lbl.setText("")
            return
        self.progress_lbl.setText(f"{self.index + 1:,} / {total:,}")
        self.filename_lbl.setText(photo.filename)
        ahead = [
            p for r in range(self.index + 1, self.index + 1 + PRELOAD_AHEAD) if (p := self.photos.photo_at(r))
        ]
        prev = self.photos.photo_at(self.index - 1)
        self.stage.show_photo(photo, ahead + ([prev] if prev else []))
        self._current_tag_ids = {t.id for t in services.get_photo_tags(photo.id)}
        self._refresh_tag_ui()

    def _refresh_tag_ui(self):
        """Actualiza checkmarks y chips sin recargar toda la lista."""
        for row, tag, _name_lbl, check_lbl in self._tag_rows:
            active = tag.id in self._current_tag_ids
            check_lbl.setVisible(active)
            row.setStyleSheet("border-radius:5px;background:#1E2E4E;" if active else "border-radius:5px;")
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
        """Muestra/oculta filas según búsqueda (nombre o alias)."""
        text = text.lower()
        for row, tag, _name_lbl, _check_lbl in self._tag_rows:
            row.setVisible(not text or text in self._search_text.get(tag.id, tag.name))
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
        photo = self.current()
        if photo is None:
            return
        if tag.id in self._current_tag_ids:
            services.remove_tag(photo.id, tag.id)
            self._current_tag_ids.discard(tag.id)
            self._history.append((self.index, [(photo.id, tag.id, False)]))
        else:
            services.add_tag_by_id(photo.id, tag.id)
            self._current_tag_ids.add(tag.id)
            self._history.append((self.index, [(photo.id, tag.id, True)]))
        self._refresh_tag_ui()

    def repeat_previous(self) -> None:
        """#51: agrega a esta foto las etiquetas de la anterior (no quita ninguna)."""
        photo, prev = self.current(), self.photos.photo_at(self.index - 1)
        if photo is None or prev is None:
            return
        added: list[tuple[int, int, bool]] = []
        for tag in services.get_photo_tags(prev.id):
            if tag.id not in self._current_tag_ids:
                services.add_tag_by_id(photo.id, tag.id)
                self._current_tag_ids.add(tag.id)
                added.append((photo.id, tag.id, True))
        if added:
            self._history.append((self.index, added))  # un solo Ctrl+Z deshace todo
        self._refresh_tag_ui()

    def _undo(self):
        if not self._history:
            return
        index, changes = self._history.pop()
        for photo_id, tag_id, was_added in reversed(changes):
            if was_added:
                services.remove_tag(photo_id, tag_id)
            else:
                services.add_tag_by_id(photo_id, tag_id)
        self.index = index  # #53: deshacer vuelve a la foto afectada
        self._load_current()

    def _prev(self):
        if self.index > 0:
            self.index -= 1
            self._load_current()

    def _next(self):
        if self.index < self.photos.count() - 1:
            self.index += 1
            self._load_current()
        elif self.photos.count():
            self._finish()

    def _open_viewer(self):
        photo = self.current()
        if photo is not None:
            ViewerWindow(services.PhotoList([photo.id]), 0, self, fullscreen=False).exec()

    def _finish(self):
        QMessageBox.information(self, "¡Listo!", f"Etiquetado completado.\n{self.photos.count():,} fotos.")
        self.close()

    # ── Retomar (#52) ─────────────────────────────────────────────────────────

    def _maybe_resume(self) -> None:
        if self.query is None:
            return
        pid = services.resume_position(self.query)
        row = self.photos.index_of(pid) if pid is not None else None
        if not row:  # None o la primera: no hay nada que retomar
            return
        answer = QMessageBox.question(
            self,
            "Continuar",
            f"La última vez quedaste en la foto {row + 1:,} de {self.photos.count():,}. ¿Continuar ahí?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.index = row

    def done(self, result: int) -> None:
        photo = self.current()
        if self.query is not None and photo is not None:
            services.remember_position(self.query, photo.id)
        self.stage.shutdown()
        self.done_signal.emit()
        super().done(result)
