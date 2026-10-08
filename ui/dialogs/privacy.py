"""
PhotoVault - ui/dialogs/privacy.py
Contenido oculto (fase 9): pedir el PIN, crearlo, mostrar el código de
recuperación y la pestaña Privacidad de Configuración.
"""

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import privacy
from ui.style import DARK_STYLE

logger = logging.getLogger(__name__)

_HINT_STYLE = "color:#666;font-size:11px;"
_ERROR_STYLE = "color:#FF4A4A;font-size:11px;"


def _password_edit(placeholder: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText(placeholder)
    return edit


class PinDialog(QDialog):
    """Pide el PIN y desbloquea. Si se olvidó, ofrece el código de recuperación."""

    def __init__(self, parent=None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Contenido oculto")
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel("🔒 <b>Escribe el PIN para ver el contenido oculto</b>"))
        if reason:
            hint = QLabel(reason)
            hint.setWordWrap(True)
            hint.setStyleSheet(_HINT_STYLE)
            lay.addWidget(hint)
        self.edit = _password_edit("PIN")
        self.edit.returnPressed.connect(self._try)
        lay.addWidget(self.edit)
        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet(_ERROR_STYLE)
        lay.addWidget(self.error_lbl)
        row = QHBoxLayout()
        btn_forgot = QPushButton("¿Olvidaste el PIN?")
        btn_forgot.setFlat(True)
        btn_forgot.setStyleSheet("color:#8888AA;border:none;text-decoration:underline;")
        btn_forgot.clicked.connect(self._forgot)
        row.addWidget(btn_forgot)
        row.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("Desbloquear")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._try)
        row.addWidget(btn_cancel)
        row.addWidget(self.btn_ok)
        lay.addLayout(row)
        # Cuenta regresiva si hay que esperar por demasiados intentos
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_lockout)
        self._update_lockout()

    def _update_lockout(self) -> None:
        wait = privacy.lockout_remaining()
        self.btn_ok.setEnabled(wait == 0)
        self.edit.setEnabled(wait == 0)
        if wait:
            self.error_lbl.setText(f"Demasiados intentos. Espera {wait} s.")
            self._timer.start()
        else:
            self._timer.stop()
            if self.error_lbl.text().startswith("Demasiados"):
                self.error_lbl.setText("")
                self.edit.setFocus()

    def _try(self) -> None:
        try:
            ok = privacy.unlock(self.edit.text())
        except privacy.PrivacyError as e:
            self.error_lbl.setText(str(e))
            self._update_lockout()
            return
        if ok:
            self.accept()
            return
        self.edit.clear()
        self.error_lbl.setText("PIN incorrecto.")
        self._update_lockout()

    def _forgot(self) -> None:
        code, ok = QInputDialog.getText(
            self,
            "Código de recuperación",
            "Escribe el código de recuperación que se mostró al crear el PIN\n(p. ej. ABCDE-FGHJK-…):",
        )
        if not ok or not code.strip():
            return
        try:
            unlocked = privacy.unlock_with_recovery(code)
        except privacy.PrivacyError as e:
            self.error_lbl.setText(str(e))
            self._update_lockout()
            return
        if not unlocked:
            self.error_lbl.setText("Código de recuperación incorrecto.")
            self._update_lockout()
            return
        dlg = NewPinDialog(self, "Nuevo PIN", "Código correcto. Elige un PIN nuevo:")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            privacy.change_pin(dlg.pin)
        self.accept()


