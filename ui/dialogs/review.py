"""
PhotoVault - ui/dialogs/review.py
Revisión por etiqueta: se eligió UNA etiqueta y se decide foto por foto si la
tiene (modo sí / no) o página por página marcando las que sí (cuadrícula).
Las respuestas se guardan al instante: salir en cualquier momento no pierde
nada y la próxima sesión empieza por lo que falta.
"""

import logging
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config
import services
from models import Photo, Tag
from ui.dialogs.keymap import bind_keys, display_keys
from ui.photo_stage import PhotoStage
from ui.style import DARK_STYLE
from ui.viewer import ViewerWindow
from ui.workers import disconnect_all, retire_on_destroy, retire_thread, thumbnail_queue

logger = logging.getLogger(__name__)

PRELOAD_AHEAD = 3
GRID_SIZES = (9, 12, 16, 20)
GRID_COLUMNS = {9: 3, 12: 4, 16: 4, 20: 5}

_BIG_BTN = (
    "QPushButton{{background:{c}22;color:{c};border:1px solid {c};border-radius:8px;"
    "padding:10px 22px;font-size:15px;}}QPushButton:hover{{background:{c}44;}}"
)


def _kbd(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:4px;"
        "color:#8888AA;font-size:11px;padding:2px 6px;"
    )
    return lbl


@dataclass
class _Step:
    """Una respuesta, para deshacerla: fila (o inicio de página), estado previo y qué se respondió."""

    row: int
    states: dict[int, services.ReviewState]  # vacío = se saltó
    yes: int = 0
    no: int = 0
    skipped: int = 0
    marked: frozenset[int] = frozenset()  # cuadrícula: celdas marcadas


class _ReviewBase(QDialog):
    """Barra superior, teclas, contadores, deshacer y cierre comunes a los dos modos."""

    def __init__(self, tag: Tag, photos: services.PhotoList, parent=None):
        super().__init__(parent)
        self.tag = tag
        self.photos = photos
        self.keymap = services.get_keymap()
        self.yes_count = 0
        self.no_count = 0
        self.skip_count = 0
        self._history: list[_Step] = []
        self.setStyleSheet(DARK_STYLE + "QDialog{background:#0D0D1A;}")
        self.setMinimumSize(1000, 680)

    def _top_bar(self, title: str, hints: list[tuple[str, str]]) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background:#13131F;border-bottom:1px solid #2D2D3F;")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(14, 8, 14, 8)
        t = QLabel(title)
        t.setStyleSheet("color:#4A9EFF;font-size:13px;font-weight:bold;")
        tb.addWidget(t)
        tag_lbl = QLabel(f"● {self.tag.name}")
        tag_lbl.setTextFormat(Qt.TextFormat.PlainText)
        tag_lbl.setStyleSheet(
            f"color:{self.tag.color};background:{self.tag.color}22;border:1px solid {self.tag.color};"
            "border-radius:10px;padding:2px 10px;font-size:13px;font-weight:bold;"
        )
        tb.addWidget(tag_lbl)
        tb.addSpacing(12)
        for action, desc in hints:
            keys = display_keys(self.keymap.get(action, []))
            if keys:
                tb.addWidget(_kbd(keys))
                d = QLabel(desc)
                d.setStyleSheet("color:#666;font-size:11px;")
                tb.addWidget(d)
                tb.addSpacing(8)
        tb.addStretch()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color:#D0D0E8;font-size:12px;")
        tb.addWidget(self.progress_lbl)
        btn_exit = QPushButton("✕  Salir")
        btn_exit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_exit.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;padding:4px 10px;")
        btn_exit.clicked.connect(self.close)
        tb.addWidget(btn_exit)
        return bar

    def _bind(self, action: str, slot) -> None:
        bind_keys(self, self.keymap.get(action, []), slot)

    def _counts_text(self) -> str:
        return f"✓ {self.yes_count:,}   ✕ {self.no_count:,}   ⤼ {self.skip_count:,}"

    def summary(self) -> str:
        return (
            f"«{self.tag.name}»: {self.yes_count:,} sí, {self.no_count:,} no, "
            f"{self.skip_count:,} saltadas. Todo quedó guardado."
        )

    def _record(self, step: _Step) -> None:
        self._history.append(step)
        self.yes_count += step.yes
        self.no_count += step.no
        self.skip_count += step.skipped

    def _pop_step(self) -> _Step | None:
        """Deshace la última respuesta en la DB y en los contadores; quien llama vuelve a esa foto/página."""
        if not self._history:
            return None
        step = self._history.pop()
        if step.states:
            services.review_restore(self.tag.id, step.states)
        self.yes_count -= step.yes
        self.no_count -= step.no
        self.skip_count -= step.skipped
        return step

    def _open_viewer(self, photo: Photo | None) -> None:
        if photo is not None:
            ViewerWindow(services.PhotoList([photo.id]), 0, self, fullscreen=False).exec()


