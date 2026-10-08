"""
PhotoVault - ui/folder_watch.py
Vigila las carpetas elegidas mientras la app está abierta y avisa cuando
algo cambió, una vez que pasa un rato sin cambios (para no indexar archivos
que todavía se están copiando).
"""

import logging
import os

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

import services

logger = logging.getLogger(__name__)

SETTLE_MS = 30_000  # 30 s sin cambios = la copia terminó


class FolderWatcher(QObject):
    """`changed(raíz)` cuando cambió algo dentro de una carpeta vigilada (ya asentado)."""

    changed = pyqtSignal(str)

    def __init__(self, parent=None, settle_ms: int = SETTLE_MS):
        super().__init__(parent)
        self.settle_ms = settle_ms
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._roots: list[str] = []
        self._timers: dict[str, QTimer] = {}

    def roots(self) -> list[str]:
        return list(self._roots)

    def watched_dirs(self) -> list[str]:
        return self._watcher.directories()

    def set_roots(self, roots: list[str]) -> None:
        """
        Vigila `roots` y sus subcarpetas indexadas (las que no existen se saltan).
        Llamarlo con raíces que se sabe que existen (el disco ya está despierto).
        """
        current = self._watcher.directories()
        if current:
            self._watcher.removePaths(current)
        self._roots = [os.path.normpath(r) for r in roots]
        dirs = services.watch_dirs(self._roots)
        if dirs:
            failed = self._watcher.addPaths(dirs)
            if failed:
                logger.warning("No se pudieron vigilar %d carpetas (p. ej. %s)", len(failed), failed[0])
        for root in list(self._timers):
            if root not in self._roots:
                self._timers.pop(root).stop()
        logger.info("Vigilando %d carpetas en %d raíces", len(dirs), len(self._roots))

    def _root_of(self, path: str) -> str | None:
        path = os.path.normpath(path)
        matches = [r for r in self._roots if path == r or path.startswith(r.rstrip("\\/") + os.sep)]
        return max(matches, key=len) if matches else None

    def _on_dir_changed(self, path: str) -> None:
        root = self._root_of(path)
        if root is None:
            return
        timer = self._timers.get(root)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda r=root: self._emit(r))
            self._timers[root] = timer
        timer.start(self.settle_ms)  # cada cambio nuevo reinicia la espera

    def _emit(self, root: str) -> None:
        if os.path.isdir(root):  # el disco puede haberse desconectado mientras tanto
            self.changed.emit(root)

    def stop(self) -> None:
        for t in self._timers.values():
            t.stop()
        current = self._watcher.directories()
        if current:
            self._watcher.removePaths(current)
