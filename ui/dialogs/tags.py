"""
PhotoVault - ui/dialogs/tags.py
Gestión de etiquetas y categorías.
"""

import html
import logging
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
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

import services
from models import Tag
from ui.style import DARK_STYLE
from ui.widgets import clear_layout

logger = logging.getLogger(__name__)


# ─── Dialog: Gestionar categorías ────────────────────────────────────────────

class CategoryManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestionar categorías")
        self.setMinimumSize(480, 500); self.setStyleSheet(DARK_STYLE)
        self._build_ui(); self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(10)
        layout.addWidget(QLabel("<b>Gestionar categorías de etiquetas</b>"))
        box = QGroupBox("Nueva categoría"); form = QHBoxLayout(box)
        self.new_cat_edit = QLineEdit()
        self.new_cat_edit.setPlaceholderText("Nombre…")
        btn = QPushButton("＋ Crear"); btn.clicked.connect(self._create)
        form.addWidget(self.new_cat_edit); form.addWidget(btn)
        layout.addWidget(box)
        self.scroll_box = QScrollArea(); self.scroll_box.setWidgetResizable(True)
        self.container = QWidget(); self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(6); self.scroll_box.setWidget(self.container)
        layout.addWidget(self.scroll_box, stretch=1)
        info = QLabel("💡 Al eliminar una categoría sus etiquetas se mueven a 'general'.")
        info.setStyleSheet("color:#666;font-size:10px;"); layout.addWidget(info)

    def _refresh(self):
        clear_layout(self.vbox)
        for cat in services.get_all_categories():
            tags  = services.get_tags_by_category(cat)
            count = len(tags)
            row   = QWidget()
            row.setStyleSheet("background:#1E1E2E;border-radius:6px;")
            hl = QHBoxLayout(row); hl.setContentsMargins(8,6,8,6); hl.setSpacing(6)
            edit = QLineEdit(cat)
            edit.setStyleSheet(
                "background:#13131F;border:1px solid #3A3A5A;border-radius:4px;padding:3px 6px;"
            )
            edit.setFixedWidth(140); hl.addWidget(edit)
            hl.addWidget(QLabel(f"{count} etiq."), stretch=1)
            btn_r = QPushButton("✎"); btn_r.setFixedSize(28,28)
            btn_r.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;border-radius:4px;")
            btn_r.clicked.connect(lambda _, o=cat, e=edit: (
                services.rename_category(o, e.text()), self._refresh()
            ))
            hl.addWidget(btn_r)
            btn_d = QPushButton("✕"); btn_d.setFixedSize(28,28)
            btn_d.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;border-radius:4px;")
            btn_d.clicked.connect(lambda _, c=cat: self._delete(c))
            hl.addWidget(btn_d)
            self.vbox.addWidget(row)
        self.vbox.addStretch()

    def _create(self):
        name = self.new_cat_edit.text().strip().lower()
        if not name: return
        if services.create_category(name):
            self.new_cat_edit.clear(); self._refresh()
        else:
            QMessageBox.information(self, "Ya existe", f'La categoría "{name}" ya existe.')

    def _delete(self, cat_name: str):
        tags = services.get_tags_by_category(cat_name)
        msg  = f'¿Eliminar la categoría "{cat_name}"?'
        if tags: msg += f"\n\nSus {len(tags)} etiqueta(s) se moverán a 'general'."
        if QMessageBox.question(self, "Confirmar", msg,
           QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            services.delete_category(cat_name); self._refresh()


# ─── Dialog: Gestionar etiquetas ─────────────────────────────────────────────

class EditTagDialog(QDialog):
    """Editar nombre, categoría y color de una etiqueta existente."""

    def __init__(self, tag: Tag, parent=None):
        super().__init__(parent)
        self.tag    = tag
        self._color = tag.color
        self.setWindowTitle(f"Editar etiqueta: {tag.name}")
        self.setFixedSize(420, 220)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20); layout.setSpacing(10)

        layout.addWidget(QLabel("Nombre:"))
        self.name_edit = QLineEdit(tag.name)
        layout.addWidget(self.name_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Categoría:"))
        self.cat_combo = QComboBox(); self.cat_combo.setEditable(True)
        self.cat_combo.addItems(services.get_all_categories())
        self.cat_combo.setCurrentText(tag.category)
        row.addWidget(self.cat_combo, stretch=1)
        self.color_btn = QPushButton("Color"); self.color_btn.setFixedWidth(70)
        self.color_btn.setStyleSheet(f"background:{self._color};")
        self.color_btn.clicked.connect(self._pick_color)
        row.addWidget(self.color_btn)
        layout.addLayout(row)

        layout.addStretch()
        btns = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar"); btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Guardar")
        btn_save.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_save.clicked.connect(self._save)
        btns.addWidget(btn_cancel); btns.addWidget(btn_save)
        layout.addLayout(btns)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self)
        if c.isValid():
            self._color = c.name()
            self.color_btn.setStyleSheet(f"background:{self._color};")

    def _save(self):
        try:
            services.update_tag(self.tag.id, self.name_edit.text(),
                                self.cat_combo.currentText(), self._color)
        except ValueError as e:   # incluye TagNameConflictError
            QMessageBox.warning(self, "No se pudo guardar", str(e))
            return
        self.accept()


class TagManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestionar etiquetas")
        self.setMinimumSize(520, 560); self.setStyleSheet(DARK_STYLE)
        self._build_ui(); self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(12,12,12,12); layout.setSpacing(8)

        box  = QGroupBox("Nueva etiqueta"); form = QHBoxLayout(box)
        self.new_name  = QLineEdit(); self.new_name.setPlaceholderText("Nombre")
        self.new_cat   = QComboBox(); self.new_cat.setEditable(True)
        self.new_cat.addItems(services.get_all_categories())
        self.new_color = QPushButton("Color"); self.new_color.setFixedWidth(60)
        self._color    = "#4A9EFF"
        self.new_color.setStyleSheet(f"background:{self._color};")
        self.new_color.clicked.connect(self._pick_color)
        btn_cr = QPushButton("Crear"); btn_cr.clicked.connect(self._create)
        for w in [self.new_name, self.new_cat, self.new_color, btn_cr]:
            form.addWidget(w)
        layout.addWidget(box)

        btns_row = QHBoxLayout()
        btn_cats = QPushButton("📂  Gestionar categorías")
        btn_cats.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_cats.clicked.connect(self._open_cats)
        btns_row.addWidget(btn_cats)

        # ── FEATURE: exportar/importar JSON ──────────────────────────────
        btn_exp = QPushButton("⬆  Exportar JSON")
        btn_exp.setStyleSheet("color:#4AFFC3;border:1px solid #4AFFC3;")
        btn_exp.clicked.connect(self._export)
        btns_row.addWidget(btn_exp)

        btn_imp = QPushButton("⬇  Importar JSON")
        btn_imp.setStyleSheet("color:#4AFFC3;border:1px solid #4AFFC3;")
        btn_imp.clicked.connect(self._import)
        btns_row.addWidget(btn_imp)
        layout.addLayout(btns_row)

        self.scroll_box = QScrollArea(); self.scroll_box.setWidgetResizable(True)
        self.container = QWidget(); self.grid = QVBoxLayout(self.container)
        self.grid.setSpacing(4); self.scroll_box.setWidget(self.container)
        layout.addWidget(self.scroll_box)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self)
        if c.isValid():
            self._color = c.name()
            self.new_color.setStyleSheet(f"background:{self._color};")

    def _create(self):
        name = self.new_name.text().strip()
        if not name: return
        cat = self.new_cat.currentText().strip().lower() or "general"
        services.create_tag(name, cat, self._color)
        self.new_name.clear()
        self.new_cat.clear(); self.new_cat.addItems(services.get_all_categories())
        self._refresh()

    def _refresh(self):
        clear_layout(self.grid)
        for tag in services.get_all_tags():
            row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(4,2,4,2)
            dot = QLabel("●"); dot.setStyleSheet(f"color:{tag.color};font-size:16px;")
            hl.addWidget(dot)
            lbl = QLabel(f"<b>{html.escape(tag.name)}</b>  "
                         f"<span style='color:#666;'>[{html.escape(tag.category)}]</span>")
            lbl.setTextFormat(Qt.TextFormat.RichText); hl.addWidget(lbl, stretch=1)
            btn_e = QPushButton("✎"); btn_e.setFixedSize(28, 28)
            btn_e.setToolTip("Editar nombre, categoría y color")
            btn_e.setStyleSheet("color:#4A9EFF;border:1px solid #4A9EFF;border-radius:4px;padding:0;")
            btn_e.clicked.connect(lambda _, t=tag: self._edit(t))
            hl.addWidget(btn_e)
            chk1 = QCheckBox("Ocultar fotos"); chk1.setChecked(tag.hidden)
            chk1.stateChanged.connect(lambda s, tid=tag.id: services.set_tag_hidden(tid, bool(s)))
            hl.addWidget(chk1)
            chk2 = QCheckBox("Ocultar sidebar"); chk2.setChecked(tag.sidebar_hidden)
            chk2.stateChanged.connect(lambda s, tid=tag.id: services.set_tag_sidebar_hidden(tid, bool(s)))
            hl.addWidget(chk2)
            btn_d = QPushButton("Eliminar"); btn_d.setFixedWidth(70)
            btn_d.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;")
            btn_d.clicked.connect(lambda _, t=tag: self._delete(t))
            hl.addWidget(btn_d)
            row.setStyleSheet("background:#1E1E2E;border-radius:6px;")
            self.grid.addWidget(row)
        self.grid.addStretch()

    def _edit(self, tag: Tag):
        if EditTagDialog(tag, self).exec():
            self._refresh()

    def _delete(self, tag: Tag):
        n = services.count_photos_with_tag(tag.id)
        msg = f"¿Eliminar la etiqueta '{tag.name}'?"
        if n:
            msg += f"\n\nSe quitará de {n:,} foto{'s' if n != 1 else ''}."
        if QMessageBox.question(
            self, "Confirmar", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            services.delete_tag(tag.id)
            self._refresh()

    def _open_cats(self):
        CategoryManagerDialog(self).exec(); self._refresh()

    def _export(self):
        default = f"photovault_etiquetas_{datetime.now():%Y-%m-%d}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar etiquetas y asignaciones", default, "JSON (*.json)"
        )
        if not path: return
        try:
            s = services.export_tags(path)
            QMessageBox.information(
                self, "Exportado",
                f"Se exportaron {s.tags:,} etiquetas y las asignaciones de "
                f"{s.assignments:,} fotos a:\n{path}\n\n"
                "Guarda este archivo fuera de este PC (USB, nube) como respaldo.",
            )
        except Exception as e:
            logger.exception("Error exportando etiquetas a %s", path)
            QMessageBox.critical(self, "Error", str(e))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar etiquetas", "", "JSON (*.json)"
        )
        if not path: return
        try:
            summary = services.read_export_summary(path)
            include = False
            if summary.assignments:
                answer = QMessageBox.question(
                    self, "Importar",
                    f"El archivo tiene {summary.tags:,} etiquetas y asignaciones para "
                    f"{summary.assignments:,} fotos.\n\n"
                    "¿Importar también las asignaciones?\n"
                    "(Solo se agregan etiquetas a las fotos; nunca se quita ninguna.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    return
                include = answer == QMessageBox.StandardButton.Yes
            r = services.import_tags(path, include_assignments=include)
            msg = f"Etiquetas creadas: {r.created:,}\nYa existían (omitidas): {r.skipped:,}"
            if include:
                msg += (f"\n\nFotos encontradas por ruta: {r.photos_matched:,}"
                        f"\nFotos encontradas por nombre y tamaño: {r.photos_by_name:,}"
                        f"\nFotos no encontradas: {r.photos_missing:,}"
                        f"\nAsignaciones agregadas: {r.pairs_added:,}")
                if r.photos_missing:
                    msg += ("\n\nSi las fotos no encontradas están en otra unidad o carpeta, "
                            "indexa esa carpeta o usa 'Reubicar' y vuelve a importar.")
            QMessageBox.information(self, "Importado", msg)
            self._refresh()
        except Exception as e:
            logger.exception("Error importando etiquetas desde %s", path)
            QMessageBox.critical(self, "Error al importar", str(e))
