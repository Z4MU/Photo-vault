"""
PhotoVault - ui/gallery_actions.py
Acciones sobre la selección de la galería: menú contextual, copiar, mostrar
en el Explorador, etiquetar, valorar y favoritas. MainWindow las hereda.
"""

import logging
import os
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QMenu, QMessageBox, QPushButton, QTabWidget, QWidget

import services
from models import Photo
from ui import system
from ui.dialogs.photo import BulkTagDialog
from ui.gallery import GalleryModel, GalleryView, format_date
from ui.sidebar import TimelinePanel
from ui.style import DARK_STYLE
from ui.widgets import TaskStatusWidget

logger = logging.getLogger(__name__)

CONFIRM_OVER = 500  # cambiar estrellas/favoritas a más fotos que esto pide confirmación


class GalleryActionsMixin:
    """Necesita que la ventana tenga estos atributos y métodos (los define MainWindow)."""

    view: GalleryView
    model: GalleryModel
    task_status: TaskStatusWidget
    btn_bulk_tag: QPushButton
    tabs: QTabWidget
    timeline_panel: TimelinePanel

    def open_viewer(self, row: int) -> None:
        raise NotImplementedError

    def set_folder_filter(self, folder: str | None) -> None:
        raise NotImplementedError

    def go_to_date(self, year: int | None, month: int | None) -> None:
        raise NotImplementedError

    def reload_keep_position(self) -> None:
        raise NotImplementedError

    def _refresh_after_tag_change(self) -> None:
        raise NotImplementedError

    def _update_count(self) -> None:
        raise NotImplementedError

    def _widget(self) -> QWidget:
        return cast(QWidget, self)

    # ── Selección ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self, *_args) -> None:
        n = self.view.selected_count()
        self.btn_bulk_tag.setEnabled(n > 0)
        self.btn_bulk_tag.setText(f"🏷 Etiquetar {n:,} fotos" if n else "🏷 Etiquetar selección")
        self._update_count()

    def selected_ids(self) -> list[int]:
        return self.model.ids_in_ranges(self.view.selected_ranges())

    def _target_ids(self) -> list[int]:
        """La selección, o la foto actual si no hay selección."""
        ids = self.selected_ids()
        if not ids:
            idx = self.view.currentIndex()
            photo = self.model.photo_at(idx.row()) if idx.isValid() else None
            ids = [photo.id] if photo is not None else []
        return ids

    def _selected_or_current_photos(self, limit: int = 2000) -> list[Photo]:
        ranges = self.view.selected_ranges()
        if not ranges:
            idx = self.view.currentIndex()
            ranges = [(idx.row(), idx.row())] if idx.isValid() else []
        photos: list[Photo] = []
        for start, end in ranges:
            for row in range(start, min(end, start + limit - len(photos) - 1) + 1):
                p = self.model.photo_at(row)
                if p is not None:
                    photos.append(p)
            if len(photos) >= limit:
                break
        return photos

    def _confirm_many(self, n: int, action: str) -> bool:
        if n <= CONFIRM_OVER:
            return True
        return (
            QMessageBox.question(
                self._widget(),
                "Muchas fotos",
                f"¿{action} {n:,} fotos?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    # ── Acciones ──────────────────────────────────────────────────────────────

    def _open_bulk_tag(self) -> None:
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self._widget(), "Sin selección", "Selecciona al menos una foto.")
            return
        if BulkTagDialog(ids, self._widget()).exec():
            self._refresh_after_tag_change()

    def set_rating_selection(self, rating: int) -> None:
        ids = self._target_ids()
        what = f"Poner {'★' * rating} a" if rating else "Quitar la valoración a"
        if not ids or not self._confirm_many(len(ids), what):
            return
        services.set_rating(ids, rating)
        self.reload_keep_position()
        stars = "★" * rating if rating else "sin valoración"
        self.task_status.finish(
            f"{stars}: {len(ids):,} foto{'s' if len(ids) != 1 else ''}", hide_after_ms=3000
        )

    def toggle_favorite_selection(self) -> None:
        ids = self._target_ids()
        if not ids or not self._confirm_many(len(ids), "Cambiar «favorita» de"):
            return
        now = services.toggle_favorite(ids)
        self.reload_keep_position()
        n = f"{len(ids):,} foto{'s' if len(ids) != 1 else ''}"
        self.task_status.finish(
            f"♥ Favorita: {n}" if now else f"♡ Ya no es favorita: {n}", hide_after_ms=3000
        )

    def _copy_selection(self, as_files: bool) -> None:
        photos = self._selected_or_current_photos()
        system.copy_paths([p.path for p in photos], as_files=as_files)
        if photos:
            what = "archivos" if as_files else "rutas"
            self.task_status.finish(f"📋 {len(photos):,} {what} copiadas al portapapeles", hide_after_ms=3000)

    def _reveal_current(self) -> None:
        photos = self._selected_or_current_photos(limit=1)
        if photos:
            system.reveal_in_explorer(photos[0].path)

    def _on_drag_refused(self, n: int) -> None:
        self.task_status.finish(
            f"Son {n:,} fotos: para arrastrar a otra app selecciona como máximo 500", hide_after_ms=5000
        )

    def _show_in_timeline(self, photo: Photo) -> None:
        self.tabs.setCurrentWidget(self.timeline_panel)
        self.go_to_date(photo.year, photo.month)

    # ── Menú contextual ───────────────────────────────────────────────────────

    def build_context_menu(self, row: int) -> QMenu:
        menu = QMenu(self._widget())
        menu.setStyleSheet(DARK_STYLE)
        photo = self.model.photo_at(row)
        n = self.view.selected_count()
        if photo is None:
            return menu
        menu.addAction("👁  Abrir en el visor\tEnter", lambda: self.open_viewer(row))
        menu.addAction("↗  Abrir con la app predeterminada", lambda: system.open_external(photo.path))
        menu.addAction("📂  Mostrar en el Explorador\tCtrl+E", lambda: system.reveal_in_explorer(photo.path))
        menu.addSeparator()
        label = "rutas" if n > 1 else "ruta"
        menu.addAction(f"📋  Copiar {label}\tCtrl+C", lambda: self._copy_selection(as_files=False))
        menu.addAction("📄  Copiar archivos\tCtrl+Shift+C", lambda: self._copy_selection(as_files=True))
        menu.addSeparator()
        menu.addAction(
            f"🏷  Etiquetar {n:,} fotos…\tCtrl+T" if n > 1 else "🏷  Etiquetar…\tCtrl+T", self._open_bulk_tag
        )
        rating_menu = menu.addMenu("★  Valoración")
        if rating_menu is not None:
            for r in range(6):
                text = f"{'★' * r}{'☆' * (5 - r)}\t{r}" if r else "Sin valoración\t0"
                action = rating_menu.addAction(text, lambda r=r: self.set_rating_selection(r))
                if action is not None and n <= 1:
                    action.setCheckable(True)
                    action.setChecked(photo.rating == r)
        fav_text = "♡  Quitar de favoritas\tF" if photo.favorite and n <= 1 else "♥  Marcar como favorita\tF"
        menu.addAction(fav_text, self.toggle_favorite_selection)
        menu.addSeparator()
        folder = os.path.dirname(photo.path)
        menu.addAction(
            f"📁  Ver solo la carpeta «{Path(folder).name or folder}»", lambda: self.set_folder_filter(folder)
        )
        if photo.year:
            menu.addAction(
                f"📅  Ir a {format_date(photo.year, photo.month)} en la línea de tiempo",
                lambda: self._show_in_timeline(photo),
            )
        menu.addSeparator()
        menu.addAction("☑  Seleccionar todo\tCtrl+A", self.view.selectAll)
        return menu

    def _show_context_menu(self, pos: QPoint) -> None:
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        sm = self.view.selectionModel()
        if sm is not None and not sm.isSelected(idx):
            self.view.select_row(idx.row())  # Clic derecho fuera de la selección: selecciona esa
        viewport = self.view.viewport()
        if viewport is not None:
            self.build_context_menu(idx.row()).exec(viewport.mapToGlobal(pos))
