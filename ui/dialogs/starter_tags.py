"""
PhotoVault - ui/dialogs/starter_tags.py
Etiquetas de ejemplo (fase 11): el usuario elige qué grupos genéricos agregar
(services.STARTER_TAGS). Solo crea las que no existan.
"""

from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

import services
from ui.style import DARK_STYLE

DEFAULT_GROUPS = ("tipo", "personas", "temas")


class StarterTagsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Etiquetas de ejemplo")
        self.setStyleSheet(DARK_STYLE)
        self.setMinimumWidth(460)
        self.created = 0
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        intro = QLabel(
            "Elige qué grupos de etiquetas agregar para empezar. Las que ya tengas no se "
            "duplican, y después puedes renombrarlas o borrarlas."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.checks: dict[str, QCheckBox] = {}
        for group, text in services.STARTER_DESCRIPTIONS.items():
            chk = QCheckBox(text)
            chk.setChecked(group in DEFAULT_GROUPS)
            self.checks[group] = chk
            lay.addWidget(chk)
        row = QHBoxLayout()
        row.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Agregar")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._add)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        lay.addLayout(row)

    def selected_groups(self) -> list[str]:
        return [g for g, chk in self.checks.items() if chk.isChecked()]

    def _add(self) -> None:
        self.created = services.add_starter_tags(self.selected_groups())
        self.accept()
