"""
PhotoVault - main.py
App de escritorio para gestionar y etiquetar fotos.
"""

import sys
import os
from pathlib import Path
from threading import Thread

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QScrollArea, QGridLayout,
    QFileDialog, QDialog, QCheckBox, QFrame, QProgressBar,
    QSizePolicy, QMessageBox, QComboBox, QColorDialog, QSplitter,
    QGroupBox, QStackedWidget, QTreeWidget, QTreeWidgetItem, QSpinBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer, QRunnable, QThreadPool, pyqtSlot
from PyQt6.QtGui import QPixmap, QIcon, QColor, QPainter, QFont, QFontDatabase

import database as db
import indexer

# ─── Hilo para indexación ─────────────────────────────────────────────────────

class IndexWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, int, int)
    error    = pyqtSignal(str)

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            added, skipped, errors = indexer.index_folder(
                self.folder,
                progress_callback=lambda cur, total, path: self.progress.emit(cur, total, path)
            )
            self.finished.emit(added, skipped, errors)
        except Exception as e:
            self.error.emit(str(e))


# ─── Hilo para cargar miniaturas ─────────────────────────────────────────────

class ThumbnailLoader(QThread):
    loaded = pyqtSignal(int, QPixmap)  # photo_id, pixmap

    def __init__(self, photos):
        super().__init__()
        self.photos = photos
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for photo in self.photos:
            if self._stop:
                break
            try:
                if photo.get("media_type") == "video":
                    from indexer import extract_video_thumbnail
                    jpeg_bytes = extract_video_thumbnail(photo["path"])
                    if jpeg_bytes:
                        pix = QPixmap()
                        pix.loadFromData(jpeg_bytes)
                        if not pix.isNull():
                            pix = pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                            self.loaded.emit(photo["id"], pix)
                else:
                    pix = QPixmap(photo["path"])
                    if not pix.isNull():
                        pix = pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                        self.loaded.emit(photo["id"], pix)
            except Exception:
                pass
            self.msleep(5)

# ─── Widget de miniatura ──────────────────────────────────────────────────────

