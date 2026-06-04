"""
PhotoVault - main.py
App de escritorio para gestionar y etiquetar fotos.

Cambios de arquitectura aplicados:
  - La UI importa `services` en lugar de `database` directamente.
  - Se usan modelos tipados (Photo, Tag, GalleryPage) en lugar de dicts/Row.
  - La lógica de "obtener fotos ocultas + filtrar + paginar" vive en services,
    no en la ventana principal.
  - ThumbnailLoader recibe Photo en lugar de dict.
  - closeEvent cierra la conexión DB del hilo principal correctamente.
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QScrollArea, QGridLayout,
    QFileDialog, QDialog, QCheckBox, QFrame, QProgressBar,
    QSizePolicy, QMessageBox, QComboBox, QColorDialog, QSplitter,
    QGroupBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QPixmap, QColor, QDesktopServices
from PyQt6.QtCore import QUrl

import database as db
import services
import indexer
import thumbnail_cache
from models import Photo, Tag, GalleryPage


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
        finally:
            db.close_connection()   # liberar conexión del hilo worker


# ─── Hilo para cargar miniaturas ─────────────────────────────────────────────

class ThumbnailLoader(QThread):
    loaded = pyqtSignal(int, QPixmap)

    def __init__(self, photos: list[Photo]):
        super().__init__()
        self.photos = photos
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        for photo in self.photos:
            if self._stop_flag:
                break
            try:
                if photo.is_video:
                    jpeg_bytes = thumbnail_cache.get_video_thumbnail(photo.path)
                else:
                    jpeg_bytes = thumbnail_cache.get_thumbnail(photo.path)

                if jpeg_bytes:
                    pix = QPixmap()
                    pix.loadFromData(jpeg_bytes)
                    if not pix.isNull():
                        if pix.width() > 200 or pix.height() > 200:
                            pix = pix.scaled(
                                200, 200,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        self.loaded.emit(photo.id, pix)
            except Exception:
                pass
            if not self._stop_flag:
                self.msleep(5)
        db.close_connection()   # liberar conexión del hilo loader


# ─── Widget de miniatura ──────────────────────────────────────────────────────

class PhotoThumbnail(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, photo: Photo, parent=None):
        super().__init__(parent)
        self.photo_id = photo.id
        self.setFixedSize(210, 230)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame { background:#1E1E2E; border-radius:8px; border:1px solid #2D2D3F; }
            QFrame:hover { border:1px solid #4A9EFF; background:#252538; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        img_container = QWidget()
        img_container.setFixedSize(200, 200)
        img_container.setStyleSheet("background:#13131F; border-radius:6px;")
        img_inner = QVBoxLayout(img_container)
        img_inner.setContentsMargins(0, 0, 0, 0)

        self.img_label = QLabel()
        self.img_label.setFixedSize(200, 200)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background:transparent;")
        img_inner.addWidget(self.img_label)

        if photo.is_video:
            play_overlay = QLabel("▶", img_container)
            play_overlay.setStyleSheet("""
                color:white; font-size:28px;
                background:rgba(0,0,0,0.55);
                border-radius:20px; padding:4px 8px;
            """)
            play_overlay.adjustSize()
            play_overlay.move(
                (200 - play_overlay.width()) // 2,
                (200 - play_overlay.height()) // 2,
            )
            if photo.duration_str:
                dur_lbl = QLabel(photo.duration_str, img_container)
                dur_lbl.setStyleSheet("""
                    color:white; font-size:10px;
                    background:rgba(0,0,0,0.7);
                    border-radius:3px; padding:1px 5px;
                """)
                dur_lbl.adjustSize()
                dur_lbl.move(200 - dur_lbl.width() - 6, 200 - dur_lbl.height() - 6)

        layout.addWidget(img_container)

        name_label = QLabel(photo.short_name)
        name_label.setStyleSheet("color:#8888AA; font-size:10px;")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

    def set_pixmap(self, pix: QPixmap):
        self.img_label.setPixmap(pix)

    def mousePressEvent(self, event):
        self.clicked.emit(self.photo_id)


# ─── Dialog: Ver/editar foto ──────────────────────────────────────────────────

class PhotoDetailDialog(QDialog):
    tags_changed = pyqtSignal()

    def __init__(self, photo: Photo, parent=None):
        super().__init__(parent)
        self.photo = photo
        self.setWindowTitle("Detalle")
        self.setMinimumSize(900, 600)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._load_tags()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        if self.photo.is_video:
            left = QWidget()
            left.setMinimumWidth(500)
            left_layout = QVBoxLayout(left)
            left_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            thumb_bytes = thumbnail_cache.get_video_thumbnail(self.photo.path, size=480)
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

            btn_play = QPushButton("▶  Reproducir")
            btn_play.setStyleSheet("""
                QPushButton {
                    background:#4A9EFF22; color:#4A9EFF;
                    border:1px solid #4A9EFF; border-radius:8px;
                    padding:10px 20px; font-size:14px;
                }
                QPushButton:hover { background:#4A9EFF44; }
            """)
            btn_play.clicked.connect(self._open_video)
            left_layout.addWidget(btn_play)
            layout.addWidget(left)
        else:
            self.img_label = QLabel()
            self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.img_label.setMinimumWidth(500)
            pix = QPixmap(self.photo.path)
            if not pix.isNull():
                pix = pix.scaled(560, 560, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                self.img_label.setPixmap(pix)
            layout.addWidget(self.img_label)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(QLabel(f"<b>{Path(self.photo.path).name}</b>"))

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
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.photo.path))

    def _load_tags(self):
        for i in reversed(range(self.tags_layout.count())):
            self.tags_layout.itemAt(i).widget().deleteLater()

        for tag in services.get_photo_tags(self.photo.id):
            self.tags_layout.addWidget(self._make_chip(tag))

        self.tag_combo.clear()
        for t in services.get_all_tags():
            self.tag_combo.addItem(t.name, userData=t.id)

    def _make_chip(self, tag: Tag) -> QPushButton:
        btn = QPushButton(f"{tag.name}  ✕")
        btn.setStyleSheet(f"""
            QPushButton {{
                background:{tag.color}33; color:{tag.color};
                border:1px solid {tag.color}; border-radius:10px;
                padding:2px 8px; font-size:11px;
            }}
            QPushButton:hover {{ background:{tag.color}66; }}
        """)
        btn.clicked.connect(lambda _, tid=tag.id: self._remove_tag(tid))
        return btn

    def _add_tag(self):
        text = self.tag_combo.currentText().strip().lower()
        if not text:
            return
        services.add_tag(self.photo.id, text)
        self._load_tags()
        self.tags_changed.emit()

    def _remove_tag(self, tag_id: int):
        services.remove_tag(self.photo.id, tag_id)
        self._load_tags()
        self.tags_changed.emit()


# ─── Dialog: Configuración ────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, current_page_size: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración de visualización")
        self.setFixedSize(420, 260)
        self.setStyleSheet(DARK_STYLE)
        self.page_size = current_page_size
        self._build_ui()

    def _build_ui(self):
        from PyQt6.QtWidgets import QSpinBox
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        layout.addWidget(QLabel("<b>Configuración de visualización</b>"))

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

        self.cache_lbl = QLabel()
        self._refresh_cache_label()
        self.cache_lbl.setStyleSheet("color:#8888AA; font-size:11px;")
        layout.addWidget(self.cache_lbl)

        btn_clear_cache = QPushButton("🗑  Limpiar caché de miniaturas")
        btn_clear_cache.setStyleSheet("color:#FFD700; border:1px solid #FFD700;")
        btn_clear_cache.clicked.connect(self._clear_cache)
        layout.addWidget(btn_clear_cache)

        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Aplicar")
        btn_ok.setStyleSheet("background:#4A9EFF22; color:#4A9EFF; border:1px solid #4A9EFF;")
        btn_ok.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _refresh_cache_label(self):
        mb = thumbnail_cache.cache_size_mb()
        self.cache_lbl.setText(f"Caché de miniaturas: {mb} MB")

    def _clear_cache(self):
        import shutil
        if thumbnail_cache.CACHE_DIR.exists():
            shutil.rmtree(thumbnail_cache.CACHE_DIR)
        self._refresh_cache_label()
        QMessageBox.information(self, "Caché limpiado",
            "El caché de miniaturas fue eliminado.\nSe regenerará al navegar la galería.")

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

        box = QGroupBox("Nueva categoría")
        form = QHBoxLayout(box)
        self.new_cat_edit = QLineEdit()
        self.new_cat_edit.setPlaceholderText("Nombre de la nueva categoría…")
        btn_create = QPushButton("＋ Crear")
        btn_create.clicked.connect(self._create_category)
        form.addWidget(self.new_cat_edit)
        form.addWidget(btn_create)
        layout.addWidget(box)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(6)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, stretch=1)

        info = QLabel("💡 Al eliminar una categoría, sus etiquetas se mueven a 'general'.")
        info.setStyleSheet("color:#666; font-size:10px;")
        layout.addWidget(info)

    def _refresh(self):
        for i in reversed(range(self.vbox.count())):
            w = self.vbox.itemAt(i).widget()
            if w: w.deleteLater()

        for cat in services.get_all_categories():
            tags  = services.get_tags_by_category(cat)
            count = len(tags)

            row = QWidget()
            row.setStyleSheet("background:#1E1E2E; border-radius:6px;")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(8, 6, 8, 6)
            hl.setSpacing(6)

            name_edit = QLineEdit(cat)
            name_edit.setStyleSheet(
                "background:#13131F; border:1px solid #3A3A5A; border-radius:4px; padding:3px 6px;"
            )
            name_edit.setFixedWidth(140)
            hl.addWidget(name_edit)

            count_lbl = QLabel(f"{count} etiqueta{'s' if count != 1 else ''}")
            count_lbl.setStyleSheet("color:#666; font-size:11px;")
            hl.addWidget(count_lbl, stretch=1)

            preview = QWidget()
            ph = QHBoxLayout(preview)
            ph.setContentsMargins(0, 0, 0, 0)
            ph.setSpacing(2)
            for tag in tags[:6]:
                dot = QLabel("●")
                dot.setStyleSheet(f"color:{tag.color}; font-size:10px;")
                ph.addWidget(dot)
            if count > 6:
                ph.addWidget(QLabel(f"+{count-6}"))
            hl.addWidget(preview)

            btn_rename = QPushButton("✎")
            btn_rename.setFixedSize(28, 28)
            btn_rename.setToolTip("Renombrar categoría")
            btn_rename.setStyleSheet("color:#4A9EFF; border:1px solid #4A9EFF; border-radius:4px;")
            btn_rename.clicked.connect(
                lambda _, old=cat, edit=name_edit: self._rename(old, edit.text())
            )
            hl.addWidget(btn_rename)

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
        if services.create_category(name):
            self.new_cat_edit.clear()
            self._refresh()
        else:
            QMessageBox.information(self, "Ya existe", f'La categoría "{name}" ya existe.')

    def _rename(self, old_name: str, new_name: str):
        new_name = new_name.strip().lower()
        if not new_name or new_name == old_name:
            return
        services.rename_category(old_name, new_name)
        self._refresh()

    def _delete(self, cat_name: str):
        tags = services.get_tags_by_category(cat_name)
        msg  = f'¿Eliminar la categoría "{cat_name}"?'
        if tags:
            msg += f"\n\nSus {len(tags)} etiqueta(s) se moverán a 'general'."
        reply = QMessageBox.question(self, "Confirmar", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            services.delete_category(cat_name)
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

        box = QGroupBox("Nueva etiqueta")
        form = QHBoxLayout(box)
        self.new_name  = QLineEdit(); self.new_name.setPlaceholderText("Nombre")
        self.new_cat   = QComboBox(); self.new_cat.setEditable(True)
        self.new_cat.setPlaceholderText("Categoría")
        self.new_cat.addItems(services.get_all_categories())
        self.new_color = QPushButton("Color"); self.new_color.setFixedWidth(60)
        self._color_val = "#4A9EFF"
        self.new_color.setStyleSheet(f"background:{self._color_val};")
        self.new_color.clicked.connect(self._pick_color)
        btn_create = QPushButton("Crear")
        btn_create.clicked.connect(self._create)
        for w in [self.new_name, self.new_cat, self.new_color, btn_create]:
            form.addWidget(w)
        layout.addWidget(box)

        btn_cats = QPushButton("📂  Gestionar categorías")
        btn_cats.setStyleSheet("color:#4A9EFF; border:1px solid #4A9EFF;")
        btn_cats.clicked.connect(self._open_category_manager)
        layout.addWidget(btn_cats)

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
        services.create_tag(name, cat, self._color_val)
        self.new_name.clear()
        self.new_cat.clear()
        self.new_cat.addItems(services.get_all_categories())
        self._refresh()

    def _refresh(self):
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w: w.deleteLater()

        for tag in services.get_all_tags():
            row = QWidget()
            hl  = QHBoxLayout(row)
            hl.setContentsMargins(4, 2, 4, 2)

            dot = QLabel("●")
            dot.setStyleSheet(f"color:{tag.color}; font-size:16px;")
            hl.addWidget(dot)

            lbl = QLabel(f"<b>{tag.name}</b>  <span style='color:#666;'>[{tag.category}]</span>")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            hl.addWidget(lbl, stretch=1)

            chk_hide = QCheckBox("Ocultar fotos")
            chk_hide.setChecked(tag.hidden)
            chk_hide.setToolTip("Las fotos con esta etiqueta no aparecen en la galería")
            chk_hide.stateChanged.connect(
                lambda state, tid=tag.id: services.set_tag_hidden(tid, bool(state))
            )
            hl.addWidget(chk_hide)

            chk_sidebar = QCheckBox("Ocultar del sidebar")
            chk_sidebar.setChecked(tag.sidebar_hidden)
            chk_sidebar.setToolTip("La etiqueta no aparece en el panel de filtros")
            chk_sidebar.stateChanged.connect(
                lambda state, tid=tag.id: services.set_tag_sidebar_hidden(tid, bool(state))
            )
            hl.addWidget(chk_sidebar)

            btn_del = QPushButton("Eliminar")
            btn_del.setFixedWidth(70)
            btn_del.setStyleSheet("color:#FF4A4A; border:1px solid #FF4A4A;")
            btn_del.clicked.connect(lambda _, tid=tag.id: self._delete(tid))
            hl.addWidget(btn_del)

            row.setStyleSheet("background:#1E1E2E; border-radius:6px;")
            self.grid.addWidget(row)

        self.grid.addStretch()

    def _delete(self, tag_id: int):
        services.delete_tag(tag_id)
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
        layout.addWidget(QLabel(
            "Selecciona una carpeta para eliminar sus registros de la base de datos.\n"
            "Los archivos originales NO se borran del disco."
        ))

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

        folders = services.get_indexed_folders()
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
                lbl = QLabel(
                    f"<b>{folder}</b>  <span style='color:#666;'>{count:,} archivos</span>"
                )
                lbl.setTextFormat(Qt.TextFormat.RichText)
                hl.addWidget(lbl, stretch=1)
                btn = QPushButton("Eliminar")
                btn.setFixedWidth(75)
                btn.setStyleSheet("color:#FF4A4A; border:1px solid #FF4A4A;")
                btn.clicked.connect(lambda _, f=folder: self._deindex_folder(f))
                hl.addWidget(btn)
                self.vbox.addWidget(row)
        self.vbox.addStretch()

    def _deindex_folder(self, folder: str):
        reply = QMessageBox.question(
            self, "Confirmar",
            f"¿Eliminar todos los registros de:\n{folder}\n\nLos archivos originales no se borrarán.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        services.deindex_folder(folder)
        self._refresh()

    def _remove_missing(self):
        count = services.remove_missing_files()
        if count == 0:
            QMessageBox.information(self, "Listo", "No se encontraron archivos faltantes.")
            return
        reply = QMessageBox.question(
            self, "Confirmar",
            f"Se eliminarán {count} registros de archivos que ya no existen en disco.\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QMessageBox.information(self, "Listo", f"Se eliminaron {count} registros.")
            self._refresh()


# ─── Ventana principal ────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoVault")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(DARK_STYLE)

        self._active_tags:    list[int]              = []
        self._offset:         int                    = 0
        self._page_size:      int                    = 100
        self._current_page:   GalleryPage | None     = None
        self._thumbnails:     dict[int, PhotoThumbnail] = {}
        self._pending_photos: list[tuple[int, Photo]]   = []
        self._grid_cols:      int                    = 4
        self._loader:         ThumbnailLoader | None = None

        self._build_timer = QTimer(self)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._add_next_batch)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(400)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_grid_cols = 0

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

        # Sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background:#13131F; border-right:1px solid #2D2D3F;")
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(10, 16, 10, 16)
        sb.setSpacing(8)

        logo = QLabel("📸 PhotoVault")
        logo.setStyleSheet("font-size:18px; font-weight:bold; color:#4A9EFF; margin-bottom:8px;")
        sb.addWidget(logo)

        for label, slot in [
            ("＋ Indexar carpeta",    self._open_index_dialog),
            ("🏷  Gestionar etiquetas", self._open_tag_manager),
            ("🗂  Des-indexar carpetas", self._open_deindex_dialog),
            ("⚙  Configuración",       self._open_settings_dialog),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            sb.addWidget(btn)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;")
        sb.addWidget(sep)
        sb.addWidget(QLabel("Filtrar por etiqueta:"))

        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_widget = QWidget()
        self.tag_vbox   = QVBoxLayout(self.tag_widget)
        self.tag_vbox.setContentsMargins(0, 0, 0, 0)
        self.tag_vbox.setSpacing(2)
        self.tag_scroll.setWidget(self.tag_widget)
        sb.addWidget(self.tag_scroll, stretch=1)

        btn_clear = QPushButton("✕ Limpiar filtros")
        btn_clear.setStyleSheet("color:#FF4A4A;")
        btn_clear.clicked.connect(self._clear_filters)
        sb.addWidget(btn_clear)

        self.stats_lbl = QLabel("")
        self.stats_lbl.setStyleSheet("color:#666; font-size:10px;")
        sb.addWidget(self.stats_lbl)
        root.addWidget(sidebar)

        # Área principal
        main_area = QWidget()
        ml = QVBoxLayout(main_area)
        ml.setContentsMargins(12, 12, 12, 12)
        ml.setSpacing(8)

        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Buscar por nombre…")
        self.search_edit.textChanged.connect(self._on_search)
        top_bar.addWidget(self.search_edit)
        self.count_lbl = QLabel("0 fotos")
        self.count_lbl.setStyleSheet("color:#8888AA;")
        top_bar.addWidget(self.count_lbl)
        ml.addLayout(top_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(8)
        self.scroll_area.setWidget(self.grid_widget)
        ml.addWidget(self.scroll_area, stretch=1)

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
        ml.addLayout(pg_bar)

        root.addWidget(main_area, stretch=1)

    # ── Sidebar de etiquetas ──────────────────────────────────────────────────

    def _refresh_tags(self):
        for i in reversed(range(self.tag_vbox.count())):
            w = self.tag_vbox.itemAt(i).widget()
            if w: w.deleteLater()

        # services.get_sidebar_tags() ya agrupa por categoría
        for category, tags in services.get_sidebar_tags().items():
            header = QPushButton(f"▾  {category.upper()}")
            header.setCheckable(True)
            header.setChecked(True)
            header.setStyleSheet("""
                QPushButton {
                    background:#1A1A2E; color:#6688AA; border:none;
                    border-top:1px solid #2D2D3F; border-radius:0;
                    text-align:left; padding:4px 6px;
                    font-size:10px; font-weight:bold; letter-spacing:1px;
                }
                QPushButton:hover { color:#88AACC; background:#1E1E2E; }
            """)

            group_widget = QWidget()
            group_vbox   = QVBoxLayout(group_widget)
            group_vbox.setContentsMargins(8, 0, 0, 4)
            group_vbox.setSpacing(1)

            for tag in tags:
                row = QWidget()
                hl  = QHBoxLayout(row)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)

                chk = QCheckBox(tag.name)
                chk.setStyleSheet(f"color:{tag.color};")
                chk.setProperty("tag_id", tag.id)
                if tag.id in self._active_tags:
                    chk.setChecked(True)
                chk.stateChanged.connect(self._on_tag_filter_changed)
                hl.addWidget(chk, stretch=1)

                eye_btn = QPushButton("👁" if not tag.sidebar_hidden else "🚫")
                eye_btn.setFixedSize(22, 22)
                eye_btn.setToolTip(
                    "Ocultar del sidebar" if not tag.sidebar_hidden else "Mostrar en sidebar"
                )
                eye_btn.setStyleSheet(
                    "QPushButton { background:transparent; border:none; font-size:11px; padding:0; }"
                    "QPushButton:hover { background:#2D2D3F; border-radius:4px; }"
                )
                eye_btn.clicked.connect(
                    lambda _, tid=tag.id, cur=tag.sidebar_hidden:
                        self._toggle_sidebar_hidden(tid, cur)
                )
                hl.addWidget(eye_btn)
                group_vbox.addWidget(row)

            header.toggled.connect(lambda checked, gw=group_widget, btn=header: (
                gw.setVisible(checked),
                btn.setText(f"{'▾' if checked else '▸'}  {btn.text()[2:]}")
            ))
            self.tag_vbox.addWidget(header)
            self.tag_vbox.addWidget(group_widget)

        self.tag_vbox.addStretch()
        self._update_stats()

    def _toggle_sidebar_hidden(self, tag_id: int, currently_hidden: bool):
        services.set_tag_sidebar_hidden(tag_id, not currently_hidden)
        self._refresh_tags()

    def _on_tag_filter_changed(self):
        self._active_tags = []
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if w is None:
                continue
            for chk in w.findChildren(QCheckBox):
                if chk.isChecked() and chk.property("tag_id") is not None:
                    self._active_tags.append(chk.property("tag_id"))
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

    def _stop_loader(self):
        if self._loader is not None:
            self._loader.loaded.disconnect()
            try:
                self._loader.finished.disconnect()
            except Exception:
                pass
            self._loader.stop()
            self._loader.wait(500)
            self._loader = None

    def _load_photos(self):
        self._build_timer.stop()
        self._stop_loader()

        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w: w.deleteLater()
        self._thumbnails.clear()

        search = self.search_edit.text().strip() or None

        # Una sola llamada al servicio devuelve todo lo necesario
        self._current_page = services.get_gallery_page(
            tag_ids = self._active_tags or None,
            search  = search,
            limit   = self._page_size,
            offset  = self._offset,
        )

        self.count_lbl.setText(f"{self._current_page.total:,} fotos")
        self._update_pagination()

        new_cols = max(1, (self.scroll_area.width() - 30) // 218)
        self._grid_cols      = new_cols
        self._last_grid_cols = new_cols

        self._pending_photos = list(enumerate(self._current_page.photos))
        self._build_timer.start()

    def _add_next_batch(self):
        BATCH = 10
        batch, self._pending_photos = (
            self._pending_photos[:BATCH],
            self._pending_photos[BATCH:],
        )

        for idx, photo in batch:
            thumb = PhotoThumbnail(photo)
            thumb.clicked.connect(self._open_photo)
            self.grid_layout.addWidget(thumb, idx // self._grid_cols, idx % self._grid_cols)
            self._thumbnails[photo.id] = thumb

        if not self._pending_photos:
            self._build_timer.stop()
            self._loader = ThumbnailLoader(self._current_page.photos)
            self._loader.loaded.connect(self._on_thumb_loaded)
            self._loader.finished.connect(self._on_loader_finished)
            self._loader.start()

    def _on_loader_finished(self):
        self._loader = None

    def _on_thumb_loaded(self, photo_id: int, pix: QPixmap):
        if photo_id in self._thumbnails:
            self._thumbnails[photo_id].set_pixmap(pix)

    def _on_search(self):
        self._offset = 0
        self._load_photos()

    # ── Paginación ────────────────────────────────────────────────────────────

    def _update_pagination(self):
        if not self._current_page:
            return
        p = self._current_page
        self.page_lbl.setText(f"Página {p.page_number} / {p.total_pages}")
        self.prev_btn.setEnabled(p.has_prev)
        self.next_btn.setEnabled(p.has_next)

    def _prev_page(self):
        self._offset = max(0, self._offset - self._page_size)
        self._load_photos()

    def _next_page(self):
        self._offset += self._page_size
        self._load_photos()

    # ── Resize con debounce ───────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()

    def _on_resize_settled(self):
        new_cols = max(1, (self.scroll_area.width() - 30) // 218)
        if new_cols != self._last_grid_cols:
            self._last_grid_cols = new_cols
            self._load_photos()

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _open_photo(self, photo_id: int):
        if not self._current_page:
            return
        photo = next((p for p in self._current_page.photos if p.id == photo_id), None)
        if not photo:
            return
        dlg = PhotoDetailDialog(photo, self)
        dlg.tags_changed.connect(self._load_photos)
        dlg.exec()

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

    def _open_settings_dialog(self):
        dlg = SettingsDialog(self._page_size, self)
        if dlg.exec():
            self._page_size = dlg.page_size
            self._offset    = 0
            self._load_photos()

    def _open_deindex_dialog(self):
        dlg = DeindexDialog(self)
        dlg.exec()
        self._load_photos()

    def _update_stats(self):
        stats = services.get_stats()
        self.stats_lbl.setText(
            f"{stats.total_photos:,} fotos  •  {stats.total_tags} etiquetas"
        )

    def closeEvent(self, event):
        self._build_timer.stop()
        self._stop_loader()
        db.close_connection()   # cerrar conexión del hilo principal
        super().closeEvent(event)


# ─── Estilos globales ─────────────────────────────────────────────────────────

DARK_STYLE = """
    * { font-family: 'Segoe UI', sans-serif; font-size: 13px; }
    QMainWindow, QDialog, QWidget { background: #0D0D1A; color: #D0D0E8; }
    QPushButton {
        background: #1E1E2E; color: #D0D0E8;
        border: 1px solid #3A3A5A; border-radius: 6px; padding: 6px 12px;
    }
    QPushButton:hover   { background: #2A2A3E; border-color: #4A9EFF; }
    QPushButton:pressed { background: #4A9EFF33; }
    QPushButton:disabled { color: #444; border-color: #222; }
    QLineEdit, QComboBox {
        background: #1E1E2E; color: #D0D0E8;
        border: 1px solid #3A3A5A; border-radius: 6px; padding: 6px 10px;
    }
    QLineEdit:focus, QComboBox:focus { border-color: #4A9EFF; }
    QScrollArea { border: none; }
    QScrollBar:vertical { background: #13131F; width: 8px; border-radius: 4px; }
    QScrollBar::handle:vertical {
        background: #3A3A5A; border-radius: 4px; min-height: 20px;
    }
    QScrollBar::handle:vertical:hover { background: #4A9EFF; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QProgressBar {
        background: #1E1E2E; border: 1px solid #3A3A5A;
        border-radius: 4px; text-align: center; color: #D0D0E8;
    }
    QProgressBar::chunk { background: #4A9EFF; border-radius: 4px; }
    QGroupBox {
        border: 1px solid #3A3A5A; border-radius: 6px;
        margin-top: 8px; padding-top: 8px; color: #8888AA; font-size: 11px;
    }
    QCheckBox { color: #D0D0E8; spacing: 6px; }
    QCheckBox::indicator {
        width: 14px; height: 14px;
        border: 1px solid #3A3A5A; border-radius: 3px; background: #1E1E2E;
    }
    QCheckBox::indicator:checked { background: #4A9EFF; border-color: #4A9EFF; }
    QLabel { color: #D0D0E8; }
    QComboBox QAbstractItemView {
        background: #1E1E2E; border: 1px solid #3A3A5A;
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
