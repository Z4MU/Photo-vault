"""
PhotoVault - main.py

Features nuevos en esta versión:
  1. Purgar huérfanos del caché  → botón en SettingsDialog
  2. Exportar/importar tags JSON → TagManagerDialog
  3. Ordenamiento configurable   → ComboBox en barra superior
  4. Estadísticas visuales       → StatsDialog con gráficos SVG
  5. Etiquetado múltiple en lote → selección con Ctrl/Shift + BulkTagDialog
  6. Vista de duplicados         → DuplicatesDialog con hash MD5
"""

import logging
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPixmap
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import backup
import database as db
import indexer
import logging_setup
import services
import thumbnail_cache
from models import DuplicateGroup, GalleryPage, Photo, SortField, SortOrder, Tag

logger = logging.getLogger(__name__)


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
                progress_callback=lambda c, t, p: self.progress.emit(c, t, p),
            )
            self.finished.emit(added, skipped, errors)
        except Exception as e:
            logger.exception("Error indexando %s", self.folder)
            self.error.emit(str(e))
        finally:
            db.close_connection()


# ─── Hilo para cargar miniaturas ──────────────────────────────────────────────

class ThumbnailLoader(QThread):
    loaded = pyqtSignal(int, QPixmap)

    def __init__(self, photos: list[Photo]):
        super().__init__()
        self.photos     = photos
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        for photo in self.photos:
            if self._stop_flag:
                break
            try:
                jpeg = (thumbnail_cache.get_video_thumbnail(photo.path)
                        if photo.is_video
                        else thumbnail_cache.get_thumbnail(photo.path))
                if jpeg:
                    pix = QPixmap()
                    pix.loadFromData(jpeg)
                    if not pix.isNull():
                        if pix.width() > 200 or pix.height() > 200:
                            pix = pix.scaled(200, 200,
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                        self.loaded.emit(photo.id, pix)
            except Exception:
                logger.exception("Error cargando miniatura de %s", photo.path)
            if not self._stop_flag:
                self.msleep(5)
        db.close_connection()


# ─── Hilo para calcular MD5s ──────────────────────────────────────────────────

class MD5Worker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int)
    error    = pyqtSignal(str)

    def run(self):
        try:
            n = services.compute_missing_md5s(
                progress_callback=lambda c, t: self.progress.emit(c, t)
            )
            self.finished.emit(n)
        except Exception as e:
            logger.exception("Error calculando MD5s")
            self.error.emit(str(e))
        finally:
            db.close_connection()


# ─── Widget de miniatura ──────────────────────────────────────────────────────

class PhotoThumbnail(QFrame):
    clicked  = pyqtSignal(int)
    selected = pyqtSignal(int, bool)   # photo_id, is_selected

    def __init__(self, photo: Photo, selectable: bool = False, parent=None):
        super().__init__(parent)
        self.photo_id   = photo.id
        self.selectable = selectable
        self._selected  = False
        self.setFixedSize(210, 230)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

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
            play = QLabel("▶", img_container)
            play.setStyleSheet(
                "color:white;font-size:28px;background:rgba(0,0,0,0.55);"
                "border-radius:20px;padding:4px 8px;"
            )
            play.adjustSize()
            play.move((200 - play.width()) // 2, (200 - play.height()) // 2)
            if photo.duration_str:
                dur = QLabel(photo.duration_str, img_container)
                dur.setStyleSheet(
                    "color:white;font-size:10px;background:rgba(0,0,0,0.7);"
                    "border-radius:3px;padding:1px 5px;"
                )
                dur.adjustSize()
                dur.move(200 - dur.width() - 6, 200 - dur.height() - 6)

        # Checkbox de selección (visible solo en modo seleccionable)
        if selectable:
            self.chk = QCheckBox(img_container)
            self.chk.setStyleSheet("""
                QCheckBox::indicator { width:18px; height:18px; border-radius:4px; }
                QCheckBox::indicator:unchecked {
                    background:rgba(0,0,0,0.5); border:2px solid #888;
                }
                QCheckBox::indicator:checked {
                    background:#4A9EFF; border:2px solid #4A9EFF;
                }
            """)
            self.chk.move(6, 6)
            self.chk.stateChanged.connect(
                lambda s: self.selected.emit(self.photo_id, bool(s))
            )

        layout.addWidget(img_container)
        name_label = QLabel(photo.short_name)
        name_label.setStyleSheet("color:#8888AA;font-size:10px;")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

    def _update_style(self):
        if self._selected:
            self.setStyleSheet("""
                QFrame { background:#1E2E4E; border-radius:8px;
                         border:2px solid #4A9EFF; }
            """)
        else:
            self.setStyleSheet("""
                QFrame { background:#1E1E2E; border-radius:8px;
                         border:1px solid #2D2D3F; }
                QFrame:hover { border:1px solid #4A9EFF; background:#252538; }
            """)

    def set_pixmap(self, pix: QPixmap):
        self.img_label.setPixmap(pix)

    def set_selected(self, val: bool):
        self._selected = val
        self._update_style()
        if self.selectable and hasattr(self, "chk"):
            self.chk.blockSignals(True)
            self.chk.setChecked(val)
            self.chk.blockSignals(False)

    def mousePressEvent(self, event):
        if self.selectable:
            new_state = not self._selected
            self.set_selected(new_state)
            self.selected.emit(self.photo_id, new_state)
        else:
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
            left = QWidget(); left.setMinimumWidth(500)
            ll   = QVBoxLayout(left)
            ll.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb = thumbnail_cache.get_video_thumbnail(self.photo.path, size=480)
            lbl   = QLabel(); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if thumb:
                pix = QPixmap(); pix.loadFromData(thumb); lbl.setPixmap(pix)
            else:
                lbl.setText("🎬"); lbl.setStyleSheet("font-size:64px;")
            ll.addWidget(lbl)
            btn = QPushButton("▶  Reproducir")
            btn.setStyleSheet(
                "QPushButton{background:#4A9EFF22;color:#4A9EFF;"
                "border:1px solid #4A9EFF;border-radius:8px;padding:10px 20px;font-size:14px;}"
                "QPushButton:hover{background:#4A9EFF44;}"
            )
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.photo.path)))
            ll.addWidget(btn)
            layout.addWidget(left)
        else:
            img = QLabel(); img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img.setMinimumWidth(500)
            pix = QPixmap(self.photo.path)
            if not pix.isNull():
                pix = pix.scaled(560, 560, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                img.setPixmap(pix)
            layout.addWidget(img)

        right = QVBoxLayout(); right.setSpacing(10)
        right.addWidget(QLabel(f"<b>{Path(self.photo.path).name}</b>"))
        right.addWidget(QLabel("Etiquetas:"))

        self.tags_container = QWidget()
        self.tags_layout    = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea(); scroll.setWidget(self.tags_container)
        scroll.setWidgetResizable(True); scroll.setFixedHeight(80)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(scroll)

        right.addWidget(QLabel("Agregar etiqueta:"))
        row = QHBoxLayout()
        self.tag_combo = QComboBox(); self.tag_combo.setEditable(True)
        self.tag_combo.setPlaceholderText("Buscar o nueva etiqueta…")
        row.addWidget(self.tag_combo)
        btn_add = QPushButton("＋ Agregar"); btn_add.clicked.connect(self._add_tag)
        row.addWidget(btn_add)
        right.addLayout(row)
        right.addStretch()
        layout.addLayout(right)

    def _load_tags(self):
        for i in reversed(range(self.tags_layout.count())):
            self.tags_layout.itemAt(i).widget().deleteLater()
        for tag in services.get_photo_tags(self.photo.id):
            self.tags_layout.addWidget(self._chip(tag))
        self.tag_combo.clear()
        for t in services.get_all_tags():
            self.tag_combo.addItem(t.name, userData=t.id)

    def _chip(self, tag: Tag) -> QPushButton:
        btn = QPushButton(f"{tag.name}  ✕")
        btn.setStyleSheet(
            f"QPushButton{{background:{tag.color}33;color:{tag.color};"
            f"border:1px solid {tag.color};border-radius:10px;padding:2px 8px;font-size:11px;}}"
            f"QPushButton:hover{{background:{tag.color}66;}}"
        )
        btn.clicked.connect(lambda _, tid=tag.id: self._remove_tag(tid))
        return btn

    def _add_tag(self):
        text = self.tag_combo.currentText().strip().lower()
        if text:
            services.add_tag(self.photo.id, text)
            self._load_tags(); self.tags_changed.emit()

    def _remove_tag(self, tag_id: int):
        services.remove_tag(self.photo.id, tag_id)
        self._load_tags(); self.tags_changed.emit()


# ─── Dialog: Etiquetado en lote ───────────────────────────────────────────────

class BulkTagDialog(QDialog):
    """Agrega o quita una etiqueta a todas las fotos seleccionadas."""

    def __init__(self, photo_ids: list[int], parent=None):
        super().__init__(parent)
        self.photo_ids = photo_ids
        self.setWindowTitle(f"Etiquetar {len(photo_ids)} fotos")
        self.setFixedSize(420, 200)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            f"<b>{len(self.photo_ids)} fotos seleccionadas</b><br>"
            "<span style='color:#888;font-size:11px;'>"
            "Escribe o elige una etiqueta y aplica a todas.</span>"
        ))

        self.combo = QComboBox(); self.combo.setEditable(True)
        self.combo.setPlaceholderText("Etiqueta a aplicar…")
        for t in services.get_all_tags():
            self.combo.addItem(t.name, userData=t.id)
        layout.addWidget(self.combo)

        row = QHBoxLayout()
        btn_add = QPushButton("＋ Agregar a todas")
        btn_add.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_add.clicked.connect(self._add)

        btn_rem = QPushButton("✕ Quitar de todas")
        btn_rem.setStyleSheet("background:#FF4A4A22;color:#FF4A4A;border:1px solid #FF4A4A;")
        btn_rem.clicked.connect(self._remove)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)

        row.addWidget(btn_add); row.addWidget(btn_rem); row.addWidget(btn_cancel)
        layout.addLayout(row)

    def _add(self):
        name = self.combo.currentText().strip().lower()
        if not name:
            return
        n = services.bulk_add_tag(self.photo_ids, name)
        QMessageBox.information(self, "Listo", f"Etiqueta '{name}' agregada a {n} fotos.")
        self.accept()

    def _remove(self):
        name = self.combo.currentText().strip().lower()
        if not name:
            return
        all_tags = services.get_all_tags()
        tag = next((t for t in all_tags if t.name == name), None)
        if not tag:
            QMessageBox.warning(self, "No encontrada",
                                f"La etiqueta '{name}' no existe.")
            return
        n = services.bulk_remove_tag(self.photo_ids, tag.id)
        QMessageBox.information(self, "Listo", f"Etiqueta '{name}' quitada de {n} fotos.")
        self.accept()


