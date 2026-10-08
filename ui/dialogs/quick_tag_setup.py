"""
PhotoVault - ui/dialogs/quick_tag_setup.py
Configurar el etiquetado rápido: modo (sí / no, cuadrícula, varias
etiquetas), etiqueta a revisar y conjunto de fotos. Recuerda la última
configuración.
"""

import logging
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

import services
import smart
from services import GalleryQuery
from ui.dialogs.keymap import KeymapDialog
from ui.dialogs.review import GRID_SIZES
from ui.style import DARK_STYLE

logger = logging.getLogger(__name__)

MODES = [
    ("review", "Una etiqueta: sí / no", "Ves una foto a la vez y decides si tiene la etiqueta."),
    (
        "grid",
        "Una etiqueta: cuadrícula",
        "Ves varias a la vez y marcas las que sí. Lo más rápido para etiquetas poco comunes.",
    ),
    ("multi", "Varias etiquetas (atajos)", "Una foto a la vez; cada tecla pone o quita su etiqueta."),
]


ORDERS = [
    ("normal", "Normal (por fecha)"),
    ("tag", "🧠 Primero las que más se parecen a la etiqueta"),
    ("adult", "🧠 Primero las que parecen contenido adulto"),
]


class QuickTagSetupDialog(QDialog):
    """Al aceptar: `mode`, `tag_id`, `query` (lo que se va a recorrer) y `grid_size`."""

    def __init__(self, gallery_query: GalleryQuery | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Etiquetado rápido")
        self.setMinimumWidth(560)
        self.setStyleSheet(DARK_STYLE)
        self.mode = "review"
        self.tag_id: int | None = None
        self.query = GalleryQuery()
        self.grid_size = 12
        # Orden por parecido: solo con el modelo y fotos analizadas (fase 10)
        self._content_ready = smart.content_model_installed() and smart.analysis_status()[0] > 0
        self._scopes: list[tuple[str, str, GalleryQuery]] = [("all", "Toda la colección", GalleryQuery())]
        if gallery_query is not None:
            self._scopes.append(
                ("gallery", "Lo que muestra la galería ahora (con sus filtros)", gallery_query)
            )
        for name, q in services.BUILTIN_ALBUMS:
            self._scopes.append((f"album:{name}", f"Álbum: {name}", q))
        for s in services.list_saved_searches():
            self._scopes.append((f"saved:{s.id}", f"Búsqueda: {s.name}", GalleryQuery.from_dict(s.query)))
        self._build_ui()
        self._restore(services.get_quick_tag_setup())
        self._refresh()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        box_mode = QGroupBox("Modo")
        ml = QVBoxLayout(box_mode)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        for key, title, desc in MODES:
            rb = QRadioButton(title)
            rb.setToolTip(desc)
            self.mode_group.addButton(rb)
            self.mode_buttons[key] = rb
            ml.addWidget(rb)
            d = QLabel(desc)
            d.setStyleSheet("color:#8888AA;font-size:11px;margin-left:22px;")
            ml.addWidget(d)
        self.mode_buttons["review"].setChecked(True)
        lay.addWidget(box_mode)

        self.box_tag = QGroupBox("Etiqueta a revisar")
        tl = QHBoxLayout(self.box_tag)
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        paths = services.tag_path_names()
        for tag in sorted(services.get_all_tags(), key=lambda t: " › ".join(paths.get(t.id, [t.name]))):
            self.tag_combo.addItem(" › ".join(paths.get(tag.id, [tag.name])), tag.id)
        completer = self.tag_combo.completer()
        if completer is not None:
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.tag_combo.currentIndexChanged.connect(self._refresh)
        tl.addWidget(self.tag_combo, stretch=1)
        lay.addWidget(self.box_tag)

        box_scope = QGroupBox("¿Qué fotos?")
        sl = QVBoxLayout(box_scope)
        self.scope_combo = QComboBox()
        for key, label, _q in self._scopes:
            self.scope_combo.addItem(label, key)
        self.scope_combo.currentIndexChanged.connect(self._refresh)
        sl.addWidget(self.scope_combo)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Carpeta:"))
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("— cualquier carpeta —", None)
        for folder, count in services.get_indexed_folders():
            self.folder_combo.addItem(f"{Path(folder).name or folder}  ({count:,})", folder)
            self.folder_combo.setItemData(self.folder_combo.count() - 1, folder, Qt.ItemDataRole.ToolTipRole)
        self.folder_combo.currentIndexChanged.connect(self._refresh)
        frow.addWidget(self.folder_combo, stretch=1)
        sl.addLayout(frow)
        self.chk_untagged = QCheckBox("Solo fotos sin ninguna etiqueta")
        self.chk_untagged.toggled.connect(self._refresh)
        sl.addWidget(self.chk_untagged)
        self.chk_reviewed = QCheckBox("Volver a revisar también las ya respondidas")
        self.chk_reviewed.setToolTip(
            "Normalmente solo aparecen las que faltan: ni tienen la etiqueta ni se marcaron «no»."
        )
        self.chk_reviewed.toggled.connect(self._refresh)
        sl.addWidget(self.chk_reviewed)
        # Orden por parecido (fase 10): las que más probablemente sean «sí», primero
        orow = QHBoxLayout()
        self.order_lbl = QLabel("Orden:")
        orow.addWidget(self.order_lbl)
        self.order_combo = QComboBox()
        for key, label in ORDERS:
            self.order_combo.addItem(label, key)
        self.order_combo.setToolTip(
            "Con 🧠 aparecen primero las fotos que más se parecen (por contenido). "
            "Solo incluye las fotos ya analizadas (Configuración → IA)."
        )
        orow.addWidget(self.order_combo, stretch=1)
        sl.addLayout(orow)
        grow = QHBoxLayout()
        self.grid_lbl = QLabel("Fotos por página:")
        grow.addWidget(self.grid_lbl)
        self.grid_combo = QComboBox()
        for n in GRID_SIZES:
            self.grid_combo.addItem(str(n), n)
        self.grid_combo.setCurrentIndex(GRID_SIZES.index(12))
        grow.addWidget(self.grid_combo)
        grow.addStretch()
        sl.addLayout(grow)
        lay.addWidget(box_scope)

        self.count_lbl = QLabel("")
        self.count_lbl.setWordWrap(True)
        self.count_lbl.setStyleSheet("color:#4A9EFF;font-size:12px;")
        lay.addWidget(self.count_lbl)

        btns = QHBoxLayout()
        btn_keys = QPushButton("⌨  Teclas…")
        btn_keys.setToolTip("Cambiar las teclas de cada modo y de cada etiqueta")
        btn_keys.clicked.connect(lambda: KeymapDialog(self).exec())
        btns.addWidget(btn_keys)
        btns.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        self.btn_start = QPushButton("▶  Empezar")
        self.btn_start.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_start.clicked.connect(self._start)
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_start)
        lay.addLayout(btns)
        # Al final: conectar antes dispararía _refresh con la ventana a medio construir
        for rb in self.mode_buttons.values():
            rb.toggled.connect(self._refresh)
        self.order_combo.currentIndexChanged.connect(self._refresh)

    # ── Estado ────────────────────────────────────────────────────────────────

    def current_mode(self) -> str:
        return next((k for k, rb in self.mode_buttons.items() if rb.isChecked()), "review")

    def current_tag_id(self) -> int | None:
        idx = self.tag_combo.findText(self.tag_combo.currentText())
        return self.tag_combo.itemData(idx) if idx >= 0 else None

    def base_query(self) -> GalleryQuery:
        key = self.scope_combo.currentData()
        q = next((q for k, _l, q in self._scopes if k == key), GalleryQuery())
        folder = self.folder_combo.currentData()
        if folder:
            q = replace(q, folder=folder)
        if self.chk_untagged.isChecked():
            q = replace(q, untagged_only=True)
        return q

    def _ordered(self, base: GalleryQuery, tag_id: int) -> GalleryQuery:
        order = self.order_combo.currentData() if self._content_ready else "normal"
        if order == "tag":
            return replace(base, semantic=f"@etiqueta:{tag_id}")
        if order == "adult":
            return replace(base, semantic="@adulto")
        return base

    def final_query(self) -> GalleryQuery:
        base = self.base_query()
        tag_id = self.current_tag_id()
        if self.current_mode() == "multi" or tag_id is None:
            return base
        return services.review_query(self._ordered(base, tag_id), tag_id, self.chk_reviewed.isChecked())

    def _refresh(self, *_args) -> None:
        mode = self.current_mode()
        single = mode in ("review", "grid")
        self.box_tag.setVisible(single)
        self.chk_reviewed.setVisible(single)
        self.order_lbl.setVisible(single and self._content_ready)
        self.order_combo.setVisible(single and self._content_ready)
        self.grid_lbl.setVisible(mode == "grid")
        self.grid_combo.setVisible(mode == "grid")
        base = self.base_query()
        tag_id = self.current_tag_id()
        if single and tag_id is not None:
            base = self._ordered(base, tag_id)
        if single and tag_id is None:
            self.count_lbl.setText("Elige una etiqueta.")
            self.btn_start.setEnabled(False)
            return
        if not single:
            n = services.count_gallery(base)
            self.count_lbl.setText(f"{n:,} foto{'s' if n != 1 else ''} para etiquetar.")
        elif self.chk_reviewed.isChecked():
            n = services.count_gallery(base)
            self.count_lbl.setText(f"Se revisarán las {n:,} fotos del conjunto (también las ya respondidas).")
        else:
            st = services.review_stats(base, tag_id)  # type: ignore[arg-type]
            n = st.pending
            self.count_lbl.setText(
                f"Faltan {st.pending:,} de {st.total:,}: {st.tagged:,} ya tienen la etiqueta y "
                f"{st.rejected:,} se marcaron «no». Las respondidas no vuelven a aparecer."
            )
        self.btn_start.setEnabled(n > 0)
        self.adjustSize()

    def _restore(self, saved: dict) -> None:
        mode = saved.get("mode")
        if mode in self.mode_buttons:
            self.mode_buttons[mode].setChecked(True)
        for combo, key in (
            (self.tag_combo, "tag_id"),
            (self.scope_combo, "scope"),
            (self.folder_combo, "folder"),
        ):
            idx = combo.findData(saved.get(key))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self.chk_untagged.setChecked(bool(saved.get("untagged")))
        self.chk_reviewed.setChecked(bool(saved.get("include_reviewed")))
        idx = self.grid_combo.findData(saved.get("grid_size"))
        if idx >= 0:
            self.grid_combo.setCurrentIndex(idx)
        idx = self.order_combo.findData(saved.get("order"))
        if idx >= 0:
            self.order_combo.setCurrentIndex(idx)

    def _start(self) -> None:
        self.mode = self.current_mode()
        self.tag_id = self.current_tag_id()
        self.query = self.final_query()
        self.grid_size = self.grid_combo.currentData()
        services.set_quick_tag_setup(
            {
                "mode": self.mode,
                "tag_id": self.tag_id,
                "scope": self.scope_combo.currentData(),
                "folder": self.folder_combo.currentData(),
                "untagged": self.chk_untagged.isChecked(),
                "include_reviewed": self.chk_reviewed.isChecked(),
                "grid_size": self.grid_size,
                "order": self.order_combo.currentData(),
            }
        )
        self.accept()