class NewPinDialog(QDialog):
    """PIN nuevo + confirmación. El resultado queda en `pin`."""

    def __init__(self, parent=None, title: str = "Crear PIN", text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(380)
        self.pin = ""
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        if text:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        self.edit1 = _password_edit(f"PIN (al menos {privacy.PIN_MIN_LENGTH} caracteres)")
        self.edit2 = _password_edit("Repite el PIN")
        self.edit2.returnPressed.connect(self._accept)
        lay.addWidget(self.edit1)
        lay.addWidget(self.edit2)
        hint = QLabel("Puede tener letras y números. Cuanto más largo, más difícil de adivinar.")
        hint.setStyleSheet(_HINT_STYLE)
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet(_ERROR_STYLE)
        lay.addWidget(self.error_lbl)
        row = QHBoxLayout()
        row.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Guardar")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        lay.addLayout(row)

    def _accept(self) -> None:
        pin = self.edit1.text()
        problem = privacy.pin_problem(pin)
        if problem is None and pin != self.edit2.text():
            problem = "Los dos PIN no coinciden."
        if problem:
            self.error_lbl.setText(problem)
            return
        self.pin = pin
        self.accept()


class RecoveryCodeDialog(QDialog):
    """Muestra el código de recuperación una vez; no se cierra con Aceptar hasta confirmar que se guardó."""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Código de recuperación")
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        info = QLabel(
            "Si olvidas el PIN, este código es la <b>única</b> forma de volver a ver el "
            "contenido oculto. Anótalo o guárdalo fuera de esta computadora: no se vuelve a mostrar."
        )
        info.setWordWrap(True)
        lay.addWidget(info)
        self.code_lbl = QLabel(code)
        self.code_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.code_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_lbl.setStyleSheet(
            "font-family:Consolas,monospace;font-size:18px;color:#FFD700;"
            "background:#1E1E2E;border:1px solid #3A3A5A;border-radius:6px;padding:10px;"
        )
        lay.addWidget(self.code_lbl)
        btn_copy = QPushButton("📋 Copiar")
        btn_copy.clicked.connect(lambda: _copy(code))
        lay.addWidget(btn_copy, alignment=Qt.AlignmentFlag.AlignLeft)
        self.chk_saved = QCheckBox("Ya lo guardé en un lugar seguro")
        lay.addWidget(self.chk_saved)
        self.btn_ok = QPushButton("Listo")
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self.accept)
        self.chk_saved.toggled.connect(self.btn_ok.setEnabled)
        lay.addWidget(self.btn_ok, alignment=Qt.AlignmentFlag.AlignRight)


