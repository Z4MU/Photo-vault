"""
PhotoVault - ui/privacy_guard.py
Filtro de eventos de toda la app (fase 9): detecta la tecla de pánico en
cualquier ventana (también en el visor, el etiquetado rápido o un diálogo
modal) y cuánto tiempo pasó sin usar PhotoVault (bloqueo automático).
"""

import time

from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QApplication

IDLE_CHECK_MS = 15_000

_ACTIVITY_EVENTS = {
    QEvent.Type.KeyPress,
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseMove,
    QEvent.Type.Wheel,
}


def key_event_text(event: QKeyEvent) -> str:
    """La tecla (con modificadores) en el formato de QKeySequence ("Ctrl+Shift+P", "F12")."""
    return QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)


class PrivacyGuard(QObject):
    panic = pyqtSignal()
    idle = pyqtSignal()  # una vez por cada período sin actividad

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panic_key = ""
        self._idle_seconds = 0
        self._last_activity = time.monotonic()
        self._idle_sent = False
        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_CHECK_MS)
        self._timer.timeout.connect(self.check_idle)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def set_panic_key(self, key: str) -> None:
        # Normalizar ("ctrl+shift+p" → "Ctrl+Shift+P")
        self._panic_key = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText) if key else ""

    def set_idle_minutes(self, minutes: int) -> None:
        self._idle_seconds = max(0, minutes) * 60
        self.touch()
        if self._idle_seconds:
            self._timer.start()
        else:
            self._timer.stop()

    def touch(self) -> None:
        self._last_activity = time.monotonic()
        self._idle_sent = False

    def check_idle(self) -> None:
        if (
            self._idle_seconds
            and not self._idle_sent
            and time.monotonic() - self._last_activity >= self._idle_seconds
        ):
            self._idle_sent = True
            self.idle.emit()

    def uninstall(self) -> None:
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt)
        etype = event.type()
        if etype in _ACTIVITY_EVENTS:
            self.touch()
        if self._panic_key and etype in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            if (
                isinstance(event, QKeyEvent)
                and not event.isAutoRepeat()
                and key_event_text(event) == self._panic_key
            ):
                if etype == QEvent.Type.ShortcutOverride:
                    # Que ningún atajo de la ventana se quede con la tecla: llega como KeyPress
                    event.accept()
                    return True
                self.panic.emit()
                return True
        return False