# ─── Modo sí / no ─────────────────────────────────────────────────────────────


class ReviewWindow(_ReviewBase):
    """Una foto a la vez: sí / no / saltar. Deshacer vuelve a la foto (#53)."""

    def __init__(self, tag: Tag, photos: services.PhotoList, parent=None):
        super().__init__(tag, photos, parent)
        self.setWindowTitle(f"Revisar «{tag.name}» — sí / no")
        self.position = 0
        self._build_ui()
        for action, slot in (
            ("review.yes", lambda: self.answer(True)),
            ("review.no", lambda: self.answer(False)),
            ("review.skip", self.skip),
            ("review.undo", self.undo),
            ("common.exit", self.close),
            ("common.viewer", lambda: self._open_viewer(self.current())),
        ):
            self._bind(action, slot)
        self._show()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        hints = [
            ("review.yes", "sí"),
            ("review.no", "no"),
            ("review.skip", "saltar"),
            ("review.undo", "deshacer"),
        ]
        root.addWidget(self._top_bar("⚡ Sí / no", hints))
        self.frame = QFrame()
        self._frame_style("transparent")
        fl = QVBoxLayout(self.frame)
        fl.setContentsMargins(0, 0, 0, 0)
        self.stage = PhotoStage()
        fl.addWidget(self.stage)
        root.addWidget(self.frame, stretch=1)
        # Destello verde/rojo al responder: confirma sin tener que mirar los contadores
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(220)
        self._flash_timer.timeout.connect(lambda: self._frame_style("transparent"))

        bottom = QWidget()
        bottom.setStyleSheet("background:#13131F;border-top:1px solid #2D2D3F;")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(14, 8, 14, 8)
        self.name_lbl = QLabel("")
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        bl.addWidget(self.name_lbl, stretch=1)
        k = self.keymap
        for text, color, slot in (
            (f"✕  No   {display_keys(k['review.no'])}", config.COLORS["danger"], lambda: self.answer(False)),
            (f"⤼  Saltar   {display_keys(k['review.skip'])}", config.COLORS["text_dim"], self.skip),
            (f"✓  Sí   {display_keys(k['review.yes'])}", config.COLORS["success"], lambda: self.answer(True)),
        ):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # las flechas son del modo, no de los botones
            b.setStyleSheet(_BIG_BTN.format(c=color))
            b.clicked.connect(slot)
            bl.addWidget(b)
        bl.addStretch(1)
        root.addWidget(bottom)

    def _frame_style(self, color: str) -> None:
        self.frame.setStyleSheet(f"QFrame{{border:4px solid {color};}}")

    def current(self) -> Photo | None:
        return self.photos.photo_at(self.position)

    def _show(self) -> None:
        total = self.photos.count()
        self.progress_lbl.setText(f"{min(self.position + 1, total):,} / {total:,}     {self._counts_text()}")
        if self.position >= total:
            self.name_lbl.setText("")
            text = (
                "No hay fotos pendientes para esta etiqueta."
                if total == 0
                else "¡Listo!\n\n" + self.summary()
            )
            self.stage.show_message(text + "\n\nEsc para salir.")
            return
        photo = self.current()
        self.name_lbl.setText(photo.filename if photo else "")
        ahead = [
            p
            for r in range(self.position + 1, self.position + 1 + PRELOAD_AHEAD)
            if (p := self.photos.photo_at(r))
        ]
        self.stage.show_photo(photo, ahead)

    def answer(self, yes: bool) -> None:
        photo = self.current()
        if photo is None:
            return
        states = services.review_answer(self.tag.id, [photo.id] if yes else [], [] if yes else [photo.id])
        self._record(_Step(self.position, states, yes=int(yes), no=int(not yes)))
        self._frame_style(config.COLORS["success"] if yes else config.COLORS["danger"])
        self._flash_timer.start()
        self.position += 1
        self._show()

    def skip(self) -> None:
        if self.current() is None:
            return
        self._record(_Step(self.position, {}, skipped=1))
        self.position += 1
        self._show()

    def undo(self) -> None:
        step = self._pop_step()
        if step is not None:
            self.position = step.row  # #53: deshacer vuelve a la foto
            self._show()

    def done(self, result: int) -> None:
        self.stage.shutdown()
        super().done(result)


# ─── Modo cuadrícula ──────────────────────────────────────────────────────────