# ─── Dialog: Estadísticas ─────────────────────────────────────────────────────

class StatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Estadísticas")
        self.setMinimumSize(700, 520)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()

    def _build_ui(self):
        stats  = services.get_stats()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Resumen numérico ───────────────────────────────────────────────
        summary = QHBoxLayout()
        for label, value in [
            ("Archivos totales", f"{stats.total_photos:,}"),
            ("Imágenes", f"{stats.by_type.get('image', 0):,}"),
            ("Videos", f"{stats.by_type.get('video', 0):,}"),
            ("Etiquetas", f"{stats.total_tags:,}"),
        ]:
            card = QWidget()
            card.setStyleSheet("background:#1E1E2E;border-radius:8px;padding:4px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 10, 16, 10)
            v = QLabel(value); v.setStyleSheet("font-size:22px;font-weight:bold;color:#4A9EFF;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l = QLabel(label); l.setStyleSheet("font-size:11px;color:#888;")
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(v); cl.addWidget(l)
            summary.addWidget(card)
        layout.addLayout(summary)

        # ── Gráfico de barras por año ──────────────────────────────────────
        if stats.years:
            layout.addWidget(QLabel("<b>Archivos por año</b>"))
            layout.addWidget(self._bar_chart(stats.years, "#4A9EFF"))

        # ── Top 10 etiquetas ──────────────────────────────────────────────
        if stats.top_tags:
            layout.addWidget(QLabel("<b>Etiquetas más usadas</b>"))
            layout.addWidget(self._bar_chart(stats.top_tags, "#9E4AFF", is_text_key=True))

        layout.addStretch()

    def _bar_chart(self, data: list[tuple], color: str,
                   is_text_key: bool = False) -> QWidget:
        """Genera un widget SVG con un gráfico de barras horizontal."""
        if not data:
            return QLabel("Sin datos")

        max_val  = max(v for _, v in data)
        bar_h    = 22
        gap      = 6
        label_w  = 80
        chart_w  = 460
        height   = len(data) * (bar_h + gap) + 10
        svg_w    = label_w + chart_w + 60

        bars = []
        for i, (key, val) in enumerate(data):
            y    = 5 + i * (bar_h + gap)
            fill = int(val / max_val * chart_w) if max_val else 0
            key_str = str(key)[:12]
            bars.append(
                f'<text x="{label_w - 6}" y="{y + bar_h - 6}" '
                f'text-anchor="end" fill="#8888AA" font-size="11">{key_str}</text>'
                f'<rect x="{label_w}" y="{y}" width="{fill}" height="{bar_h}" '
                f'rx="4" fill="{color}99"/>'
                f'<text x="{label_w + fill + 6}" y="{y + bar_h - 6}" '
                f'fill="#CCC" font-size="11">{val:,}</text>'
            )

        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{svg_w}" height="{height}">'
            f'<rect width="{svg_w}" height="{height}" fill="#13131F" rx="8"/>'
            + "".join(bars) +
            "</svg>"
        )

        widget = QSvgWidget()
        widget.load(svg.encode())
        widget.setFixedHeight(height + 10)
        return widget


# ─── Dialog: Duplicados ───────────────────────────────────────────────────────

class DuplicatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vista de duplicados")
        self.setMinimumSize(700, 540)
        self.setStyleSheet(DARK_STYLE)
        self._groups: list[DuplicateGroup] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            "<b>Duplicados por hash MD5</b><br>"
            "<span style='color:#888;font-size:11px;'>"
            "Los archivos con el mismo contenido aparecen agrupados. "
            "Puedes eliminar físicamente las copias extra.</span>"
        ))

        # Barra de cálculo de MD5
        md5_bar = QHBoxLayout()
        self.md5_progress = QProgressBar(); self.md5_progress.setRange(0, 100)
        self.md5_progress.setVisible(False)
        self.btn_scan = QPushButton("🔍  Escanear duplicados")
        self.btn_scan.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_scan.clicked.connect(self._start_scan)
        md5_bar.addWidget(self.btn_scan)
        md5_bar.addWidget(self.md5_progress, stretch=1)
        layout.addLayout(md5_bar)

        self.status_lbl = QLabel("Presiona 'Escanear' para calcular los MD5s.")
        self.status_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(self.status_lbl)

        # Lista de grupos
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(8)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, stretch=1)

        btn_close = QPushButton("Cerrar"); btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _start_scan(self):
        self.btn_scan.setEnabled(False)
        self.md5_progress.setVisible(True)
        self.md5_progress.setValue(0)
        self.status_lbl.setText("Calculando hashes…")
        self._worker = MD5Worker()
        self._worker.progress.connect(
            lambda c, t: self.md5_progress.setValue(int(c / t * 100) if t else 0)
        )
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(lambda e: (
            QMessageBox.critical(self, "Error", e),
            self.btn_scan.setEnabled(True),
        ))
        self._worker.start()

    def _on_scan_done(self, n: int):
        self.md5_progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        self._groups = services.get_duplicate_groups()
        self._render_groups()

    def _render_groups(self):
        for i in reversed(range(self.vbox.count())):
            w = self.vbox.itemAt(i).widget()
            if w: w.deleteLater()

        if not self._groups:
            self.status_lbl.setText("✓ No se encontraron duplicados.")
            self.vbox.addWidget(QLabel("No hay duplicados."))
            return

        total_wasted = sum(g.wasted_bytes for g in self._groups)
        self.status_lbl.setText(
            f"{len(self._groups)} grupos de duplicados  •  "
            f"{total_wasted / 1_048_576:.1f} MB recuperables"
        )

        for group in self._groups:
            card = QWidget()
            card.setStyleSheet("background:#1A1A2E;border-radius:8px;")
            cl = QVBoxLayout(card); cl.setContentsMargins(10, 8, 10, 8); cl.setSpacing(6)

            header_lbl = QLabel(
                f"<b>{group.size} copias</b>  "
                f"<span style='color:#888;font-size:11px;'>"
                f"MD5: {group.md5[:16]}…  •  "
                f"+{group.wasted_bytes // 1024} KB duplicados</span>"
            )
            header_lbl.setTextFormat(Qt.TextFormat.RichText)
            cl.addWidget(header_lbl)

            photos_row = QHBoxLayout()
            for photo in group.photos:
                col = QVBoxLayout()
                # Miniatura pequeña
                img_lbl = QLabel()
                img_lbl.setFixedSize(100, 100)
                img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_lbl.setStyleSheet("background:#13131F;border-radius:4px;")
                jpeg = thumbnail_cache.get_thumbnail(photo.path, size=100)
                if jpeg:
                    pix = QPixmap(); pix.loadFromData(jpeg)
                    img_lbl.setPixmap(pix.scaled(100, 100,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                col.addWidget(img_lbl)

                name = QLabel(photo.short_name)
                name.setStyleSheet("font-size:10px;color:#8888AA;")
                name.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col.addWidget(name)

                size_lbl = QLabel(f"{(photo.filesize or 0) // 1024} KB")
                size_lbl.setStyleSheet("font-size:10px;color:#666;")
                size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col.addWidget(size_lbl)

                btn_del = QPushButton("🗑 Eliminar")
                btn_del.setFixedWidth(90)
                btn_del.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;font-size:11px;")
                btn_del.clicked.connect(
                    lambda _, pid=photo.id, g=group: self._delete_photo(pid, g)
                )
                col.addWidget(btn_del)
                photos_row.addLayout(col)

            photos_row.addStretch()
            cl.addLayout(photos_row)
            self.vbox.addWidget(card)

        self.vbox.addStretch()

    def _delete_photo(self, photo_id: int, group: DuplicateGroup):
        if group.size <= 1:
            QMessageBox.warning(self, "Atención",
                "No puedes eliminar la última copia del archivo.")
            return
        reply = QMessageBox.question(
            self, "Confirmar eliminación",
            "¿Eliminar este archivo del disco y de la base de datos?\n"
            "Esta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok = services.delete_photo_file(photo_id)
        if ok:
            group.photos = [p for p in group.photos if p.id != photo_id]
            self._render_groups()
        else:
            QMessageBox.critical(self, "Error", "No se pudo eliminar el archivo.")


# ─── Dialog: Configuración del modo etiquetado rápido ────────────────────────

class QuickTagSetupDialog(QDialog):
    """
    Permite al usuario elegir qué fotos incluir en el modo etiquetado rápido:
    - Carpeta indexada (opcional)
    - Etiquetas que deben tener (opcional)
    - Solo fotos sin etiquetar
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modo etiquetado rápido — configurar")
        self.setFixedSize(480, 380)
        self.setStyleSheet(DARK_STYLE)
        self.selected_folder: str | None = None
        self.selected_tag_ids: list[int] = []
        self.untagged_only: bool = False
        self._build_ui()
        self._refresh_count()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(QLabel(
            "<b>¿Qué fotos quieres etiquetar?</b><br>"
            "<span style='color:#888;font-size:11px;'>"
            "Combina los filtros para acotar el conjunto.</span>"
        ))

        # ── Filtro por carpeta ─────────────────────────────────────────────
        box_folder = QGroupBox("Carpeta indexada (opcional)")
        fl = QHBoxLayout(box_folder)
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("— Todas las carpetas —", userData=None)
        for folder, count in services.get_indexed_folders():
            label = f"{Path(folder).name}  ({count:,} archivos)"
            self.folder_combo.addItem(label, userData=folder)
        self.folder_combo.currentIndexChanged.connect(self._refresh_count)
        fl.addWidget(self.folder_combo)
        layout.addWidget(box_folder)

        # ── Filtro por etiquetas ───────────────────────────────────────────
        box_tags = QGroupBox("Solo fotos que tengan estas etiquetas (opcional)")
        tl = QVBoxLayout(box_tags)
        tl.setSpacing(4)

        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setFixedHeight(100)
        tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_inner = QWidget()
        tag_grid  = QVBoxLayout(tag_inner)
        tag_grid.setContentsMargins(4, 4, 4, 4)
        tag_grid.setSpacing(2)

        self._tag_checks: list[tuple[QCheckBox, int]] = []
        for tag in services.get_all_tags(include_sidebar_hidden=True):
            chk = QCheckBox(f"{tag.name}  [{tag.category}]")
            chk.setStyleSheet(f"color:{tag.color};")
            chk.stateChanged.connect(self._refresh_count)
            tag_grid.addWidget(chk)
            self._tag_checks.append((chk, tag.id))
        tag_grid.addStretch()

        tag_scroll.setWidget(tag_inner)
        tl.addWidget(tag_scroll)
        layout.addWidget(box_tags)

        # ── Solo sin etiquetar ─────────────────────────────────────────────
        self.chk_untagged = QCheckBox("Solo fotos sin ninguna etiqueta")
        self.chk_untagged.setStyleSheet("color:#FFD700;")
        self.chk_untagged.stateChanged.connect(self._refresh_count)
        layout.addWidget(self.chk_untagged)

        # ── Contador de resultados ─────────────────────────────────────────
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color:#4A9EFF;font-size:12px;")
        layout.addWidget(self.count_lbl)

        layout.addStretch()

        # ── Botones ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)

        self.btn_start = QPushButton("▶  Iniciar etiquetado")
        self.btn_start.setStyleSheet(
            "background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;"
        )
        self.btn_start.clicked.connect(self._start)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_start)
        layout.addLayout(btn_row)

    def _refresh_count(self):
        folder   = self.folder_combo.currentData()
        tag_ids  = [tid for chk, tid in self._tag_checks if chk.isChecked()]
        untagged = self.chk_untagged.isChecked()
        photos   = services.get_photos_for_tagging(
            folder=folder, tag_ids=tag_ids or None, untagged_only=untagged
        )
        n = len(photos)
        self.count_lbl.setText(
            f"{n:,} foto{'s' if n != 1 else ''} coinciden con este filtro"
        )
        self.btn_start.setEnabled(n > 0)

    def _start(self):
        self.selected_folder   = self.folder_combo.currentData()
        self.selected_tag_ids  = [tid for chk, tid in self._tag_checks if chk.isChecked()]
        self.untagged_only     = self.chk_untagged.isChecked()
        self.accept()


# ─── Ventana: Modo etiquetado rápido ─────────────────────────────────────────

class QuickTagWindow(QDialog):
    """
    Vista de pantalla completa para etiquetar fotos una por una con teclado.

    Controles:
      ← / →     foto anterior / siguiente
      Space      avanzar sin cambios
      1–9        activar/desactivar etiqueta por atajo
      Ctrl+Z     deshacer última acción
      Esc        salir y volver a la galería
    """

    done_signal = pyqtSignal()   # emitido al cerrar para que galería recargue

    def __init__(self, photos: list[Photo], parent=None):
        super().__init__(parent)
        self.photos   = photos
        self.index    = 0
        self._history: list[tuple[int, int, bool]] = []  # (photo_id, tag_id, was_added)
        self._tag_shortcuts: dict[int, Tag] = {}          # tecla 1-9 → Tag

        self.setWindowTitle("Etiquetado rápido")
        self.setMinimumSize(1000, 640)
        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._load_current()

    # ── Construcción de UI ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barra superior
        topbar = QWidget()
        topbar.setStyleSheet("background:#13131F;border-bottom:1px solid #2D2D3F;")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 8, 14, 8)

        title = QLabel("⚡ Etiquetado rápido")
        title.setStyleSheet("color:#4A9EFF;font-size:13px;font-weight:bold;")
        tb.addWidget(title)

        for key, desc in [("←→", "navegar"), ("1–9", "etiqueta"), ("Space", "saltar"),
                           ("Ctrl+Z", "deshacer"), ("Esc", "salir")]:
            tb.addWidget(self._kbd(key))
            lbl = QLabel(desc); lbl.setStyleSheet("color:#555;font-size:11px;")
            tb.addWidget(lbl)
            tb.addSpacing(10)

        tb.addStretch()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color:#8888AA;font-size:12px;")
        tb.addWidget(self.progress_lbl)

        btn_exit = QPushButton("✕  Salir")
        btn_exit.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;padding:4px 10px;")
        btn_exit.clicked.connect(self.close)
        tb.addWidget(btn_exit)
        root.addWidget(topbar)

        # Cuerpo principal
        body = QWidget()
        bl   = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ── Zona de foto ──────────────────────────────────────────────────
        photo_area = QWidget()
        photo_area.setStyleSheet("background:#0D0D1A;")
        pal = QVBoxLayout(photo_area)
        pal.setAlignment(Qt.AlignmentFlag.AlignCenter)

        nav_row = QHBoxLayout()
        self.btn_prev = QPushButton("‹")
        self.btn_prev.setFixedSize(44, 44)
        self.btn_prev.setStyleSheet(
            "QPushButton{background:#1E1E2E99;border:0.5px solid #3A3A5A;"
            "border-radius:22px;font-size:22px;color:#D0D0E8;}"
            "QPushButton:hover{background:#2A2A3E;border-color:#4A9EFF;}"
            "QPushButton:disabled{color:#333;border-color:#222;}"
        )
        self.btn_prev.clicked.connect(self._prev)

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setMinimumSize(500, 500)
        self.img_label.setStyleSheet("background:transparent;")

        self.btn_next = QPushButton("›")
        self.btn_next.setFixedSize(44, 44)
        self.btn_next.setStyleSheet(
            "QPushButton{background:#1E1E2E99;border:0.5px solid #3A3A5A;"
            "border-radius:22px;font-size:22px;color:#D0D0E8;}"
            "QPushButton:hover{background:#2A2A3E;border-color:#4A9EFF;}"
            "QPushButton:disabled{color:#333;border-color:#222;}"
        )
        self.btn_next.clicked.connect(self._next)

        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.img_label, stretch=1)
        nav_row.addWidget(self.btn_next)
        pal.addLayout(nav_row)

        # Chips de etiquetas activas bajo la foto
        self.chips_widget = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_widget)
        self.chips_layout.setContentsMargins(0, 8, 0, 0)
        self.chips_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chips_layout.setSpacing(6)
        pal.addWidget(self.chips_widget)

        # Nombre del archivo
        self.filename_lbl = QLabel("")
        self.filename_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filename_lbl.setStyleSheet("color:#555;font-size:11px;margin-top:4px;")
        pal.addWidget(self.filename_lbl)

        bl.addWidget(photo_area, stretch=1)

        # ── Panel de etiquetas ────────────────────────────────────────────
        panel = QWidget()
        panel.setFixedWidth(270)
        panel.setStyleSheet("background:#13131F;border-left:1px solid #2D2D3F;")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        # Búsqueda
        search_bar = QWidget()
        search_bar.setStyleSheet("background:#13131F;padding:8px;")
        sl = QHBoxLayout(search_bar)
        sl.setContentsMargins(10, 8, 10, 8)
        self.tag_search = QLineEdit()
        self.tag_search.setPlaceholderText("Buscar etiqueta…")
        self.tag_search.textChanged.connect(self._filter_tags)
        sl.addWidget(self.tag_search)
        pl.addWidget(search_bar)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;"); pl.addWidget(sep)

        # Lista de etiquetas
        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_container = QWidget()
        self.tag_vbox      = QVBoxLayout(self.tag_container)
        self.tag_vbox.setContentsMargins(8, 4, 8, 4)
        self.tag_vbox.setSpacing(1)
        self.tag_scroll.setWidget(self.tag_container)
        pl.addWidget(self.tag_scroll, stretch=1)

        # Pie del panel
        footer = QWidget()
        footer.setStyleSheet("background:#0D0D1A;border-top:1px solid #2D2D3F;padding:6px;")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(4)
        for key, desc in [("Space", "saltar sin cambios"), ("Ctrl+Z", "deshacer")]:
            row = QHBoxLayout()
            row.addWidget(self._kbd(key))
            lbl = QLabel(desc); lbl.setStyleSheet("color:#8888AA;font-size:11px;")
            row.addWidget(lbl); row.addStretch()
            fl.addLayout(row)
        pl.addWidget(footer)

        bl.addWidget(panel)
        root.addWidget(body, stretch=1)

        # Construir lista de etiquetas (estática, se filtra por visibilidad)
        self._build_tag_list()

    def _kbd(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:4px;"
            "color:#8888AA;font-size:11px;padding:2px 6px;"
        )
        return lbl

    def _build_tag_list(self):
        """Construye los widgets de etiquetas una sola vez."""
        self._tag_rows: list[tuple[QWidget, Tag, QLabel, QLabel]] = []
        shortcut_n = 1

        all_tags = services.get_all_tags(include_sidebar_hidden=True)

        # Agrupar por categoría
        from collections import OrderedDict
        groups: OrderedDict[str, list[Tag]] = OrderedDict()
        for tag in all_tags:
            groups.setdefault(tag.category or "general", []).append(tag)

        for category, tags in groups.items():
            cat_lbl = QLabel(category.upper())
            cat_lbl.setStyleSheet(
                "color:#4A6A88;font-size:10px;font-weight:500;"
                "padding:8px 4px 2px;letter-spacing:1px;"
            )
            cat_lbl.setProperty("cat_label", True)
            self.tag_vbox.addWidget(cat_lbl)

            for tag in tags:
                row = QWidget()
                row.setStyleSheet("border-radius:5px;")
                hl  = QHBoxLayout(row)
                hl.setContentsMargins(4, 3, 6, 3)
                hl.setSpacing(7)

                dot = QLabel("●")
                dot.setStyleSheet(f"color:{tag.color};font-size:11px;")
                dot.setFixedWidth(14)
                hl.addWidget(dot)

                name_lbl = QLabel(tag.name)
                name_lbl.setStyleSheet("color:#D0D0E8;font-size:12px;")
                hl.addWidget(name_lbl, stretch=1)

                # Atajo de teclado 1-9
                kbd_lbl = QLabel("")
                kbd_lbl.setStyleSheet(
                    "background:#2D2D3F;border:0.5px solid #4A4A6A;border-radius:3px;"
                    "color:#8888AA;font-size:10px;padding:1px 5px;"
                )
                kbd_lbl.setFixedWidth(22)
                kbd_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if shortcut_n <= 9:
                    kbd_lbl.setText(str(shortcut_n))
                    self._tag_shortcuts[shortcut_n] = tag
                    shortcut_n += 1
                hl.addWidget(kbd_lbl)

                # Check activo
                check_lbl = QLabel("✓")
                check_lbl.setStyleSheet("color:#4A9EFF;font-size:14px;")
                check_lbl.setFixedWidth(16)
                check_lbl.setVisible(False)
                hl.addWidget(check_lbl)

                row.mousePressEvent = lambda e, t=tag: self._toggle_tag(t)
                row.setCursor(Qt.CursorShape.PointingHandCursor)

                self.tag_vbox.addWidget(row)
                self._tag_rows.append((row, tag, name_lbl, check_lbl))

        self.tag_vbox.addStretch()

    # ── Carga de foto ─────────────────────────────────────────────────────────

    def _load_current(self):
        if not self.photos:
            return

        photo = self.photos[self.index]
        total = len(self.photos)

        # Progreso
        self.progress_lbl.setText(f"{self.index + 1} / {total}")
        self.filename_lbl.setText(photo.filename)

        # Botones de navegación
        self.btn_prev.setEnabled(self.index > 0)
        self.btn_next.setEnabled(self.index < total - 1)

        # Imagen
        self.img_label.clear()
        if photo.is_video:
            jpeg = thumbnail_cache.get_video_thumbnail(photo.path, size=480)
        else:
            # Para el modo rápido mostramos imagen a mayor resolución
            jpeg = thumbnail_cache.get_thumbnail(photo.path, size=480)

        if jpeg:
            pix = QPixmap()
            pix.loadFromData(jpeg)
            if not pix.isNull():
                pix = pix.scaled(520, 520,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                self.img_label.setPixmap(pix)
        else:
            self.img_label.setText("No se pudo cargar la imagen")

        # Actualizar estado de etiquetas
        self._current_tag_ids = {t.id for t in services.get_photo_tags(photo.id)}
        self._refresh_tag_ui()

    def _refresh_tag_ui(self):
        """Actualiza checkmarks y chips sin recargar toda la lista."""
        for row, tag, _name_lbl, check_lbl in self._tag_rows:
            active = tag.id in self._current_tag_ids
            check_lbl.setVisible(active)
            row.setStyleSheet(
                "border-radius:5px;background:#1E2E4E;" if active
                else "border-radius:5px;"
            )

        # Chips bajo la imagen
        for i in reversed(range(self.chips_layout.count())):
            w = self.chips_layout.itemAt(i).widget()
            if w: w.deleteLater()

        for tag_id in self._current_tag_ids:
            all_tags = services.get_all_tags(include_sidebar_hidden=True)
            tag = next((t for t in all_tags if t.id == tag_id), None)
            if not tag: continue
            chip = QLabel(tag.name)
            chip.setStyleSheet(
                f"background:{tag.color}22;color:{tag.color};"
                f"border:1px solid {tag.color};border-radius:10px;"
                f"padding:2px 8px;font-size:11px;"
            )
            self.chips_layout.addWidget(chip)

    def _filter_tags(self, text: str):
        """Muestra/oculta filas según búsqueda."""
        text = text.lower()
        for row, tag, _name_lbl, _check_lbl in self._tag_rows:
            row.setVisible(not text or text in tag.name)
        # Ocultar headers de categoría si todos sus tags están ocultos
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if w and w.property("cat_label"):
                # Buscar siguiente widget que sea una fila de tag
                any_visible = False
                for j in range(i + 1, self.tag_vbox.count()):
                    nw = self.tag_vbox.itemAt(j).widget()
                    if nw and not nw.property("cat_label") and nw.isVisible():
                        any_visible = True
                        break
                    if nw and nw.property("cat_label"):
                        break
                w.setVisible(any_visible)

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _toggle_tag(self, tag: Tag):
        photo = self.photos[self.index]
        if tag.id in self._current_tag_ids:
            services.remove_tag(photo.id, tag.id)
            self._current_tag_ids.discard(tag.id)
            self._history.append((photo.id, tag.id, False))  # False = se quitó
        else:
            services.add_tag(photo.id, tag.name)
            self._current_tag_ids.add(tag.id)
            self._history.append((photo.id, tag.id, True))   # True = se agregó
        self._refresh_tag_ui()

    def _undo(self):
        if not self._history:
            return
        photo_id, tag_id, was_added = self._history.pop()
        if was_added:
            # Se había agregado → quitar
            services.remove_tag(photo_id, tag_id)
        else:
            # Se había quitado → volver a agregar
            all_tags = services.get_all_tags(include_sidebar_hidden=True)
            tag = next((t for t in all_tags if t.id == tag_id), None)
            if tag:
                services.add_tag(photo_id, tag.name)
        # Si el undo fue en la foto actual, refrescar UI
        if self.photos[self.index].id == photo_id:
            self._current_tag_ids = {t.id for t in services.get_photo_tags(photo_id)}
            self._refresh_tag_ui()

    def _prev(self):
        if self.index > 0:
            self.index -= 1
            self._load_current()

    def _next(self):
        if self.index < len(self.photos) - 1:
            self.index += 1
            self._load_current()
        else:
            self._finish()

    def _finish(self):
        QMessageBox.information(
            self, "¡Listo!",
            f"Etiquetado completado.\n{len(self.photos):,} fotos procesadas."
        )
        self.close()

    # ── Teclado ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        key  = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Left:
            self._prev()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self._next()
        elif mods == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Z:
            self._undo()
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            n   = key - Qt.Key.Key_0
            tag = self._tag_shortcuts.get(n)
            if tag:
                self._toggle_tag(tag)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.done_signal.emit()
        super().closeEvent(event)


# ─── Dialog: Configuración ────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, current_page_size: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setFixedSize(440, 320)
        self.setStyleSheet(DARK_STYLE)
        self.page_size = current_page_size
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(QLabel("<b>Configuración de visualización</b>"))

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Fotos por página:"))
        self.spin = QSpinBox()
        self.spin.setRange(10, 500); self.spin.setSingleStep(10)
        self.spin.setValue(self.page_size); self.spin.setFixedWidth(80)
        r1.addWidget(self.spin); r1.addStretch()
        layout.addLayout(r1)

        info = QLabel("💡 Recomendado: 50-100 para colecciones grandes.")
        info.setStyleSheet("color:#666;font-size:11px;")
        layout.addWidget(info)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;"); layout.addWidget(sep)

        layout.addWidget(QLabel("<b>Caché de miniaturas</b>"))
        self.cache_lbl = QLabel()
        self._refresh_cache_label()
        self.cache_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        layout.addWidget(self.cache_lbl)

        cache_row = QHBoxLayout()
        btn_clear = QPushButton("🗑  Limpiar caché")
        btn_clear.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        btn_clear.clicked.connect(self._clear_cache)

        btn_purge = QPushButton("🧹  Purgar huérfanos")
        btn_purge.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        btn_purge.setToolTip("Elimina miniaturas de archivos que ya no están indexados")
        btn_purge.clicked.connect(self._purge_orphans)

        cache_row.addWidget(btn_clear); cache_row.addWidget(btn_purge)
        layout.addLayout(cache_row)

        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancelar"); btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Aplicar")
        btn_ok.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        btn_ok.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel); btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _refresh_cache_label(self):
        self.cache_lbl.setText(f"Tamaño del caché: {thumbnail_cache.cache_size_mb()} MB")

    def _clear_cache(self):
        import shutil
        if thumbnail_cache.CACHE_DIR.exists():
            shutil.rmtree(thumbnail_cache.CACHE_DIR)
        self._refresh_cache_label()
        QMessageBox.information(self, "Caché limpiado",
            "El caché fue eliminado. Se regenerará al navegar la galería.")

    def _purge_orphans(self):
        n = services.purge_cache_orphans()
        self._refresh_cache_label()
        QMessageBox.information(self, "Purga completada",
            f"Se eliminaron {n} miniaturas huérfanas del caché.")

    def _apply(self):
        self.page_size = self.spin.value()
        self.accept()


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
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.container = QWidget(); self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(6); self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, stretch=1)
        info = QLabel("💡 Al eliminar una categoría sus etiquetas se mueven a 'general'.")
        info.setStyleSheet("color:#666;font-size:10px;"); layout.addWidget(info)

    def _refresh(self):
        for i in reversed(range(self.vbox.count())):
            w = self.vbox.itemAt(i).widget()
            if w: w.deleteLater()
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

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.container = QWidget(); self.grid = QVBoxLayout(self.container)
        self.grid.setSpacing(4); self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

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
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w: w.deleteLater()
        for tag in services.get_all_tags():
            row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(4,2,4,2)
            dot = QLabel("●"); dot.setStyleSheet(f"color:{tag.color};font-size:16px;")
            hl.addWidget(dot)
            lbl = QLabel(f"<b>{tag.name}</b>  <span style='color:#666;'>[{tag.category}]</span>")
            lbl.setTextFormat(Qt.TextFormat.RichText); hl.addWidget(lbl, stretch=1)
            chk1 = QCheckBox("Ocultar fotos"); chk1.setChecked(tag.hidden)
            chk1.stateChanged.connect(lambda s, tid=tag.id: services.set_tag_hidden(tid, bool(s)))
            hl.addWidget(chk1)
            chk2 = QCheckBox("Ocultar sidebar"); chk2.setChecked(tag.sidebar_hidden)
            chk2.stateChanged.connect(lambda s, tid=tag.id: services.set_tag_sidebar_hidden(tid, bool(s)))
            hl.addWidget(chk2)
            btn_d = QPushButton("Eliminar"); btn_d.setFixedWidth(70)
            btn_d.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;")
            btn_d.clicked.connect(lambda _, tid=tag.id: (services.delete_tag(tid), self._refresh()))
            hl.addWidget(btn_d)
            row.setStyleSheet("background:#1E1E2E;border-radius:6px;")
            self.grid.addWidget(row)
        self.grid.addStretch()

    def _open_cats(self):
        CategoryManagerDialog(self).exec(); self._refresh()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar etiquetas", "photovault_tags.json",
            "JSON (*.json)"
        )
        if not path: return
        try:
            n = services.export_tags(path)
            QMessageBox.information(self, "Exportado", f"Se exportaron {n} etiquetas a:\n{path}")
        except Exception as e:
            logger.exception("Error exportando etiquetas a %s", path)
            QMessageBox.critical(self, "Error", str(e))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar etiquetas", "", "JSON (*.json)"
        )
        if not path: return
        try:
            created, skipped = services.import_tags(path)
            QMessageBox.information(self, "Importado",
                f"Etiquetas creadas: {created}\nYa existían (omitidas): {skipped}")
            self._refresh()
        except Exception as e:
            logger.exception("Error importando etiquetas desde %s", path)
            QMessageBox.critical(self, "Error al importar", str(e))


# ─── Dialog: Indexar carpeta ──────────────────────────────────────────────────

class IndexDialog(QDialog):
    indexing_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indexar carpeta")
        self.setFixedSize(500, 200); self.setStyleSheet(DARK_STYLE)
        self.worker = None; self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(16,16,16,16); layout.setSpacing(10)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(); self.path_edit.setPlaceholderText("Ruta de la carpeta…")
        btn_b = QPushButton("Examinar"); btn_b.clicked.connect(self._browse)
        row.addWidget(self.path_edit); row.addWidget(btn_b); layout.addLayout(row)
        self.progress = QProgressBar(); self.progress.setRange(0,100); layout.addWidget(self.progress)
        self.status = QLabel("Listo para indexar.")
        self.status.setStyleSheet("color:#8888AA;font-size:10px;"); layout.addWidget(self.status)
        self.btn_start = QPushButton("▶  Iniciar indexación")
        self.btn_start.clicked.connect(self._start); layout.addWidget(self.btn_start)

    def _browse(self):
        f = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta")
        if f: self.path_edit.setText(f)

    def _start(self):
        folder = self.path_edit.text().strip()
        if not folder or not Path(folder).exists():
            QMessageBox.warning(self, "Error", "Selecciona una carpeta válida."); return
        self.btn_start.setEnabled(False)
        self.worker = IndexWorker(folder)
        self.worker.progress.connect(lambda c,t,p: (
            self.progress.setValue(int(c/t*100)),
            self.status.setText(f"[{c}/{t}] {Path(p).name}")
        ))
        self.worker.finished.connect(lambda a,s,e: (
            self.status.setText(f"✓ Listo: {a} indexadas, {e} errores."),
            self.btn_start.setEnabled(True),
            self.indexing_done.emit()
        ))
        self.worker.error.connect(lambda m: (
            QMessageBox.critical(self,"Error",m), self.btn_start.setEnabled(True)
        ))
        self.worker.start()


# ─── Dialog: Des-indexar carpetas ─────────────────────────────────────────────

class DeindexDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Des-indexar carpetas")
        self.setMinimumSize(560, 420); self.setStyleSheet(DARK_STYLE)
        self._build_ui(); self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(16,16,16,16); layout.setSpacing(10)
        layout.addWidget(QLabel("<b>Carpetas indexadas</b>"))
        layout.addWidget(QLabel("Los archivos originales NO se borran del disco."))
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.container = QWidget(); self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(4); self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, stretch=1)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;"); layout.addWidget(sep)
        btn = QPushButton("🧹  Eliminar registros de archivos que ya no existen en disco")
        btn.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        btn.clicked.connect(self._remove_missing); layout.addWidget(btn)

    def _refresh(self):
        for i in reversed(range(self.vbox.count())):
            w = self.vbox.itemAt(i).widget()
            if w: w.deleteLater()
        folders = services.get_indexed_folders()
        if not folders:
            lbl = QLabel("No hay carpetas indexadas.")
            lbl.setStyleSheet("color:#666;"); self.vbox.addWidget(lbl)
        else:
            for folder, count in folders:
                row = QWidget(); row.setStyleSheet("background:#1E1E2E;border-radius:6px;")
                hl  = QHBoxLayout(row); hl.setContentsMargins(8,6,8,6)
                lbl = QLabel(f"<b>{folder}</b>  <span style='color:#666;'>{count:,} archivos</span>")
                lbl.setTextFormat(Qt.TextFormat.RichText); hl.addWidget(lbl, stretch=1)
                btn = QPushButton("Eliminar"); btn.setFixedWidth(75)
                btn.setStyleSheet("color:#FF4A4A;border:1px solid #FF4A4A;")
                btn.clicked.connect(lambda _, f=folder: self._deindex(f))
                hl.addWidget(btn); self.vbox.addWidget(row)
        self.vbox.addStretch()

    def _deindex(self, folder: str):
        if QMessageBox.question(self, "Confirmar",
            f"¿Eliminar registros de:\n{folder}\n\nLos archivos NO se borrarán.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            services.deindex_folder(folder); self._refresh()

    def _remove_missing(self):
        count = services.remove_missing_files()
        msg   = "No se encontraron archivos faltantes." if count == 0 else \
                f"Se eliminaron {count} registros."
        QMessageBox.information(self, "Listo", msg)
        if count: self._refresh()


# ─── Ventana principal ────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoVault")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(DARK_STYLE)

        self._active_tags:    list[int]                 = []
        self._offset:         int                       = 0
        self._page_size:      int                       = 100
        self._sort_field:     SortField                 = SortField.DATE
        self._sort_order:     SortOrder                 = SortOrder.DESC
        self._current_page:   GalleryPage | None        = None
        self._thumbnails:     dict[int, PhotoThumbnail] = {}
        self._pending_photos: list                      = []
        self._grid_cols:      int                       = 4
        self._loader:         ThumbnailLoader | None    = None
        self._select_mode:    bool                      = False
        self._selected_ids:   set[int]                  = set()

        self._build_timer  = QTimer(self)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._add_next_batch)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(400)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_grid_cols = 0

        # db.init_db() corre antes, en _startup()
        self._build_ui()
        self._refresh_tags()
        QTimer.singleShot(100, self._load_photos)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root    = QHBoxLayout(central)
        root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = QWidget(); sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background:#13131F;border-right:1px solid #2D2D3F;")
        sb = QVBoxLayout(sidebar); sb.setContentsMargins(10,16,10,16); sb.setSpacing(8)

        logo = QLabel("📸 PhotoVault")
        logo.setStyleSheet("font-size:18px;font-weight:bold;color:#4A9EFF;margin-bottom:8px;")
        sb.addWidget(logo)

        for label, slot in [
            ("＋ Indexar carpeta",     self._open_index_dialog),
            ("🏷  Gestionar etiquetas", self._open_tag_manager),
            ("🗂  Des-indexar",         self._open_deindex_dialog),
            ("📊  Estadísticas",        self._open_stats_dialog),
            ("🔍  Duplicados",          self._open_duplicates_dialog),
            ("⚙  Configuración",       self._open_settings_dialog),
        ]:
            btn = QPushButton(label); btn.clicked.connect(slot); sb.addWidget(btn)

        # Botón de etiquetado rápido — destacado visualmente
        btn_qt = QPushButton("⚡  Etiquetado rápido")
        btn_qt.setStyleSheet(
            "QPushButton{background:#4A9EFF22;color:#4A9EFF;"
            "border:1px solid #4A9EFF;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#4A9EFF44;}"
        )
        btn_qt.clicked.connect(self._open_quick_tag)
        sb.addWidget(btn_qt)

        if False:  # dummy para que el for de abajo no duplique los botones
            pass
        for label, slot in []:
            btn = QPushButton(label); btn.clicked.connect(slot); sb.addWidget(btn)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#2D2D3F;"); sb.addWidget(sep)
        sb.addWidget(QLabel("Filtrar por etiqueta:"))

        self.tag_scroll = QScrollArea(); self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tag_widget = QWidget(); self.tag_vbox = QVBoxLayout(self.tag_widget)
        self.tag_vbox.setContentsMargins(0,0,0,0); self.tag_vbox.setSpacing(2)
        self.tag_scroll.setWidget(self.tag_widget)
        sb.addWidget(self.tag_scroll, stretch=1)

        btn_clear = QPushButton("✕ Limpiar filtros")
        btn_clear.setStyleSheet("color:#FF4A4A;"); btn_clear.clicked.connect(self._clear_filters)
        sb.addWidget(btn_clear)

        self.stats_lbl = QLabel(""); self.stats_lbl.setStyleSheet("color:#666;font-size:10px;")
        sb.addWidget(self.stats_lbl)
        root.addWidget(sidebar)

        # ── Área principal ─────────────────────────────────────────────────
        main_area = QWidget(); ml = QVBoxLayout(main_area)
        ml.setContentsMargins(12,12,12,12); ml.setSpacing(8)

        # Barra superior
        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Buscar por nombre…")
        self.search_edit.textChanged.connect(self._on_search)
        top_bar.addWidget(self.search_edit)

        # ── FEATURE: ordenamiento ─────────────────────────────────────────
        top_bar.addWidget(QLabel("Ordenar:"))
        self.sort_field_combo = QComboBox()
        for f in SortField:
            self.sort_field_combo.addItem(f.value, userData=f)
        self.sort_field_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_bar.addWidget(self.sort_field_combo)

        self.sort_order_combo = QComboBox()
        self.sort_order_combo.addItem("↓ desc", userData=SortOrder.DESC)
        self.sort_order_combo.addItem("↑ asc",  userData=SortOrder.ASC)
        self.sort_order_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_bar.addWidget(self.sort_order_combo)

        self.count_lbl = QLabel("0 fotos"); self.count_lbl.setStyleSheet("color:#8888AA;")
        top_bar.addWidget(self.count_lbl)

        # ── FEATURE: modo selección ───────────────────────────────────────
        self.btn_select = QPushButton("☐ Seleccionar")
        self.btn_select.setCheckable(True)
        self.btn_select.setStyleSheet("color:#FFD700;border:1px solid #FFD700;")
        self.btn_select.toggled.connect(self._toggle_select_mode)
        top_bar.addWidget(self.btn_select)

        self.btn_bulk_tag = QPushButton("🏷 Etiquetar selección")
        self.btn_bulk_tag.setStyleSheet("background:#4A9EFF22;color:#4A9EFF;border:1px solid #4A9EFF;")
        self.btn_bulk_tag.setVisible(False)
        self.btn_bulk_tag.clicked.connect(self._open_bulk_tag)
        top_bar.addWidget(self.btn_bulk_tag)

        ml.addLayout(top_bar)

        self.scroll_area = QScrollArea(); self.scroll_area.setWidgetResizable(True)
        self.grid_widget = QWidget(); self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(8); self.scroll_area.setWidget(self.grid_widget)
        ml.addWidget(self.scroll_area, stretch=1)

        pg_bar = QHBoxLayout()
        self.prev_btn = QPushButton("← Anterior"); self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn = QPushButton("Siguiente →"); self.next_btn.clicked.connect(self._next_page)
        self.page_lbl = QLabel(""); self.page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pg_bar.addWidget(self.prev_btn); pg_bar.addWidget(self.page_lbl, stretch=1)
        pg_bar.addWidget(self.next_btn)
        ml.addLayout(pg_bar)

        root.addWidget(main_area, stretch=1)

    # ── Sidebar de etiquetas ──────────────────────────────────────────────────

    def _refresh_tags(self):
        for i in reversed(range(self.tag_vbox.count())):
            w = self.tag_vbox.itemAt(i).widget()
            if w: w.deleteLater()

        for category, tags in services.get_sidebar_tags().items():
            header = QPushButton(f"▾  {category.upper()}")
            header.setCheckable(True); header.setChecked(True)
            header.setStyleSheet("""
                QPushButton { background:#1A1A2E;color:#6688AA;border:none;
                    border-top:1px solid #2D2D3F;border-radius:0;
                    text-align:left;padding:4px 6px;font-size:10px;
                    font-weight:bold;letter-spacing:1px; }
                QPushButton:hover { color:#88AACC;background:#1E1E2E; }
            """)
            group_widget = QWidget(); gv = QVBoxLayout(group_widget)
            gv.setContentsMargins(8,0,0,4); gv.setSpacing(1)
            for tag in tags:
                row = QWidget(); hl = QHBoxLayout(row)
                hl.setContentsMargins(0,0,0,0); hl.setSpacing(4)
                chk = QCheckBox(tag.name); chk.setStyleSheet(f"color:{tag.color};")
                chk.setProperty("tag_id", tag.id)
                if tag.id in self._active_tags: chk.setChecked(True)
                chk.stateChanged.connect(self._on_tag_filter_changed)
                hl.addWidget(chk, stretch=1)
                eye = QPushButton("👁" if not tag.sidebar_hidden else "🚫")
                eye.setFixedSize(22,22)
                eye.setStyleSheet("QPushButton{background:transparent;border:none;font-size:11px;padding:0;}"
                                  "QPushButton:hover{background:#2D2D3F;border-radius:4px;}")
                eye.clicked.connect(lambda _, tid=tag.id, cur=tag.sidebar_hidden:
                    self._toggle_sidebar_hidden(tid, cur))
                hl.addWidget(eye); gv.addWidget(row)
            header.toggled.connect(lambda checked, gw=group_widget, btn=header: (
                gw.setVisible(checked),
                btn.setText(f"{'▾' if checked else '▸'}  {btn.text()[2:]}")
            ))
            self.tag_vbox.addWidget(header); self.tag_vbox.addWidget(group_widget)

        self.tag_vbox.addStretch()
        self._update_stats()

    def _toggle_sidebar_hidden(self, tag_id: int, currently_hidden: bool):
        services.set_tag_sidebar_hidden(tag_id, not currently_hidden)
        self._refresh_tags()

    def _on_tag_filter_changed(self):
        self._active_tags = []
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if not w: continue
            for chk in w.findChildren(QCheckBox):
                if chk.isChecked() and chk.property("tag_id") is not None:
                    self._active_tags.append(chk.property("tag_id"))
        self._offset = 0; self._load_photos()

    def _clear_filters(self):
        self._active_tags = []
        for i in range(self.tag_vbox.count()):
            w = self.tag_vbox.itemAt(i).widget()
            if not w: continue
            for chk in w.findChildren(QCheckBox):
                chk.blockSignals(True); chk.setChecked(False); chk.blockSignals(False)
        self._offset = 0; self._load_photos()

    # ── Ordenamiento ──────────────────────────────────────────────────────────

    def _on_sort_changed(self):
        self._sort_field = self.sort_field_combo.currentData()
        self._sort_order = self.sort_order_combo.currentData()
        self._offset = 0; self._load_photos()

    # ── Modo selección ────────────────────────────────────────────────────────

    def _toggle_select_mode(self, checked: bool):
        self._select_mode  = checked
        self._selected_ids = set()
        self.btn_bulk_tag.setVisible(checked)
        self.btn_select.setText("✓ Cancelar selección" if checked else "☐ Seleccionar")
        self._load_photos()

    def _on_thumb_selected(self, photo_id: int, is_selected: bool):
        if is_selected:
            self._selected_ids.add(photo_id)
        else:
            self._selected_ids.discard(photo_id)
        n = len(self._selected_ids)
        self.btn_bulk_tag.setText(f"🏷 Etiquetar {n} fotos" if n else "🏷 Etiquetar selección")

    def _open_bulk_tag(self):
        if not self._selected_ids:
            QMessageBox.information(self, "Sin selección", "Selecciona al menos una foto.")
            return
        dlg = BulkTagDialog(list(self._selected_ids), self)
        if dlg.exec():
            self._selected_ids.clear()
            self._toggle_select_mode(False)
            self.btn_select.setChecked(False)

    # ── Carga de fotos ────────────────────────────────────────────────────────

    def _stop_loader(self):
        if self._loader is not None:
            self._loader.loaded.disconnect()
            try: self._loader.finished.disconnect()
            except TypeError: pass  # No tenía conexiones
            self._loader.stop(); self._loader.wait(500)
            self._loader = None

    def _load_photos(self):
        self._build_timer.stop(); self._stop_loader()
        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w: w.deleteLater()
        self._thumbnails.clear()

        search = self.search_edit.text().strip() or None
        self._current_page = services.get_gallery_page(
            tag_ids    = self._active_tags or None,
            search     = search,
            limit      = self._page_size,
            offset     = self._offset,
            sort_field = self._sort_field,
            sort_order = self._sort_order,
        )

        self.count_lbl.setText(f"{self._current_page.total:,} fotos")
        self._update_pagination()

        new_cols = max(1, (self.scroll_area.width() - 30) // 218)
        self._grid_cols = self._last_grid_cols = new_cols
        self._pending_photos = list(enumerate(self._current_page.photos))
        self._build_timer.start()

    def _add_next_batch(self):
        BATCH = 10
        batch, self._pending_photos = self._pending_photos[:BATCH], self._pending_photos[BATCH:]
        for idx, photo in batch:
            thumb = PhotoThumbnail(photo, selectable=self._select_mode)
            if self._select_mode:
                thumb.selected.connect(self._on_thumb_selected)
                if photo.id in self._selected_ids:
                    thumb.set_selected(True)
            else:
                thumb.clicked.connect(self._open_photo)
            self.grid_layout.addWidget(thumb, idx // self._grid_cols, idx % self._grid_cols)
            self._thumbnails[photo.id] = thumb

        if not self._pending_photos:
            self._build_timer.stop()
            self._loader = ThumbnailLoader(self._current_page.photos)
            self._loader.loaded.connect(self._on_thumb_loaded)
            self._loader.finished.connect(lambda: setattr(self, "_loader", None))
            self._loader.start()

    def _on_thumb_loaded(self, photo_id: int, pix: QPixmap):
        if photo_id in self._thumbnails:
            self._thumbnails[photo_id].set_pixmap(pix)

    def _on_search(self):
        self._offset = 0; self._load_photos()

    # ── Paginación ────────────────────────────────────────────────────────────

    def _update_pagination(self):
        if not self._current_page: return
        p = self._current_page
        self.page_lbl.setText(f"Página {p.page_number} / {p.total_pages}")
        self.prev_btn.setEnabled(p.has_prev)
        self.next_btn.setEnabled(p.has_next)

    def _prev_page(self):
        self._offset = max(0, self._offset - self._page_size); self._load_photos()

    def _next_page(self):
        self._offset += self._page_size; self._load_photos()

    # ── Resize ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event); self._resize_timer.start()

    def _on_resize_settled(self):
        new_cols = max(1, (self.scroll_area.width() - 30) // 218)
        if new_cols != self._last_grid_cols:
            self._last_grid_cols = new_cols; self._load_photos()

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _open_photo(self, photo_id: int):
        if not self._current_page: return
        photo = next((p for p in self._current_page.photos if p.id == photo_id), None)
        if not photo: return
        dlg = PhotoDetailDialog(photo, self)
        dlg.tags_changed.connect(self._load_photos); dlg.exec()

    def _open_index_dialog(self):
        dlg = IndexDialog(self); dlg.indexing_done.connect(self._on_index_done); dlg.exec()

    def _on_index_done(self):
        self._refresh_tags(); self._load_photos()

    def _open_tag_manager(self):
        TagManagerDialog(self).exec(); self._refresh_tags(); self._load_photos()

    def _open_settings_dialog(self):
        dlg = SettingsDialog(self._page_size, self)
        if dlg.exec():
            self._page_size = dlg.page_size; self._offset = 0; self._load_photos()

    def _open_deindex_dialog(self):
        DeindexDialog(self).exec(); self._load_photos()

    def _open_stats_dialog(self):
        StatsDialog(self).exec()

    def _open_duplicates_dialog(self):
        DuplicatesDialog(self).exec(); self._load_photos()

    def _update_stats(self):
        stats = services.get_stats()
        self.stats_lbl.setText(f"{stats.total_photos:,} fotos  •  {stats.total_tags} etiquetas")

    def _open_quick_tag(self):
        setup = QuickTagSetupDialog(self)
        if setup.exec() != QDialog.DialogCode.Accepted:
            return
        photos = services.get_photos_for_tagging(
            folder        = setup.selected_folder,
            tag_ids       = setup.selected_tag_ids or None,
            untagged_only = setup.untagged_only,
        )
        if not photos:
            QMessageBox.information(self, "Sin fotos",
                "No se encontraron fotos con ese filtro.")
            return
        win = QuickTagWindow(photos, self)
        win.done_signal.connect(self._load_photos)
        win.exec()

    def closeEvent(self, event):
        self._build_timer.stop(); self._stop_loader()
        db.close_connection(); super().closeEvent(event)


# ─── Estilos ──────────────────────────────────────────────────────────────────

DARK_STYLE = """
    * { font-family:'Segoe UI',sans-serif; font-size:13px; }
    QMainWindow,QDialog,QWidget { background:#0D0D1A; color:#D0D0E8; }
    QPushButton { background:#1E1E2E;color:#D0D0E8;border:1px solid #3A3A5A;
                  border-radius:6px;padding:6px 12px; }
    QPushButton:hover   { background:#2A2A3E;border-color:#4A9EFF; }
    QPushButton:pressed { background:#4A9EFF33; }
    QPushButton:disabled { color:#444;border-color:#222; }
    QLineEdit,QComboBox { background:#1E1E2E;color:#D0D0E8;border:1px solid #3A3A5A;
                          border-radius:6px;padding:6px 10px; }
    QLineEdit:focus,QComboBox:focus { border-color:#4A9EFF; }
    QScrollArea { border:none; }
    QScrollBar:vertical { background:#13131F;width:8px;border-radius:4px; }
    QScrollBar::handle:vertical { background:#3A3A5A;border-radius:4px;min-height:20px; }
    QScrollBar::handle:vertical:hover { background:#4A9EFF; }
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
    QProgressBar { background:#1E1E2E;border:1px solid #3A3A5A;border-radius:4px;
                   text-align:center;color:#D0D0E8; }
    QProgressBar::chunk { background:#4A9EFF;border-radius:4px; }
    QGroupBox { border:1px solid #3A3A5A;border-radius:6px;margin-top:8px;
                padding-top:8px;color:#8888AA;font-size:11px; }
    QCheckBox { color:#D0D0E8;spacing:6px; }
    QCheckBox::indicator { width:14px;height:14px;border:1px solid #3A3A5A;
                           border-radius:3px;background:#1E1E2E; }
    QCheckBox::indicator:checked { background:#4A9EFF;border-color:#4A9EFF; }
    QLabel { color:#D0D0E8; }
    QComboBox QAbstractItemView { background:#1E1E2E;border:1px solid #3A3A5A;
                                  selection-background-color:#4A9EFF33; }
"""

# ─── Entry point ──────────────────────────────────────────────────────────────

def _startup() -> bool:
    """
    Backup diario + migraciones de la DB. Devuelve False si la app no debe
    abrirse (el error ya se mostró al usuario).
    """
    backup.daily_backup(db.DB_PATH)
    try:
        db.init_db()
    except db.DatabaseTooNewError as e:
        logger.error("%s", e)
        QMessageBox.critical(None, "Versión de base de datos no compatible", str(e))
        return False
    except Exception as e:
        logger.exception("No se pudo inicializar la base de datos")
        QMessageBox.critical(
            None, "Error al abrir la base de datos",
            f"No se pudo preparar la base de datos:\n\n{e}\n\n"
            f"No se hicieron cambios. Detalles en:\n{logging_setup.LOG_FILE}",
        )
        return False
    return True


if __name__ == "__main__":
    logging_setup.setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("PhotoVault")
    logging_setup.install_qt_handlers()
    logger.info("PhotoVault iniciando (esquema DB v%d)", db.SCHEMA_VERSION)
    if not _startup():
        sys.exit(1)
    window = MainWindow()
    window.show()
    code = app.exec()
    logger.info("PhotoVault cerrado (código %d)", code)
    sys.exit(code)
