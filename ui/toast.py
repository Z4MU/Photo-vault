"""
PhotoVault - ui/toast.py
Avisos breves abajo a la derecha que se van solos ("rutas copiadas",
"3 fotos nuevas"…). Para lo que no necesita que el usuario haga nada: no
bloquean como un QMessageBox. Clic para cerrar uno antes de tiempo.
"""

import logging

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

import config

logger = logging.getLogger(__name__)

KINDS = {
    "info": config.COLORS["accent"],
    "success": config.COLORS["success"],
    "warning": config.COLORS["warning"],
    "error": config.COLORS["danger"],
}
MAX_TOASTS = 4
MARGIN = 16
FADE_MS = 250


class Toast(QLabel):
    closed = pyqtSignal(object)

    def __init__(self, text: str, kind: str, ms: int, parent: QWidget, on_click=None):
        super().__init__(text, parent)
        self.on_click = on_click  # opcional: qué hacer al pulsarlo (si no, solo se cierra)
        color = KINDS.get(kind, KINDS["info"])
        self.kind = kind
        self.setTextFormat(Qt.TextFormat.PlainText)  # el texto puede traer nombres de archivo
        self.setWordWrap(True)
        self.setMaximumWidth(420)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QLabel{{background:{config.COLORS['panel_alt']};color:{config.COLORS['text']};"
            f"border:1px solid {color};border-left:4px solid {color};border-radius:8px;"
            "padding:10px 14px;font-size:12px;}"
        )
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._closing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(ms)
        self.adjustSize()

    def fade_in(self) -> None:
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._finish)
        self._anim.start()

    def _finish(self) -> None:
        self.closed.emit(self)
        self.hide()
        self.deleteLater()

    def mousePressEvent(self, event):
        if self.on_click is not None:
            callback, self.on_click = self.on_click, None
            callback()
        self.dismiss()


class ToastManager(QObject):
    """Apila los avisos sobre `host` (la ventana) y los reubica al cambiar su tamaño."""

    def __init__(self, host: QWidget, bottom_offset: int = 30):
        super().__init__(host)
        self.host = host
        self.bottom_offset = bottom_offset  # para no tapar la barra de estado
        self.toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(self, text: str, kind: str = "info", ms: int = 4000, on_click=None) -> Toast:
        logger.info("Aviso: %s", text)
        while len(self.toasts) >= MAX_TOASTS:
            old = self.toasts.pop(0)
            old.dismiss()
        toast = Toast(text, kind, ms, self.host, on_click)
        toast.closed.connect(self._on_closed)
        self.toasts.append(toast)
        toast.show()
        toast.raise_()
        toast.fade_in()
        self._layout()
        return toast

    def _on_closed(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
        self._layout()

    def _layout(self) -> None:
        y = self.host.height() - self.bottom_offset - MARGIN
        for toast in reversed(self.toasts):  # el más nuevo abajo
            toast.adjustSize()
            y -= toast.height()
            toast.move(self.host.width() - toast.width() - MARGIN, y)
            y -= 8

    def eventFilter(self, obj, event):
        if obj is self.host and event is not None and event.type() == QEvent.Type.Resize:
            self._layout()
        return False

    def clear(self) -> None:
        """Quita todos los avisos al instante (modo pánico)."""
        for toast in self.toasts:
            toast.hide()
            toast.deleteLater()
        self.toasts.clear()

    def texts(self) -> list[str]:
        return [t.text() for t in self.toasts]
