"""
PhotoVault - ui/privacy_actions.py
PrivacyMixin (fase 9): mostrar / bloquear el contenido oculto desde la
ventana principal, bloqueo automático (inactividad, minimizar) y modo pánico.
"""

import logging
from typing import cast

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtGui import QPixmapCache
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QPushButton, QWidget

import privacy
import services
from ui.dialogs.privacy import ensure_unlocked
from ui.privacy_guard import PrivacyGuard
from ui.sidebar import SavedSearchPanel
from ui.tag_panel import TagFilterPanel
from ui.toast import ToastManager
from ui.workers import TaskWorker, disconnect_all, retire_on_destroy, retire_thread

logger = logging.getLogger(__name__)

TOGGLE_HIDDEN_KEY = "Ctrl+Shift+H"
# Esperar a que terminen los bucles de los diálogos cerrados antes de cerrar la app
_PANIC_CLOSE_DELAY_MS = 50


class PrivacyMixin:
    """Lo usa MainWindow (necesita reload_keep_position, tag_panel, toast…)."""

    tag_panel: TagFilterPanel
    saved_panel: SavedSearchPanel
    toasts: ToastManager
    _panic_close = False
    _secure_worker: TaskWorker | None = None

    def reload_keep_position(self) -> None:
        raise NotImplementedError

    def toast(self, text: str, kind: str = "info", ms: int = 4000) -> None:
        raise NotImplementedError

    def _update_stats(self) -> None:
        raise NotImplementedError

    def _window(self) -> QWidget:
        return cast(QWidget, self)

    def _init_privacy(self, status_bar) -> None:
        self.btn_hidden = QPushButton("")
        self.btn_hidden.setFlat(True)
        self.btn_hidden.clicked.connect(self.toggle_hidden_content)
        status_bar.addPermanentWidget(self.btn_hidden)
        self.privacy_guard = PrivacyGuard(self._window())
        self.privacy_guard.panic.connect(self._on_panic_key)
        self.privacy_guard.idle.connect(self._on_idle)
        self.apply_privacy_options()
        self._update_hidden_button()
        self._secure_hidden_thumbnails()

    def _secure_hidden_thumbnails(self) -> None:
        """
        Al abrir: borrar miniaturas sin cifrar de fotos ocultas que hayan quedado
        (de antes de la fase 9). En un hilo: si falta el mtime en la DB, mira el
        archivo, y un disco USB dormido tarda segundos en responder.
        """
        w = TaskWorker(lambda progress_callback=None, should_stop=None: services.secure_hidden_thumbnails())
        w.completed.connect(self._on_hidden_secured)
        self._secure_worker = w
        w.start()
        retire_on_destroy(self._window(), w)

    def _on_hidden_secured(self, _removed) -> None:
        retire_thread(self._secure_worker)
        self._secure_worker = None

    def _shutdown_privacy(self) -> None:
        """Al cerrar la ventana."""
        self.privacy_guard.uninstall()
        privacy.lock()
        if self._secure_worker is not None:
            disconnect_all(self._secure_worker.completed)
            retire_thread(self._secure_worker)
            self._secure_worker = None

    def apply_privacy_options(self) -> None:
        opts = privacy.get_options()
        self.privacy_guard.set_panic_key(opts.panic_key)
        self.privacy_guard.set_idle_minutes(opts.autolock_minutes)

    def _update_hidden_button(self) -> None:
        unlocked = privacy.is_unlocked()
        self.btn_hidden.setText("🔓 Oculto visible" if unlocked else "🔒")
        self.btn_hidden.setToolTip(
            f"El contenido oculto está visible: clic para bloquearlo ({TOGGLE_HIDDEN_KEY})"
            if unlocked
            else f"Mostrar el contenido oculto ({TOGGLE_HIDDEN_KEY})"
        )
        self.btn_hidden.setStyleSheet(
            "QPushButton{color:#FFD700;border:1px solid #FFD700;border-radius:8px;padding:1px 8px;}"
            if unlocked
            else "QPushButton{color:#8888AA;border:none;padding:1px 6px;}"
        )

    def toggle_hidden_content(self) -> None:
        if privacy.is_unlocked():
            self.lock_hidden_content()
        elif ensure_unlocked(self._window()):
            self.privacy_changed()
            self.toast(
                f"🔓 Contenido oculto visible. {TOGGLE_HIDDEN_KEY} o 🔓 abajo para bloquearlo.", "warning"
            )

    def lock_hidden_content(self, reason: str = "", close_windows: bool = True) -> None:
        """Bloquea lo oculto: cierra el visor y los diálogos (pueden estar mostrándolo) y recarga."""
        was_unlocked = privacy.is_unlocked()
        privacy.lock()
        if close_windows:
            self._close_other_windows()
        if was_unlocked:
            self.privacy_changed()
            self.toast(f"🔒 Contenido oculto bloqueado{f' ({reason})' if reason else ''}", "info")

    def privacy_changed(self) -> None:
        """Se bloqueó, desbloqueó, o se creó/quitó el PIN: todo lo que muestra la ventana cambia."""
        QPixmapCache.clear()  # miniaturas de lo oculto que quedaron en memoria
        self._update_hidden_button()
        self.tag_panel.refresh()
        self.saved_panel.mark_dirty()
        self._update_stats()
        self.reload_keep_position()

    def _close_other_windows(self) -> None:
        for w in QApplication.topLevelWidgets():
            if w is self or not w.isVisible():
                continue
            if isinstance(w, QDialog):
                w.reject()
            elif isinstance(w, QMenu):
                w.close()

    # ── Modo pánico y bloqueo automático ──────────────────────────────────────

    def _on_panic_key(self) -> None:
        # Fuera del filtro de eventos: cerrar ventanas desde ahí dentro no es seguro
        QTimer.singleShot(0, self.panic)

    def panic(self) -> None:
        action = privacy.get_options().panic_action
        logger.info("Modo pánico (%s)", action)
        self.lock_hidden_content(close_windows=True)
        self.toasts.clear()
        if action == "close":
            self._panic_close = True
            QTimer.singleShot(_PANIC_CLOSE_DELAY_MS, self._window().close)
        elif action == "minimize":
            self._window().showMinimized()

    def _on_idle(self) -> None:
        if privacy.is_unlocked():
            self.lock_hidden_content(reason="sin uso")

    def _privacy_change_event(self, event) -> None:
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self._window().isMinimized()
            and privacy.is_unlocked()
            and privacy.get_options().lock_on_minimize
        ):
            self.lock_hidden_content(reason="ventana minimizada")