class GridCell(QFrame):
    """Una celda: miniatura, n.º de tecla y marca ✓."""

    clicked = pyqtSignal(int)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.photo: Photo | None = None
        self.marked = False
        self.focused = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(120, 120)
        lay.addWidget(self.image, stretch=1)
        self.badge = QLabel(str(index + 1) if index < 9 else "", self)
        self.badge.setStyleSheet(
            "background:#000A;color:#D0D0E8;border-radius:4px;padding:1px 6px;font-size:12px;font-weight:bold;"
        )
        self.badge.move(8, 8)
        self.check = QLabel("✓", self)
        self.check.setStyleSheet(
            f"background:{config.COLORS['success']};color:#000;border-radius:12px;"
            "padding:0 6px;font-size:16px;font-weight:bold;"
        )
        self._pixmap: QPixmap | None = None
        self._restyle()

    def set_photo(self, photo: Photo | None) -> None:
        self.photo = photo
        self.marked = False
        self._pixmap = None
        self.image.clear()
        self.image.setText("" if photo is None else ("▶ video" if photo.is_video else "…"))
        self.setVisible(photo is not None)
        self._restyle()

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is not None and not self._pixmap.isNull():
            self.image.setPixmap(
                self._pixmap.scaled(
                    self.image.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()
        self.check.adjustSize()
        self.check.move(self.width() - self.check.width() - 8, 8)

    def set_marked(self, marked: bool) -> None:
        self.marked = marked
        self._restyle()

    def set_focused(self, focused: bool) -> None:
        self.focused = focused
        self._restyle()

    def _restyle(self) -> None:
        c = config.COLORS
        border = c["success"] if self.marked else c["accent"] if self.focused else c["border"]
        width = 4 if self.marked else 2 if self.focused else 1
        bg = "#16301F" if self.marked else c["panel_alt"]
        self.setStyleSheet(f"GridCell{{background:{bg};border:{width}px solid {border};border-radius:8px;}}")
        self.check.setVisible(self.marked)

    def mousePressEvent(self, event):
        self.clicked.emit(self.index)


class GridReviewWindow(_ReviewBase):
    """
    Página de N miniaturas: se marcan las que SÍ tienen la etiqueta y al
    confirmar el resto queda como "no". Para etiquetas poco comunes es mucho
    más rápido que una a una: el ojo descarta varias de un vistazo.
    """

    def __init__(self, tag: Tag, photos: services.PhotoList, grid_size: int = 12, parent=None):
        super().__init__(tag, photos, parent)
        self.setWindowTitle(f"Revisar «{tag.name}» — cuadrícula")
        self.grid_size = grid_size if grid_size in GRID_SIZES else 12
        self.page_start = 0
        self.focus_index = 0
        self._thumb_keys: dict[str, int] = {}  # clave pedida → celda
        self._thumbs = thumbnail_queue()
        self._thumbs.loaded.connect(self._on_thumb)
        self._thumbs.start()
        retire_on_destroy(self, self._thumbs)
        self._build_ui()
        for action, slot in (
            ("grid.confirm", self.confirm),
            ("grid.skip", self.skip_page),
            ("grid.toggle", lambda: self.toggle(self.focus_index)),
            ("grid.all", self.toggle_all),
            ("grid.undo", self.undo),
            ("common.exit", self.close),
            ("common.viewer", lambda: self._open_viewer(self.cells[self.focus_index].photo)),
        ):
            self._bind(action, slot)
        for n in range(1, 10):  # 1–9 marcan la celda n (fijas)
            sc = QShortcut(QKeySequence(str(n)), self)
            sc.activated.connect(lambda i=n - 1: self.toggle(i))
        for key, delta in (("Left", -1), ("Right", 1), ("Up", None), ("Down", None)):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda k=key, d=delta: self._move_focus(k, d))
        self._load_page()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        hints = [
            ("grid.confirm", "confirmar"),
            ("grid.toggle", "marcar"),
            ("grid.skip", "saltar página"),
            ("grid.undo", "deshacer página"),
        ]
        root.addWidget(self._top_bar("⚡ Cuadrícula", hints))
        info = QLabel(
            "Marca las fotos que SÍ tienen la etiqueta (clic o 1–9). Al confirmar, las no marcadas quedan como «no»."
        )
        info.setStyleSheet("color:#8888AA;font-size:11px;padding:6px 14px;")
        root.addWidget(info)
        body = QWidget()
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(10, 4, 10, 4)
        self.grid.setSpacing(8)
        cols = GRID_COLUMNS[self.grid_size]
        self.cells: list[GridCell] = []
        for i in range(self.grid_size):
            cell = GridCell(i)
            cell.clicked.connect(self.toggle)
            self.grid.addWidget(cell, i // cols, i % cols)
            self.cells.append(cell)
        root.addWidget(body, stretch=1)

        bottom = QWidget()
        bottom.setStyleSheet("background:#13131F;border-top:1px solid #2D2D3F;")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(14, 8, 14, 8)
        self.page_lbl = QLabel("")
        self.page_lbl.setStyleSheet("color:#8888AA;font-size:12px;")
        bl.addWidget(self.page_lbl, stretch=1)
        k = self.keymap
        for text, color, slot in (
            (f"⤼  Saltar página   {display_keys(k['grid.skip'])}", config.COLORS["text_dim"], self.skip_page),
            (f"✓  Confirmar   {display_keys(k['grid.confirm'])}", config.COLORS["success"], self.confirm),
        ):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(_BIG_BTN.format(c=color))
            b.clicked.connect(slot)
            bl.addWidget(b)
        root.addWidget(bottom)

    # ── Página ────────────────────────────────────────────────────────────────

    def page_photos(self) -> list[Photo]:
        return [c.photo for c in self.cells if c.photo is not None]

    def marked_ids(self) -> list[int]:
        return [c.photo.id for c in self.cells if c.photo is not None and c.marked]

    def _load_page(self, marked: frozenset[int] = frozenset()) -> None:
        self._thumb_keys.clear()
        self._thumbs.clear()
        total = self.photos.count()
        for i, cell in enumerate(self.cells):
            photo = self.photos.photo_at(self.page_start + i)
            cell.set_photo(photo)
            if photo is not None:
                cell.set_marked(photo.id in marked)
                key = f"grid:{photo.id}:{photo.mtime}"
                self._thumb_keys[key] = i
                self._thumbs.request(key, (photo, config.THUMB_SIZE_LARGE))
        # Precarga: la página siguiente queda en el caché de disco
        for r in range(self.page_start + self.grid_size, self.page_start + 2 * self.grid_size):
            nxt = self.photos.photo_at(r)
            if nxt is not None:
                self._thumbs.request(f"pre:{nxt.id}:{nxt.mtime}", (nxt, config.THUMB_SIZE_LARGE))
        # Las de la página actual van primero (LIFO): pedirlas de nuevo las sube
        for key, i in reversed(list(self._thumb_keys.items())):
            photo = self.cells[i].photo
            if photo is not None:
                self._thumbs.request(key, (photo, config.THUMB_SIZE_LARGE))
        self.focus_index = 0
        self._update_focus()
        page = self.page_start // self.grid_size + 1
        pages = max(1, -(-total // self.grid_size))
        self.page_lbl.setText(
            f"Página {min(page, pages):,} / {pages:,}  ·  fotos {min(self.page_start + 1, total):,}–"
            f"{min(self.page_start + self.grid_size, total):,} de {total:,}"
        )
        self.progress_lbl.setText(self._counts_text())
        if not self.page_photos():
            text = (
                "No hay fotos pendientes para esta etiqueta." if total == 0 else "¡Listo! " + self.summary()
            )
            self.page_lbl.setText(text + "  Esc para salir.")

    def _on_thumb(self, key: str, img: QImage) -> None:
        i = self._thumb_keys.get(key)
        if i is not None and not img.isNull():
            self.cells[i].set_pixmap(QPixmap.fromImage(img))

    # ── Acciones ──────────────────────────────────────────────────────────────

    def toggle(self, index: int) -> None:
        if 0 <= index < len(self.cells) and self.cells[index].photo is not None:
            self.cells[index].set_marked(not self.cells[index].marked)
            self.focus_index = index
            self._update_focus()

    def toggle_all(self) -> None:
        cells = [c for c in self.cells if c.photo is not None]
        new = not all(c.marked for c in cells)
        for c in cells:
            c.set_marked(new)

    def _move_focus(self, key: str, delta: int | None) -> None:
        cols = GRID_COLUMNS[self.grid_size]
        step = delta if delta is not None else (-cols if key == "Up" else cols)
        n = len(self.page_photos())
        if n:
            self.focus_index = max(0, min(n - 1, self.focus_index + step))
            self._update_focus()

    def _update_focus(self) -> None:
        for i, c in enumerate(self.cells):
            c.set_focused(i == self.focus_index)

    def confirm(self) -> None:
        photos = self.page_photos()
        if not photos:
            return
        yes = self.marked_ids()
        no = [p.id for p in photos if p.id not in yes]
        states = services.review_answer(self.tag.id, yes, no)
        self._record(_Step(self.page_start, states, yes=len(yes), no=len(no), marked=frozenset(yes)))
        self.page_start += self.grid_size
        self._load_page()

    def skip_page(self) -> None:
        photos = self.page_photos()
        if not photos:
            return
        self._record(_Step(self.page_start, {}, skipped=len(photos)))
        self.page_start += self.grid_size
        self._load_page()

    def undo(self) -> None:
        step = self._pop_step()
        if step is not None:
            self.page_start = step.row
            self._load_page(marked=step.marked)  # vuelve con las mismas marcas

    def done(self, result: int) -> None:
        disconnect_all(self._thumbs.loaded)
        retire_thread(self._thumbs)
        super().done(result)
