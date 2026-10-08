"""
PhotoVault - config.py
Constantes de la app en un solo lugar: rutas de datos, tamaños y paleta.

Los módulos copian estas rutas a un atributo propio (database.DB_PATH,
thumbnail_cache.CACHE_DIR, backup.BACKUP_DIR, logging_setup.LOG_DIR) para que
los tests puedan redirigirlas con monkeypatch. Cambia el valor aquí, no allá.
"""

from pathlib import Path

APP_NAME = "PhotoVault"
APP_VERSION = "2.3.0"

# ── Datos del usuario (fuera del repo) ────────────────────────────────────────
DATA_DIR = Path.home() / ".photovault"
DB_PATH = DATA_DIR / "photovault.db"
THUMBS_DIR = DATA_DIR / "thumbs"
# Miniaturas de las fotos ocultas, cifradas (fase 9)
PRIVATE_THUMBS_DIR = DATA_DIR / "thumbs_private"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"

# ── Miniaturas (lado máximo en píxeles) ───────────────────────────────────────
THUMB_SIZE_SMALL = 100  # vista de duplicados
THUMB_SIZE_GALLERY = 200  # galería
THUMB_SIZE_LARGE = 480  # etiquetado rápido, galería con miniaturas grandes
# Visor: lado máximo al decodificar (permite zoom al 100 % en fotos de hasta ~27 MP
# sin pasar de ~150 MB por imagen en memoria)
VIEWER_MAX_SIDE = 6000

# ── Paleta del tema oscuro (también usada en ui/dark.qss) ─────────────────────
COLORS = {
    "bg": "#0D0D1A",
    "panel": "#13131F",
    "panel_alt": "#1E1E2E",
    "border": "#2D2D3F",
    "border_alt": "#3A3A5A",
    "text": "#D0D0E8",
    "text_dim": "#8888AA",
    "accent": "#4A9EFF",
    "danger": "#FF4A4A",
    "warning": "#FFD700",
    "success": "#4AFF9E",
}