def _copy(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def create_pin(parent) -> bool:
    """Pide un PIN nuevo, lo crea (queda desbloqueado) y muestra el código de recuperación."""
    dlg = NewPinDialog(
        parent,
        "Crear PIN",
        "Para ver el contenido oculto hace falta un PIN. Con el PIN, además, las miniaturas "
        "de lo oculto se guardan cifradas y sus etiquetas no aparecen mientras esté bloqueado.",
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    code = privacy.set_pin(dlg.pin)
    RecoveryCodeDialog(code, parent).exec()
    return True


def ensure_unlocked(parent, reason: str = "") -> bool:
    """True si el contenido oculto queda desbloqueado (pidiendo o creando el PIN)."""
    if privacy.is_unlocked():
        return True
    if not privacy.has_pin():
        return create_pin(parent)
    return PinDialog(parent, reason).exec() == QDialog.DialogCode.Accepted


class PrivacySettingsPanel(QWidget):
    """Pestaña Privacidad de Configuración. Las acciones del PIN se aplican al momento."""

    state_changed = pyqtSignal()  # se creó/quitó el PIN o se desbloqueó

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(10)

        lay.addWidget(QLabel("<b>PIN del contenido oculto</b>"))
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)
        row = QHBoxLayout()
        self.btn_create = QPushButton("🔑 Crear PIN…")
        self.btn_create.clicked.connect(self._create)
        self.btn_change = QPushButton("Cambiar PIN…")
        self.btn_change.clicked.connect(self._change)
        self.btn_code = QPushButton("Nuevo código…")
        self.btn_code.setToolTip("Genera otro código de recuperación (el anterior deja de servir)")
        self.btn_code.clicked.connect(self._new_code)
        self.btn_remove = QPushButton("Quitar PIN…")
        self.btn_remove.setStyleSheet("color:#FF4A4A;")
        self.btn_remove.clicked.connect(self._remove)
        for b in (self.btn_create, self.btn_change, self.btn_code, self.btn_remove):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        limits = QLabel(
            "Las fotos con una etiqueta marcada como oculta (🏷 Gestionar etiquetas) solo se ven "
            "después de escribir el PIN (Ctrl+Shift+H). Sus miniaturas se guardan cifradas.<br>"
            "⚠ Protege de miradas, no de un experto: la base de datos no está cifrada "
            "(rutas y nombres de etiquetas se pueden leer con otro programa)."
        )
        limits.setWordWrap(True)
        limits.setStyleSheet(_HINT_STYLE)
        lay.addWidget(limits)

        opts = privacy.get_options()
        lay.addWidget(QLabel("<b>Volver a bloquear</b>"))
        idle_row = QHBoxLayout()
        idle_row.addWidget(QLabel("Tras"))
        self.spin_idle = QSpinBox()
        self.spin_idle.setRange(0, 240)
        self.spin_idle.setSuffix(" min")
        self.spin_idle.setSpecialValueText("nunca")
        self.spin_idle.setValue(opts.autolock_minutes)
        idle_row.addWidget(self.spin_idle)
        idle_row.addWidget(QLabel("sin usar PhotoVault"))
        idle_row.addStretch()
        lay.addLayout(idle_row)
        self.chk_minimize = QCheckBox("Al minimizar la ventana")
        self.chk_minimize.setChecked(opts.lock_on_minimize)
        lay.addWidget(self.chk_minimize)

        lay.addWidget(QLabel("<b>Modo pánico</b>"))
        panic_info = QLabel(
            "Una tecla que, desde cualquier ventana de PhotoVault, bloquea lo oculto y cierra "
            "el visor y los demás diálogos al instante."
        )
        panic_info.setWordWrap(True)
        panic_info.setStyleSheet(_HINT_STYLE)
        lay.addWidget(panic_info)
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Tecla:"))
        self.key_edit = QKeySequenceEdit(QKeySequence(opts.panic_key))
        self.key_edit.setMaximumSequenceLength(1)
        self.key_edit.setClearButtonEnabled(True)
        self.key_edit.setToolTip("Pulsa la tecla o combinación. Vacía = sin modo pánico.")
        key_row.addWidget(self.key_edit, stretch=1)
        lay.addLayout(key_row)
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Además:"))
        self.combo_action = QComboBox()
        for value, text in privacy.PANIC_ACTIONS.items():
            self.combo_action.addItem(text, userData=value)
        self.combo_action.setCurrentIndex(max(0, self.combo_action.findData(opts.panic_action)))
        action_row.addWidget(self.combo_action, stretch=1)
        lay.addLayout(action_row)
        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet(_ERROR_STYLE)
        self.error_lbl.setWordWrap(True)
        lay.addWidget(self.error_lbl)
        lay.addStretch()
        self._refresh()

    def panic_key(self) -> str:
        return self.key_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def _refresh(self) -> None:
        has_pin = privacy.has_pin()
        if not has_pin:
            self.status_lbl.setText("Sin PIN: lo oculto no se puede ver dentro de la app.")
        elif privacy.is_unlocked():
            self.status_lbl.setText("🔓 PIN creado · ahora mismo el contenido oculto está <b>visible</b>.")
        else:
            self.status_lbl.setText("🔒 PIN creado · el contenido oculto está bloqueado.")
        self.btn_create.setVisible(not has_pin)
        for b in (self.btn_change, self.btn_code, self.btn_remove):
            b.setVisible(has_pin)

    def _create(self) -> None:
        if create_pin(self):
            self._changed()

    def _unlock_first(self) -> bool:
        was = privacy.is_unlocked()
        ok = ensure_unlocked(self, "Primero escribe el PIN actual.")
        if ok and not was:
            self._changed()
        return ok

    def _change(self) -> None:
        if not self._unlock_first():
            return
        dlg = NewPinDialog(self, "Cambiar PIN")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            privacy.change_pin(dlg.pin)
            QMessageBox.information(
                self, "PIN", "PIN cambiado. El código de recuperación sigue siendo el mismo."
            )

    def _new_code(self) -> None:
        if not self._unlock_first():
            return
        RecoveryCodeDialog(privacy.new_recovery_code(), self).exec()

    def _remove(self) -> None:
        if not self._unlock_first():
            return
        if (
            QMessageBox.question(
                self,
                "Quitar PIN",
                "Sin PIN, lo oculto seguirá oculto pero no habrá forma de verlo en la app "
                "(salvo creando otro PIN), y se borran sus miniaturas cifradas.\n\n¿Quitar el PIN?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        privacy.remove_pin()
        self._changed()

    def _changed(self) -> None:
        self._refresh()
        self.state_changed.emit()

    def validate(self) -> str | None:
        problem = privacy.panic_key_problem(self.panic_key())
        self.error_lbl.setText(f"Modo pánico: {problem}" if problem else "")
        return problem

    def apply(self) -> None:
        privacy.set_options(
            privacy.PrivacyOptions(
                autolock_minutes=self.spin_idle.value(),
                lock_on_minimize=self.chk_minimize.isChecked(),
                panic_key=self.panic_key(),
                panic_action=self.combo_action.currentData(),
            )
        )