class PhotoThumbnail(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, photo_id: int, filename: str, is_video: bool = False,
                 duration: float = None, parent=None):
        super().__init__(parent)
        self.photo_id = photo_id
        self.is_video = is_video
        self.setFixedSize(210, 230)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame {
                background: #1E1E2E;
                border-radius: 8px;
                border: 1px solid #2D2D3F;
            }
            QFrame:hover {
                border: 1px solid #4A9EFF;
                background: #252538;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        # Contenedor de imagen con overlay de video
        img_container = QWidget()
        img_container.setFixedSize(200, 200)
        img_container.setStyleSheet("background: #13131F; border-radius: 6px;")
        img_inner = QVBoxLayout(img_container)
        img_inner.setContentsMargins(0, 0, 0, 0)

        self.img_label = QLabel()
        self.img_label.setFixedSize(200, 200)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background: transparent;")
        img_inner.addWidget(self.img_label)

        # Ícono ▶ y duración encima para videos
        if is_video:
            play_overlay = QLabel("▶", img_container)
            play_overlay.setStyleSheet("""
                color: white;
                font-size: 28px;
                background: rgba(0,0,0,0.55);
                border-radius: 20px;
                padding: 4px 8px;
            """)
            play_overlay.adjustSize()
            play_overlay.move(
                (200 - play_overlay.width()) // 2,
                (200 - play_overlay.height()) // 2
            )

            if duration:
                mins = int(duration) // 60
                secs = int(duration) % 60
                dur_lbl = QLabel(f"{mins}:{secs:02d}", img_container)
                dur_lbl.setStyleSheet("""
                    color: white;
                    font-size: 10px;
                    background: rgba(0,0,0,0.7);
                    border-radius: 3px;
                    padding: 1px 5px;
                """)
                dur_lbl.adjustSize()
                dur_lbl.move(200 - dur_lbl.width() - 6, 200 - dur_lbl.height() - 6)

        layout.addWidget(img_container)

        name_label = QLabel(filename[:22] + "…" if len(filename) > 22 else filename)
        name_label.setStyleSheet("color: #8888AA; font-size: 10px;")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

    def set_pixmap(self, pix: QPixmap):
        self.img_label.setPixmap(pix)

    def mousePressEvent(self, event):
        self.clicked.emit(self.photo_id)


# ─── Dialog: Ver/editar foto ──────────────────────────────────────────────────

class PhotoDetailDialog(QDialog):
    tags_changed = pyqtSignal()

    def __init__(self, photo_id: int, path: str, media_type: str = "image", parent=None):
        super().__init__(parent)
        self.photo_id   = photo_id
        self.path       = path
        self.media_type = media_type
        self.setWindowTitle("Detalle")
        self.setMinimumSize(900, 600)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._load_tags()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Lado izquierdo: imagen o panel de video
        if self.media_type == "video":
            left = QWidget()
            left.setMinimumWidth(500)
            left_layout = QVBoxLayout(left)
            left_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # Miniatura del video
            from indexer import extract_video_thumbnail
            thumb_bytes = extract_video_thumbnail(self.path, size=480)
            thumb_lbl = QLabel()
            thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if thumb_bytes:
                pix = QPixmap()
                pix.loadFromData(thumb_bytes)
                thumb_lbl.setPixmap(pix)
            else:
                thumb_lbl.setText("🎬")
                thumb_lbl.setStyleSheet("font-size:64px;")
            left_layout.addWidget(thumb_lbl)

            btn_play = QPushButton("▶  Reproducir en Windows Media Player")
            btn_play.setStyleSheet("""
                QPushButton {
                    background: #4A9EFF22;
                    color: #4A9EFF;
                    border: 1px solid #4A9EFF;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-size: 14px;
                }
                QPushButton:hover { background: #4A9EFF44; }
            """)
            btn_play.clicked.connect(self._open_video)
            left_layout.addWidget(btn_play)
            layout.addWidget(left)
        else:
            self.img_label = QLabel()
            self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.img_label.setMinimumWidth(500)
            pix = QPixmap(self.path)
            if not pix.isNull():
                pix = pix.scaled(560, 560, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                self.img_label.setPixmap(pix)
            layout.addWidget(self.img_label)

        # Panel derecho de etiquetas
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(QLabel(f"<b>{Path(self.path).name}</b>"))

        right.addWidget(QLabel("Etiquetas:"))
        self.tags_container = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidget(self.tags_container)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(80)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(scroll)

        right.addWidget(QLabel("Agregar etiqueta:"))
        add_row = QHBoxLayout()
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.setPlaceholderText("Buscar o nueva etiqueta…")
        add_row.addWidget(self.tag_combo)
        btn_add = QPushButton("＋ Agregar")
        btn_add.clicked.connect(self._add_tag)
        add_row.addWidget(btn_add)
        right.addLayout(add_row)
        right.addStretch()
        layout.addLayout(right)

    def _open_video(self):
        import subprocess
        subprocess.Popen(["start", "", self.path], shell=True)

    def _load_tags(self):
        # Limpiar
        for i in reversed(range(self.tags_layout.count())):
            self.tags_layout.itemAt(i).widget().deleteLater()

        # Etiquetas actuales de la foto
        current = db.get_photo_tags(self.photo_id)
        for tag in current:
            chip = self._make_chip(tag["id"], tag["name"], tag["color"])
            self.tags_layout.addWidget(chip)

        # Poblar combo con todas las etiquetas
        all_tags = db.get_all_tags()
        self.tag_combo.clear()
        for t in all_tags:
            self.tag_combo.addItem(t["name"], userData=t["id"])

    def _make_chip(self, tag_id: int, name: str, color: str):
        btn = QPushButton(f"{name}  ✕")
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color}33;
                color: {color};
                border: 1px solid {color};
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background: {color}66; }}
        """)
        btn.clicked.connect(lambda _, tid=tag_id: self._remove_tag(tid))
        return btn

    def _add_tag(self):
        text = self.tag_combo.currentText().strip().lower()
        if not text:
            return
        tag_id = db.create_tag(text)
        db.add_tag_to_photo(self.photo_id, tag_id)
        self._load_tags()
        self.tags_changed.emit()

    def _remove_tag(self, tag_id: int):
        db.remove_tag_from_photo(self.photo_id, tag_id)
        self._load_tags()
        self.tags_changed.emit()



# ─── Dialog: Configuración ────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, current_page_size: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de visualización")
        self.setFixedSize(420, 220)
        self.setStyleSheet(DARK_STYLE)
        self.page_size = current_page_size
        self._build_ui()

    def _build_ui(self):
        from PyQt6.QtWidgets import QSlider, QSpinBox
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        layout.addWidget(QLabel("<b>Configuración de visualización</b>"))

        # Fotos por página
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Fotos por página:"))
        self.spin_page = QSpinBox()
        self.spin_page.setRange(10, 500)
        self.spin_page.setSingleStep(10)
        self.spin_page.setValue(self.page_size)
        self.spin_page.setFixedWidth(80)
        row1.addWidget(self.spin_page)
        row1.addStretch()
        layout.addLayout(row1)

        info = QLabel("💡 Menos fotos por página = carga más rápida.\nRecomendado: 50-100 para colecciones grandes.")
        info.setStyleSheet("color:#666; font-size:11px;")
        layout.addWidget(info)

        layout.addStretch()

        # Botones
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Aplicar")
        btn_ok.setStyleSheet("background:#4A9EFF22; color:#4A9EFF; border:1px solid #4A9EFF;")
        btn_ok.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _apply(self):
        self.page_size = self.spin_page.value()
        self.accept()



# ─── Dialog: Gestionar categorías ────────────────────────────────────────────

class CategoryManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestionar categorías")
        self.setMinimumSize(480, 500)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(QLabel("<b>Gestionar categorías de etiquetas</b>"))

        # Crear nueva categoría
        box = QGroupBox("Nueva categoría")
        form = QHBoxLayout(box)
        self.new_cat_edit = QLineEdit()
        self.new_cat_edit.setPlaceholderText("Nombre de la nueva categoría…")
        btn_create = QPushButton("＋ Crear")
        btn_create.clicked.connect(self._create_category)
        form.addWidget(self.new_cat_edit)
        form.addWidget(btn_create)
        layout.addWidget(box)

        # Lista de categorías existentes
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(6)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, stretch=1)

        info = QLabel("💡 Al eliminar una categoría, sus etiquetas se mueven a \'general\'.")
        info.setStyleSheet("color:#666; font-size:10px;")
        layout.addWidget(info)

    def _refresh(self):
        for i in reversed(range(self.vbox.count())):
            w = self.vbox.itemAt(i).widget()
            if w: w.deleteLater()

        categories = db.get_all_categories()
        for cat in categories:
            tags = db.get_tags_by_category(cat)
            count = len(tags)

            row = QWidget()
            row.setStyleSheet("background:#1E1E2E; border-radius:6px;")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(8, 6, 8, 6)
            hl.setSpacing(6)

            # Nombre editable inline
            name_edit = QLineEdit(cat)
            name_edit.setStyleSheet("background:#13131F; border:1px solid #3A3A5A; border-radius:4px; padding:3px 6px;")
            name_edit.setFixedWidth(140)
            hl.addWidget(name_edit)

            # Contador de etiquetas
            count_lbl = QLabel(f"{count} etiqueta{'s' if count != 1 else ''}")
            count_lbl.setStyleSheet("color:#666; font-size:11px;")
            hl.addWidget(count_lbl, stretch=1)

            # Preview de colores
            preview = QWidget()
            preview_hl = QHBoxLayout(preview)
            preview_hl.setContentsMargins(0,0,0,0)
            preview_hl.setSpacing(2)
            for tag in tags[:6]:
                dot = QLabel("●")
                dot.setStyleSheet(f"color:{tag['color']}; font-size:10px;")
                preview_hl.addWidget(dot)
            if count > 6:
                preview_hl.addWidget(QLabel(f"+{count-6}"))
            hl.addWidget(preview)

            # Botón renombrar
            btn_rename = QPushButton("✎")
            btn_rename.setFixedSize(28, 28)
            btn_rename.setToolTip("Renombrar categoría")
            btn_rename.setStyleSheet("color:#4A9EFF; border:1px solid #4A9EFF; border-radius:4px;")
            btn_rename.clicked.connect(lambda _, old=cat, edit=name_edit: self._rename(old, edit.text()))
            hl.addWidget(btn_rename)

            # Botón eliminar
            btn_del = QPushButton("✕")
            btn_del.setFixedSize(28, 28)
            btn_del.setToolTip("Eliminar categoría (mueve etiquetas a general)")
            btn_del.setStyleSheet("color:#FF4A4A; border:1px solid #FF4A4A; border-radius:4px;")
            btn_del.clicked.connect(lambda _, c=cat: self._delete(c))
            hl.addWidget(btn_del)

            self.vbox.addWidget(row)

        self.vbox.addStretch()

    def _create_category(self):
        name = self.new_cat_edit.text().strip().lower()
        if not name:
            return
        created = db.create_category(name)
        if created:
            self.new_cat_edit.clear()
            self._refresh()
        else:
            QMessageBox.information(self, "Ya existe", f"La categoría \"{name}\" ya existe.")

    def _rename(self, old_name: str, new_name: str):
        new_name = new_name.strip().lower()
        if not new_name or new_name == old_name:
            return
        db.rename_category(old_name, new_name)
        self._refresh()

    def _delete(self, cat_name: str):
        tags = db.get_tags_by_category(cat_name)
        msg = f"¿Eliminar la categoría \"{cat_name}\"?"
        if tags:
            msg += f"\n\nSus {len(tags)} etiqueta(s) se moverán a 'general'."
        reply = QMessageBox.question(self, "Confirmar", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_category(cat_name)
            self._refresh()


# ─── Dialog: Gestionar etiquetas ─────────────────────────────────────────────

class TagManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestionar etiquetas")
        self.setMinimumSize(500, 500)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Crear nueva etiqueta
        box = QGroupBox("Nueva etiqueta")
        form = QHBoxLayout(box)
        self.new_name   = QLineEdit(); self.new_name.setPlaceholderText("Nombre")
        self.new_cat    = QComboBox(); self.new_cat.setEditable(True)
        self.new_cat.setPlaceholderText("Categoría")
        self.new_cat.addItems(db.get_all_categories())
        self.new_color  = QPushButton("Color"); self.new_color.setFixedWidth(60)
        self._color_val = "#4A9EFF"
        self.new_color.setStyleSheet(f"background:{self._color_val};")
        self.new_color.clicked.connect(self._pick_color)
        btn_create = QPushButton("Crear")
        btn_create.clicked.connect(self._create)
        for w in [self.new_name, self.new_cat, self.new_color, btn_create]:
            form.addWidget(w)
        layout.addWidget(box)

        # Botón para gestionar categorías
        btn_cats = QPushButton("📂  Gestionar categorías")
        btn_cats.setStyleSheet("color:#4A9EFF; border:1px solid #4A9EFF;")
        btn_cats.clicked.connect(self._open_category_manager)
        layout.addWidget(btn_cats)

        # Tabla de etiquetas
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QVBoxLayout(self.container)
        self.grid.setSpacing(4)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color_val), self)
        if c.isValid():
            self._color_val = c.name()
            self.new_color.setStyleSheet(f"background:{self._color_val};")

    def _create(self):
        name = self.new_name.text().strip()
        if not name:
            return
        cat = self.new_cat.currentText().strip().lower() or "general"
        db.create_tag(name, cat, self._color_val)
        self.new_name.clear()
        # Refrescar el combo de categorías
        self.new_cat.clear()
        self.new_cat.addItems(db.get_all_categories())
        self._refresh()

    def _refresh(self):
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w:
                w.deleteLater()

        for tag in db.get_all_tags():
            row = QWidget()
            hl  = QHBoxLayout(row)
            hl.setContentsMargins(4, 2, 4, 2)

            dot = QLabel("●")
            dot.setStyleSheet(f"color:{tag['color']}; font-size:16px;")
            hl.addWidget(dot)

            lbl = QLabel(f"<b>{tag['name']}</b>  <span style='color:#666;'>[{tag['category']}]</span>")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            hl.addWidget(lbl, stretch=1)

            chk_hide_photos = QCheckBox("Ocultar fotos")
            chk_hide_photos.setChecked(bool(tag["hidden"]))
            chk_hide_photos.setToolTip("Las fotos con esta etiqueta no aparecen en la galería")
            chk_hide_photos.stateChanged.connect(lambda state, tid=tag["id"]:
                db.set_tag_hidden(tid, bool(state)))
            hl.addWidget(chk_hide_photos)

            chk_hide_sidebar = QCheckBox("Ocultar del sidebar")
            chk_hide_sidebar.setChecked(bool(tag["sidebar_hidden"]))
            chk_hide_sidebar.setToolTip("La etiqueta no aparece en el panel de filtros")
            chk_hide_sidebar.stateChanged.connect(lambda state, tid=tag["id"]:
                db.set_tag_sidebar_hidden(tid, bool(state)))
            hl.addWidget(chk_hide_sidebar)

            btn_del = QPushButton("Eliminar")
            btn_del.setFixedWidth(70)
            btn_del.setStyleSheet("color:#FF4A4A; border:1px solid #FF4A4A;")
            btn_del.clicked.connect(lambda _, tid=tag["id"]: self._delete(tid))
            hl.addWidget(btn_del)

            row.setStyleSheet("background:#1E1E2E; border-radius:6px;")
            self.grid.addWidget(row)

        self.grid.addStretch()

    def _delete(self, tag_id: int):
        db.delete_tag(tag_id)
        self._refresh()

    def _open_category_manager(self):
        dlg = CategoryManagerDialog(self)
        dlg.exec()
        self._refresh()


# ─── Dialog: Indexar carpeta ──────────────────────────────────────────────────

class IndexDialog(QDialog):
    indexing_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indexar carpeta")
        self.setFixedSize(500, 200)
        self.setStyleSheet(DARK_STYLE)
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Ruta de la carpeta…")
        btn_browse = QPushButton("Examinar")
        btn_browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit)
        row.addWidget(btn_browse)
        layout.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status_lbl = QLabel("Listo para indexar.")
        self.status_lbl.setStyleSheet("color:#8888AA; font-size:10px;")
        layout.addWidget(self.status_lbl)

        self.btn_start = QPushButton("▶  Iniciar indexación")
        self.btn_start.clicked.connect(self._start)
        layout.addWidget(self.btn_start)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta")
        if folder:
            self.path_edit.setText(folder)

    def _start(self):
        folder = self.path_edit.text().strip()
        if not folder or not Path(folder).exists():
            QMessageBox.warning(self, "Error", "Selecciona una carpeta válida.")
            return
        self.btn_start.setEnabled(False)
        self.worker = IndexWorker(folder)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, cur, total, path):
        self.progress.setValue(int(cur / total * 100))
        self.status_lbl.setText(f"[{cur}/{total}] {Path(path).name}")

    def _on_finished(self, added, skipped, errors):
        self.status_lbl.setText(f"✓ Listo: {added} indexadas, {errors} errores.")
        self.btn_start.setEnabled(True)
        self.indexing_done.emit()

    def _on_error(self, msg):
        QMessageBox.critical(self, "Error", msg)
        self.btn_start.setEnabled(True)





# ─── Dialog: Des-indexar carpetas ─────────────────────────────────────────────

class DeindexDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Des-indexar carpetas")
        self.setMinimumSize(560, 420)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("<b>Carpetas indexadas</b>"))
        layout.addWidget(QLabel("Selecciona una carpeta para eliminar sus registros de la base de datos.\nLos archivos originales NO se borran del disco."))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(4)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        layout.addWidget(sep)

        btn_missing = QPushButton("🧹  Eliminar registros de archivos que ya no existen en disco")
        btn_missing.setStyleSheet("color:#FFD700; border:1px solid #FFD700;")
        btn_missing.clicked.connect(self._remove_missing)
        layout.addWidget(btn_missing)

    def _refresh(self):
        for i in reversed(range(self.vbox.count())):
            w = self.vbox.itemAt(i).widget()
            if w: w.deleteLater()

        folders = self._get_folders()
        if not folders:
            lbl = QLabel("No hay carpetas indexadas.")
            lbl.setStyleSheet("color:#666;")
            self.vbox.addWidget(lbl)
        else:
            for folder, count in folders:
                row = QWidget()
                row.setStyleSheet("background:#1E1E2E; border-radius:6px;")
                hl = QHBoxLayout(row)
                hl.setContentsMargins(8, 6, 8, 6)
                lbl = QLabel(f"<b>{folder}</b>  <span style=\'color:#666;\'>{count:,} archivos</span>")
                lbl.setTextFormat(Qt.TextFormat.RichText)
                hl.addWidget(lbl, stretch=1)
                btn = QPushButton("Eliminar")
                btn.setFixedWidth(75)
                btn.setStyleSheet("color:#FF4A4A; border:1px solid #FF4A4A;")
                btn.clicked.connect(lambda _, f=folder: self._deindex_folder(f))
                hl.addWidget(btn)
                self.vbox.addWidget(row)
        self.vbox.addStretch()

    def _get_folders(self):
        """Devuelve lista de (carpeta_raiz, cantidad) agrupando por directorio padre común."""
        with db.get_connection() as conn:
            rows = conn.execute("SELECT path FROM photos ORDER BY path").fetchall()
        from collections import Counter
        folder_counts = Counter()
        for row in rows:
            folder_counts[str(Path(row[0]).parent)] += 1
        # Agrupar: encontrar carpetas únicas de primer nivel
        result = {}
        for folder, count in folder_counts.items():
            p = Path(folder)
            # Buscar el ancestro más alto que siga siendo una subcarpeta de alguna carpeta indexada
            result[folder] = result.get(folder, 0) + count
        # Simplificar: mostrar solo directorios únicos ordenados
        return sorted(result.items(), key=lambda x: x[0])

    def _deindex_folder(self, folder: str):
        reply = QMessageBox.question(
            self, "Confirmar",
            f"¿Eliminar todos los registros de:\n{folder}\n\nLos archivos originales no se borrarán.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        with db.get_connection() as conn:
            conn.execute("DELETE FROM photos WHERE path LIKE ?", (folder + "%",))
        self._refresh()

    def _remove_missing(self):
        with db.get_connection() as conn:
            rows = conn.execute("SELECT id, path FROM photos").fetchall()
        missing = [(row[0],) for row in rows if not Path(row[1]).exists()]
        if not missing:
            QMessageBox.information(self, "Listo", "No se encontraron archivos faltantes.")
            return
        reply = QMessageBox.question(
            self, "Confirmar",
            f"Se eliminarán {len(missing)} registros de archivos que ya no existen en disco.\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        with db.get_connection() as conn:
            conn.executemany("DELETE FROM photos WHERE id = ?", missing)
        QMessageBox.information(self, "Listo", f"Se eliminaron {len(missing)} registros.")
        self._refresh()

# ─── Ventana principal ────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoVault")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(DARK_STYLE)

        self._active_tags: list[int] = []
        self._offset    = 0
        self._page_size = 100
        self._total     = 0
        self._thumbnails: dict[int, PhotoThumbnail] = {}
        self._active_loaders: list = []
        self._loader: ThumbnailLoader = None
        self._current_photos = []
        self._pending_photos: list = []   # cola de widgets por crear
        self._view_mode = 'gallery'  # 'gallery' or 'folders'
        self._folder_filter: str = None
        self._folder_loader: ThumbnailLoader = None
        self._folder_thumbnails: dict = {}
        self._folder_photos: list = []
        self._build_timer = QTimer(self)  # timer para agregar widgets poco a poco
        self._build_timer.setInterval(0)  # lo más rápido posible pero sin bloquear
        self._build_timer.timeout.connect(self._add_next_batch)
        self._grid_cols = 4

        db.init_db()
        self._build_ui()
        self._refresh_tags()
        QTimer.singleShot(100, self._load_photos)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background:#13131F; border-right:1px solid #2D2D3F;")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(10, 16, 10, 16)
        sb_layout.setSpacing(8)

        logo = QLabel("📸 PhotoVault")
        logo.setStyleSheet("font-size:18px; font-weight:bold; color:#4A9EFF; margin-bottom:8px;")
        sb_layout.addWidget(logo)

        # Mode toggle buttons
        mode_row = QHBoxLayout()
        self.btn_gallery_mode = QPushButton("🖼  Galería")
        self.btn_gallery_mode.setCheckable(True)
        self.btn_gallery_mode.setChecked(True)
        self.btn_gallery_mode.clicked.connect(self._show_gallery)
        self.btn_folder_mode = QPushButton("📁  Carpetas")
        self.btn_folder_mode.setCheckable(True)
        self.btn_folder_mode.clicked.connect(self._show_explorer)
        for b in [self.btn_gallery_mode, self.btn_folder_mode]:
            b.setStyleSheet("""QPushButton{background:#1E1E2E;border:1px solid #3A3A5A;border-radius:6px;padding:5px;}
                QPushButton:checked{background:#4A9EFF33;border-color:#4A9EFF;color:#4A9EFF;}
                QPushButton:hover{border-color:#4A9EFF;}""")
        mode_row.addWidget(self.btn_gallery_mode)
        mode_row.addWidget(self.btn_folder_mode)
        sb_layout.addLayout(mode_row)

        btn_index = QPushButton("＋ Indexar carpeta")
        btn_index.clicked.connect(self._open_index_dialog)
        sb_layout.addWidget(btn_index)


        btn_tags = QPushButton("🏷  Gestionar etiquetas")
        btn_tags.clicked.connect(self._open_tag_manager)
        sb_layout.addWidget(btn_tags)

        btn_deindex = QPushButton("🗂  Des-indexar carpetas")
        btn_deindex.clicked.connect(self._open_deindex_dialog)
        sb_layout.addWidget(btn_deindex)

        btn_settings = QPushButton("⚙  Configuración")
        btn_settings.clicked.connect(self._open_settings_dialog)
        sb_layout.addWidget(btn_settings)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        sb_layout.addWidget(sep)

        # Fila: label + botones colapsar/expandir
        tag_header_row = QHBoxLayout()
        tag_header_row.addWidget(QLabel("Filtrar por etiqueta:"))
        tag_header_row.addStretch()
        btn_expand_all = QPushButton("▾")
        btn_expand_all.setFixedSize(28, 28)
        btn_expand_all.setToolTip("Expandir todos los grupos")
        btn_expand_all.setStyleSheet("QPushButton{background:transparent;border:none;color:#6688AA;font-size:14px;} QPushButton:hover{color:#4A9EFF;}")
        btn_expand_all.clicked.connect(self._expand_all_groups)
        btn_collapse_all = QPushButton("▸")
        btn_collapse_all.setFixedSize(28, 28)
        btn_collapse_all.setToolTip("Colapsar todos los grupos")
        btn_collapse_all.setStyleSheet("QPushButton{background:transparent;border:none;color:#6688AA;font-size:14px;} QPushButton:hover{color:#4A9EFF;}")
        btn_collapse_all.clicked.connect(self._collapse_all_groups)
        tag_header_row.addWidget(btn_expand_all)
        tag_header_row.addWidget(btn_collapse_all)
        sb_layout.addLayout(tag_header_row)

        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_widget = QWidget()
        self.tag_vbox   = QVBoxLayout(self.tag_widget)
        self.tag_vbox.setContentsMargins(0, 0, 0, 0)
        self.tag_vbox.setSpacing(2)
        self.tag_scroll.setWidget(self.tag_widget)
        sb_layout.addWidget(self.tag_scroll, stretch=1)

        btn_clear = QPushButton("✕ Limpiar filtros")
        btn_clear.setStyleSheet("color:#FF4A4A;")
        btn_clear.clicked.connect(self._clear_filters)
        sb_layout.addWidget(btn_clear)

        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#666; font-size:10px;")
        sb_layout.addWidget(self.stats_lbl)

        root.addWidget(sidebar)

        # ── Área principal (stacked: galería | explorador) ───────────────
        self.stack = QStackedWidget()

        # ── Vista 0: Galería ──────────────────────────────────────────────
        gallery_page = QWidget()
        gallery_layout = QVBoxLayout(gallery_page)
        gallery_layout.setContentsMargins(12, 12, 12, 12)
        gallery_layout.setSpacing(8)

        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Buscar por nombre…")
        self.search_edit.textChanged.connect(self._on_search)
        top_bar.addWidget(self.search_edit)
        self.count_lbl = QLabel("0 fotos")
        self.count_lbl.setStyleSheet("color:#8888AA;")
        top_bar.addWidget(self.count_lbl)
        gallery_layout.addLayout(top_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(8)
        self.scroll_area.setWidget(self.grid_widget)
        gallery_layout.addWidget(self.scroll_area, stretch=1)

        pg_bar = QHBoxLayout()
        self.prev_btn = QPushButton("← Anterior")
        self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn = QPushButton("Siguiente →")
        self.next_btn.clicked.connect(self._next_page)
        self.page_lbl = QLabel("")
        self.page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pg_bar.addWidget(self.prev_btn)
        pg_bar.addWidget(self.page_lbl, stretch=1)
        pg_bar.addWidget(self.next_btn)
        gallery_layout.addLayout(pg_bar)

        self.stack.addWidget(gallery_page)  # index 0

        # ── Vista 1: Explorador de carpetas ───────────────────────────────
        explorer_page = QWidget()
        explorer_layout = QVBoxLayout(explorer_page)
        explorer_layout.setContentsMargins(0, 0, 0, 0)
        explorer_layout.setSpacing(0)

        # Barra superior del explorador
        exp_top = QWidget()
        exp_top.setStyleSheet("background:#13131F; border-bottom:1px solid #2D2D3F;")
        exp_top_hl = QHBoxLayout(exp_top)
        exp_top_hl.setContentsMargins(10, 6, 10, 6)
        btn_back_gallery = QPushButton("← Volver a galería")
        btn_back_gallery.setStyleSheet("color:#4A9EFF; border:none; background:transparent; font-size:12px;")
        btn_back_gallery.clicked.connect(self._show_gallery)
        exp_top_hl.addWidget(btn_back_gallery)
        self.exp_path_lbl = QLabel("Selecciona una carpeta")
        self.exp_path_lbl.setStyleSheet("color:#8888AA; font-size:11px;")
        exp_top_hl.addWidget(self.exp_path_lbl, stretch=1)
        self.exp_count_lbl = QLabel("")
        self.exp_count_lbl.setStyleSheet("color:#4A9EFF; font-size:11px;")
        exp_top_hl.addWidget(self.exp_count_lbl)
        explorer_layout.addWidget(exp_top)

        # Splitter árbol | grid
        exp_splitter = QSplitter(Qt.Orientation.Horizontal)
        exp_splitter.setStyleSheet("QSplitter::handle { background: #2D2D3F; width: 1px; }")

        # Árbol de carpetas
        tree_container = QWidget()
        tree_container.setFixedWidth(250)
        tree_container.setStyleSheet("background:#13131F;")
        tree_vbox = QVBoxLayout(tree_container)
        tree_vbox.setContentsMargins(0, 0, 0, 0)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setStyleSheet("""
            QTreeWidget { background:#13131F; border:none; color:#D0D0E8; font-size:12px; }
            QTreeWidget::item { padding:4px 6px; }
            QTreeWidget::item:selected { background:#4A9EFF33; color:#4A9EFF; }
            QTreeWidget::item:hover { background:#1E1E2E; }
        """)
        self.folder_tree.itemClicked.connect(self._on_folder_clicked)
        tree_vbox.addWidget(self.folder_tree)
        exp_splitter.addWidget(tree_container)

        # Grid del explorador
        exp_right = QWidget()
        exp_right_layout = QVBoxLayout(exp_right)
        exp_right_layout.setContentsMargins(8, 8, 8, 4)
        exp_right_layout.setSpacing(6)
        self.exp_scroll = QScrollArea()
        self.exp_scroll.setWidgetResizable(True)
        self.exp_grid_widget = QWidget()
        self.exp_grid_layout = QGridLayout(self.exp_grid_widget)
        self.exp_grid_layout.setSpacing(6)
        self.exp_scroll.setWidget(self.exp_grid_widget)
        exp_right_layout.addWidget(self.exp_scroll, stretch=1)

        # Paginación explorador
        exp_pg = QHBoxLayout()
        self.exp_prev_btn = QPushButton("← Anterior")
        self.exp_prev_btn.setEnabled(False)
        self.exp_prev_btn.clicked.connect(self._exp_prev_page)
        self.exp_next_btn = QPushButton("Siguiente →")
        self.exp_next_btn.setEnabled(False)
        self.exp_next_btn.clicked.connect(self._exp_next_page)
        self.exp_page_lbl = QLabel("")
        self.exp_page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.exp_page_lbl.setStyleSheet("color:#8888AA; font-size:11px;")
        exp_pg.addWidget(self.exp_prev_btn)
        exp_pg.addWidget(self.exp_page_lbl, stretch=1)
        exp_pg.addWidget(self.exp_next_btn)
        exp_right_layout.addLayout(exp_pg)
        exp_splitter.addWidget(exp_right)
        exp_splitter.setSizes([250, 800])
        explorer_layout.addWidget(exp_splitter, stretch=1)

        self.stack.addWidget(explorer_page)  # index 1
        root.addWidget(self.stack, stretch=1)

        # Estado paginación explorador
        self._exp_offset = 0
        self._exp_page_size = 80
        self._exp_total = 0

    # ── Etiquetas sidebar ─────────────────────────────────────────────────────

    def _refresh_tags(self):
        for i in reversed(range(self.tag_vbox.count())):
            w = self.tag_vbox.itemAt(i).widget()
            if w:
                w.deleteLater()

        all_tags = db.get_all_tags(include_sidebar_hidden=False)

        # Agrupar por categoría
        from collections import OrderedDict
        groups = OrderedDict()
        for tag in all_tags:
            cat = tag["category"] or "general"
            groups.setdefault(cat, []).append(tag)

        for category, tags in groups.items():
            # ── Encabezado de grupo (colapsable) ──────────────────────────
            header = QPushButton(f"▾  {category.upper()}")
            header.setCheckable(True)
            header.setChecked(True)
            header.setStyleSheet("""
                QPushButton {
                    background: #1A1A2E;
                    color: #6688AA;
                    border: none;
                    border-top: 1px solid #2D2D3F;
                    border-radius: 0;
                    text-align: left;
                    padding: 4px 6px;
                    font-size: 10px;
                    font-weight: bold;
                    letter-spacing: 1px;
                }
                QPushButton:hover { color: #88AACC; background: #1E1E2E; }
            """)

            # Contenedor de las etiquetas de este grupo
            group_widget = QWidget()
            group_vbox = QVBoxLayout(group_widget)
            group_vbox.setContentsMargins(8, 0, 0, 4)
            group_vbox.setSpacing(1)

            for tag in tags:
                row = QWidget()
                hl = QHBoxLayout(row)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)

                chk = QCheckBox(tag["name"])
                chk.setStyleSheet(f"color:{tag['color']};")
                chk.setProperty("tag_id", tag["id"])
                if tag["id"] in self._active_tags:
                    chk.setChecked(True)
                chk.stateChanged.connect(self._on_tag_filter_changed)
                hl.addWidget(chk, stretch=1)

                eye_btn = QPushButton("👁" if not tag["sidebar_hidden"] else "🚫")
                eye_btn.setFixedSize(22, 22)
                eye_btn.setToolTip("Ocultar del sidebar" if not tag["sidebar_hidden"] else "Mostrar en sidebar")
                eye_btn.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 11px; padding: 0; } QPushButton:hover { background: #2D2D3F; border-radius: 4px; }")
                eye_btn.clicked.connect(lambda _, tid=tag["id"], cur=tag["sidebar_hidden"]: self._toggle_sidebar_hidden(tid, cur))
                hl.addWidget(eye_btn)

                group_vbox.addWidget(row)

            # Conectar header para colapsar/expandir el grupo
            header.toggled.connect(lambda checked, gw=group_widget, btn=header: (
                gw.setVisible(checked),
                btn.setText(f"{'▾' if checked else '▸'}  {btn.text()[2:]}")
            ))

            self.tag_vbox.addWidget(header)
            self.tag_vbox.addWidget(group_widget)

        self.tag_vbox.addStretch()
        self._update_stats()

    def _toggle_sidebar_hidden(self, tag_id: int, currently_hidden: bool):
        db.set_tag_sidebar_hidden(tag_id, not currently_hidden)
        self._refresh_tags()

    def _on_tag_filter_changed(self):
        self._active_tags = []
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if w is None:
                continue
            # Buscar checkboxes dentro de group_widgets
            for row in w.findChildren(QCheckBox):
                if row.isChecked() and row.property("tag_id") is not None:
                    self._active_tags.append(row.property("tag_id"))
        self._offset = 0
        self._load_photos()

    def _clear_filters(self):
        self._active_tags = []
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if w is None:
                continue
            for chk in w.findChildren(QCheckBox):
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)
        self._offset = 0
        self._load_photos()

    # ── Carga de fotos ────────────────────────────────────────────────────────

    def _load_photos(self):
        # Detener timer y loader previos
        self._build_timer.stop()
        if self._loader is not None:
            try:
                self._loader.stop()
                self._loader.wait(1000)
            except RuntimeError:
                pass
            self._loader = None

        # Limpiar grid
        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._thumbnails.clear()

        hidden = db.get_hidden_tag_ids()
        search = self.search_edit.text().strip() or None

        self._total = db.get_photo_count(
            tag_ids=self._active_tags or None,
            hidden_tag_ids=hidden,
            search=search
        )
        self.count_lbl.setText(f"{self._total:,} fotos")
        self._update_pagination()

        photos = db.get_photos(
            tag_ids=self._active_tags or None,
            hidden_tag_ids=hidden,
            search=search,
            limit=self._page_size,
            offset=self._offset
        )
        self._current_photos = [dict(p) for p in photos]
        self._grid_cols = max(1, (self.scroll_area.width() - 30) // 218)

        # Encolar todos los widgets para crearlos en lotes
        self._pending_photos = list(enumerate(self._current_photos))
        self._build_timer.start()

    def _add_next_batch(self):
        """Agrega hasta 10 widgets por tick del timer — sin bloquear la UI."""
        BATCH = 10
        batch = self._pending_photos[:BATCH]
        self._pending_photos = self._pending_photos[BATCH:]

        for idx, photo in batch:
            thumb = PhotoThumbnail(
                photo["id"], photo["filename"],
                is_video=(photo.get("media_type") == "video"),
                duration=photo.get("duration")
            )
            thumb.clicked.connect(self._open_photo)
            self.grid_layout.addWidget(thumb, idx // self._grid_cols, idx % self._grid_cols)
            self._thumbnails[photo["id"]] = thumb

        if not self._pending_photos:
            self._build_timer.stop()
            # Iniciar carga de miniaturas solo cuando todos los widgets existen
            self._loader = ThumbnailLoader(self._current_photos)
            self._loader.loaded.connect(self._on_thumb_loaded)
            self._loader.finished.connect(self._on_loader_finished)
            self._active_loaders.append(self._loader)
            self._loader.start()

    def _on_loader_finished(self):
        loader = self.sender()
        if loader in self._active_loaders:
            self._active_loaders.remove(loader)
        self._loader = None

    def _on_thumb_loaded(self, photo_id: int, pix: QPixmap):
        if photo_id in self._thumbnails:
            self._thumbnails[photo_id].set_pixmap(pix)

    def _on_search(self):
        self._offset = 0
        self._load_photos()

    def _on_scroll(self, value):
        pass

    # ── Paginación ────────────────────────────────────────────────────────────

    def _update_pagination(self):
        page   = self._offset // self._page_size + 1
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        self.page_lbl.setText(f"Página {page} / {total_pages}")
        self.prev_btn.setEnabled(self._offset > 0)
        self.next_btn.setEnabled(self._offset + self._page_size < self._total)

    def _prev_page(self):
        self._offset = max(0, self._offset - self._page_size)
        self._load_photos()

    def _next_page(self):
        self._offset += self._page_size
        self._load_photos()

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _open_photo(self, photo_id: int):
        photo = next((p for p in self._current_photos if p["id"] == photo_id), None)
        if not photo:
            return
        dlg = PhotoDetailDialog(photo_id, photo["path"], photo.get("media_type", "image"), self)
        dlg.tags_changed.connect(self._load_photos)
        dlg.exec()

    def _expand_all_groups(self):
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if isinstance(w, QPushButton) and w.isCheckable():
                w.setChecked(True)

    def _collapse_all_groups(self):
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if isinstance(w, QPushButton) and w.isCheckable():
                w.setChecked(False)

    def _open_index_dialog(self):
        dlg = IndexDialog(self)
        dlg.indexing_done.connect(self._on_index_done)
        dlg.exec()

    def _on_index_done(self):
        self._refresh_tags()
        self._load_photos()

    def _open_tag_manager(self):
        dlg = TagManagerDialog(self)
        dlg.exec()
        self._refresh_tags()
        self._load_photos()

    def _open_folder_browser(self):
        self._show_explorer()

    def _show_gallery(self):
        self._view_mode = 'gallery'
        self.stack.setCurrentIndex(0)
        self.btn_gallery_mode.setChecked(True)
        self.btn_folder_mode.setChecked(False)

    def _show_explorer(self):
        self._view_mode = 'folders'
        self.stack.setCurrentIndex(1)
        self.btn_gallery_mode.setChecked(False)
        self.btn_folder_mode.setChecked(True)
        self._build_folder_tree()

    def _build_folder_tree(self):
        self.folder_tree.clear()
        with db.get_connection() as conn:
            rows = conn.execute("SELECT DISTINCT path FROM photos ORDER BY path").fetchall()
        tree = {}
        for row in rows:
            parts = Path(row[0]).parent.parts
            node = tree
            for part in parts:
                node = node.setdefault(part, {})

        def add_items(parent, subtree, full_path=""):
            for name, children in sorted(subtree.items()):
                if full_path == "" and name.endswith(":"):
                    fp = name + "\\"
                elif full_path == "":
                    fp = name
                else:
                    fp = str(Path(full_path) / name)
                item = QTreeWidgetItem([f"📁 {name}"])
                item.setData(0, Qt.ItemDataRole.UserRole, fp)
                if hasattr(parent, 'addChild'):
                    parent.addChild(item)
                else:
                    self.folder_tree.addTopLevelItem(item)
                add_items(item, children, fp)

        add_items(self.folder_tree, tree)
        self.folder_tree.expandToDepth(2)

    def _on_folder_clicked(self, item, col):
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder:
            self._folder_filter = folder
            self.exp_path_lbl.setText(folder)
            self._exp_offset = 0
            self._exp_load_photos()

    def _exp_load_photos(self):
        if not self._folder_filter:
            return
        # Stop previous loader safely
        if self._folder_loader is not None:
            try:
                self._folder_loader.stop()
                self._folder_loader.wait(1000)
            except RuntimeError:
                pass
            self._folder_loader = None

        # Clear grid
        for i in reversed(range(self.exp_grid_layout.count())):
            w = self.exp_grid_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._folder_thumbnails.clear()

        folder_filter = self._folder_filter.rstrip("/\\")
        with db.get_connection() as conn:
            self._exp_total = conn.execute(
                "SELECT COUNT(*) FROM photos WHERE path LIKE ?",
                (folder_filter + "%",)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, path, filename, media_type, duration FROM photos "
                "WHERE path LIKE ? ORDER BY path, filename LIMIT ? OFFSET ?",
                (folder_filter + "%", self._exp_page_size, self._exp_offset)
            ).fetchall()

        self._folder_photos = [dict(r) for r in rows]
        self.exp_count_lbl.setText(f"{self._exp_total:,} archivos")
        self._exp_update_pagination()

        cols = max(1, (self.exp_scroll.width() - 30) // 160)
        for idx, photo in enumerate(self._folder_photos):
            thumb = PhotoThumbnail(
                photo["id"], photo["filename"],
                is_video=(photo.get("media_type") == "video"),
                duration=photo.get("duration")
            )
            thumb.setFixedSize(150, 170)
            thumb.clicked.connect(self._exp_open_photo)
            self.exp_grid_layout.addWidget(thumb, idx // cols, idx % cols)
            self._folder_thumbnails[photo["id"]] = thumb

        self._folder_loader = ThumbnailLoader(self._folder_photos)
        self._folder_loader.loaded.connect(self._exp_on_thumb_loaded)
        self._folder_loader.finished.connect(self._exp_on_loader_finished)
        self._active_loaders.append(self._folder_loader)
        self._folder_loader.start()

    def _exp_on_loader_finished(self):
        loader = self.sender()
        if loader in self._active_loaders:
            self._active_loaders.remove(loader)
        self._folder_loader = None

    def _exp_on_thumb_loaded(self, photo_id, pix):
        if photo_id in self._folder_thumbnails:
            self._folder_thumbnails[photo_id].set_pixmap(pix)

    def _exp_open_photo(self, photo_id):
        photo = next((p for p in self._folder_photos if p["id"] == photo_id), None)
        if photo:
            dlg = PhotoDetailDialog(photo_id, photo["path"], photo.get("media_type", "image"), self)
            dlg.exec()

    def _exp_update_pagination(self):
        page = self._exp_offset // self._exp_page_size + 1
        total_pages = max(1, (self._exp_total + self._exp_page_size - 1) // self._exp_page_size)
        self.exp_page_lbl.setText(f"Página {page} / {total_pages}  ({self._exp_total:,} archivos)")
        self.exp_prev_btn.setEnabled(self._exp_offset > 0)
        self.exp_next_btn.setEnabled(self._exp_offset + self._exp_page_size < self._exp_total)

    def _exp_prev_page(self):
        self._exp_offset = max(0, self._exp_offset - self._exp_page_size)
        self._exp_load_photos()

    def _exp_next_page(self):
        self._exp_offset += self._exp_page_size
        self._exp_load_photos()

    def _open_settings_dialog(self):
        dlg = SettingsDialog(self._page_size, self)
        if dlg.exec():
            self._page_size = dlg.page_size
            self._offset = 0
            self._load_photos()

    def _open_deindex_dialog(self):
        dlg = DeindexDialog(self)
        dlg.exec()
        self._load_photos()

    def _update_stats(self):
        stats = db.get_stats()
        self.stats_lbl.setText(
            f"{stats['total_photos']:,} fotos  •  {stats['total_tags']} etiquetas"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(100, self._load_photos)


# ─── Estilos globales ─────────────────────────────────────────────────────────

DARK_STYLE = """
    * {
        font-family: 'Segoe UI', sans-serif;
        font-size: 13px;
    }
    QMainWindow, QDialog, QWidget {
        background: #0D0D1A;
        color: #D0D0E8;
    }
    QPushButton {
        background: #1E1E2E;
        color: #D0D0E8;
        border: 1px solid #3A3A5A;
        border-radius: 6px;
        padding: 6px 12px;
    }
    QPushButton:hover  { background: #2A2A3E; border-color: #4A9EFF; }
    QPushButton:pressed{ background: #4A9EFF33; }
    QPushButton:disabled { color: #444; border-color: #222; }
    QLineEdit, QComboBox {
        background: #1E1E2E;
        color: #D0D0E8;
        border: 1px solid #3A3A5A;
        border-radius: 6px;
        padding: 6px 10px;
    }
    QLineEdit:focus, QComboBox:focus { border-color: #4A9EFF; }
    QScrollArea { border: none; }
    QScrollBar:vertical {
        background: #13131F;
        width: 8px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: #3A3A5A;
        border-radius: 4px;
        min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #4A9EFF; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QProgressBar {
        background: #1E1E2E;
        border: 1px solid #3A3A5A;
        border-radius: 4px;
        text-align: center;
        color: #D0D0E8;
    }
    QProgressBar::chunk { background: #4A9EFF; border-radius: 4px; }
    QGroupBox {
        border: 1px solid #3A3A5A;
        border-radius: 6px;
        margin-top: 8px;
        padding-top: 8px;
        color: #8888AA;
        font-size: 11px;
    }
    QCheckBox { color: #D0D0E8; spacing: 6px; }
    QCheckBox::indicator {
        width: 14px; height: 14px;
        border: 1px solid #3A3A5A;
        border-radius: 3px;
        background: #1E1E2E;
    }
    QCheckBox::indicator:checked {
        background: #4A9EFF;
        border-color: #4A9EFF;
    }
    QLabel { color: #D0D0E8; }
    QComboBox QAbstractItemView {
        background: #1E1E2E;
        border: 1px solid #3A3A5A;
        selection-background-color: #4A9EFF33;
    }
"""


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("PhotoVault")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
