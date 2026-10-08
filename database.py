"""
PhotoVault - database.py
Maneja toda la interacción con SQLite.
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import backup
import config
from models import Photo, SortField, SortOrder, Stats, Tag, TrashBatch, sort_to_sql

logger = logging.getLogger(__name__)

DB_PATH = config.DB_PATH

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-32000")  # ~32 MB por conexión
        # Con WAL, NORMAL es seguro ante cortes de la app (solo un corte de luz
        # puede perder la última transacción, nunca corromper la DB) y escribe
        # mucho más rápido que FULL.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        _local.conn = conn
    return conn


def close_connection():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            logger.warning("Error al cerrar la conexión SQLite", exc_info=True)
        _local.conn = None


class _Transaction:
    def __enter__(self):
        self._conn = get_connection()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False


def transaction() -> "_Transaction":
    return _Transaction()


# ── Inicialización y migraciones ──────────────────────────────────────────────
#
# La versión del esquema se guarda en PRAGMA user_version.
#   - Cada migración es una función _mNNN_* agregada al final de _MIGRATIONS.
#   - La versión N corresponde a _MIGRATIONS[N - 1].
#   - Cada migración corre en su propia transacción junto con el cambio de
#     user_version: o se aplica completa o no se aplica.
#   - Dentro de una migración NO usar executescript (hace COMMIT implícito).
#   - Nunca modificar una migración ya publicada: agregar una nueva.
#   - Antes de migrar una DB existente se crea un backup obligatorio.


class DatabaseTooNewError(RuntimeError):
    """La DB fue creada por una versión más nueva de PhotoVault."""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # table_xinfo (no table_info): también lista las columnas calculadas (photos.folder)
    return {r[1] for r in conn.execute(f"PRAGMA table_xinfo({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, typedef: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")


def _m001_baseline(conn: sqlite3.Connection) -> None:
    """Esquema base: equivale a todo lo que hacía init_db() hasta V11."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            path        TEXT UNIQUE NOT NULL,
            filename    TEXT NOT NULL,
            media_type  TEXT DEFAULT 'image',
            year        INTEGER,
            month       INTEGER,
            filesize    INTEGER,
            width       INTEGER,
            height      INTEGER,
            duration    REAL,
            md5         TEXT,
            added_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT UNIQUE NOT NULL COLLATE NOCASE,
            category       TEXT DEFAULT 'general',
            color          TEXT DEFAULT '#4A9EFF',
            hidden         INTEGER DEFAULT 0,
            sidebar_hidden INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photo_tags (
            photo_id    INTEGER REFERENCES photos(id) ON DELETE CASCADE,
            tag_id      INTEGER REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (photo_id, tag_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            name  TEXT PRIMARY KEY NOT NULL COLLATE NOCASE
        )
    """)
    # Configuración interna (ej: flag 'seeded' del seed inicial)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL
        )
    """)

    # Columnas agregadas en versiones anteriores — DBs viejas pueden no tenerlas.
    # Deben existir ANTES de crear índices que las usen.
    _add_column_if_missing(conn, "photos", "media_type", "TEXT DEFAULT 'image'")
    _add_column_if_missing(conn, "photos", "duration", "REAL")
    _add_column_if_missing(conn, "photos", "md5", "TEXT")
    _add_column_if_missing(conn, "tags", "hidden", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "tags", "sidebar_hidden", "INTEGER DEFAULT 0")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_year      ON photos(year)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_month     ON photos(month)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_md5       ON photos(md5)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_photo ON photo_tags(photo_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_tag   ON photo_tags(tag_id)")

    # DB vieja con etiquetas pero sin registro de seed: marcarla como
    # sembrada para no volver a insertar lo que el usuario borró.
    tag_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    if tag_count > 0:
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('seeded', '1')")

    conn.execute(
        "INSERT OR IGNORE INTO categories (name) "
        "SELECT DISTINCT category FROM tags WHERE category IS NOT NULL"
    )


def _m002_internal_trash(conn: sqlite3.Connection) -> None:
    """Papelera interna: registros quitados (con sus etiquetas) para poder restaurarlos."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id    TEXT NOT NULL,              -- una operación = un lote
            reason      TEXT NOT NULL,              -- texto para el usuario
            deleted_at  TEXT NOT NULL DEFAULT (datetime('now')),
            path        TEXT NOT NULL,
            photo_json  TEXT NOT NULL,              -- columnas de photos
            tags_json   TEXT NOT NULL DEFAULT '[]'  -- [{name, category, color}]
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_batch ON deleted_photos(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_at    ON deleted_photos(deleted_at)")


def _m003_mtime_and_sort_indexes(conn: sqlite3.Connection) -> None:
    """mtime para indexación incremental + índices para ordenar la galería."""
    # NULL = registro de antes de v3: se vuelve a leer una vez en la próxima indexación
    _add_column_if_missing(conn, "photos", "mtime", "REAL")
    # Sirve a ORDER BY year, month, filename, id en ambos sentidos (el rowid va
    # implícito al final de todo índice)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_date     ON photos(year, month, filename)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_filename ON photos(filename)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_filesize ON photos(filesize)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_added    ON photos(added_at)")
    # Los índices de solo año / solo mes quedan cubiertos por idx_photos_date
    conn.execute("DROP INDEX IF EXISTS idx_photos_year")
    conn.execute("DROP INDEX IF EXISTS idx_photos_month")
    # Carpeta de cada foto como columna calculada (VIRTUAL: no ocupa espacio en
    # la tabla) e indexada: la lista de carpetas pasa a ser un recorrido del
    # índice. rtrim(path, <caracteres de path salvo '\'>) = todo hasta la última '\'.
    if "folder" not in _columns(conn, "photos"):
        conn.execute(
            r"ALTER TABLE photos ADD COLUMN folder TEXT "
            r"GENERATED ALWAYS AS (rtrim(path, replace(path, '\', ''))) VIRTUAL"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_folder ON photos(folder)")


def _m004_ratings_notes_tag_tree(conn: sqlite3.Connection) -> None:
    """Valoración, favoritas y notas; etiquetas jerárquicas y alias; búsquedas guardadas."""
    _add_column_if_missing(conn, "photos", "rating", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "photos", "favorite", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "photos", "note", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_rating ON photos(rating, year, month, filename)")
    # Parcial: solo las favoritas (pocas) ocupan lugar en el índice
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_favorite ON photos(favorite) WHERE favorite = 1")
    # Borrar el padre deja a los hijos sin padre (no los borra)
    _add_column_if_missing(conn, "tags", "parent_id", "INTEGER REFERENCES tags(id) ON DELETE SET NULL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_aliases (
            alias   TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
            tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_aliases_tag ON tag_aliases(tag_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saved_searches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL COLLATE NOCASE,
            query_json  TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)


def _m005_tag_rejections(conn: sqlite3.Connection) -> None:
    """Revisión por etiqueta: las fotos marcadas "no" (para no volver a mostrarlas)."""
    # Solo se guardan los "no": un "sí" es la etiqueta misma (photo_tags)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_rejections (
            tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            rejected_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (tag_id, photo_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_rejections_photo ON tag_rejections(photo_id)")


_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _m001_baseline,
    _m002_internal_trash,
    _m003_mtime_and_sort_indexes,
    _m004_ratings_notes_tag_tree,
    _m005_tag_rejections,
]

SCHEMA_VERSION = len(_MIGRATIONS)


def get_schema_version() -> int:
    return get_connection().execute("PRAGMA user_version").fetchone()[0]


def _apply_migration(
    conn: sqlite3.Connection, version: int, migration: Callable[[sqlite3.Connection], None]
) -> None:
    doc = (migration.__doc__ or migration.__name__).strip().splitlines()[0]
    logger.info("Aplicando migración %d: %s", version, doc)
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN")
    try:
        migration(conn)
        # PRAGMA no acepta parámetros; version es un int controlado por nosotros
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Falló la migración %d; la DB quedó en la versión %d", version, version - 1)
        raise


def init_db() -> None:
    """
    Corre en cada arranque. Aplica las migraciones pendientes (con backup
    previo si la DB ya existía) y el seed inicial si nunca se hizo.
    """
    conn = get_connection()
    current = get_schema_version()

    if current > SCHEMA_VERSION:
        raise DatabaseTooNewError(
            f"La base de datos está en la versión {current} del esquema, pero esta "
            f"versión de PhotoVault solo conoce hasta la {SCHEMA_VERSION}.\n"
            f"Actualiza PhotoVault para abrirla. No se modificó nada."
        )

    if current < SCHEMA_VERSION:
        has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0
        if has_tables:
            # Si el backup falla se lanza la excepción y no se migra
            backup.pre_migration_backup(DB_PATH, SCHEMA_VERSION)
        for version in range(current + 1, SCHEMA_VERSION + 1):
            _apply_migration(conn, version, _MIGRATIONS[version - 1])

    # ── Seed de datos iniciales ────────────────────────────────────────
    # Solo se ejecuta UNA VEZ en la vida de la base de datos.
    # Si el usuario borra o modifica etiquetas/categorías, esos cambios
    # se respetan en reinicios posteriores.
    with transaction() as conn:
        _seed_initial_data(conn)


# ── Seed inicial ─────────────────────────────────────────────────────────────


def _seed_initial_data(conn):
    """
    Inserta etiquetas y categorías predefinidas la primera vez que se crea
    la base de datos. En arranques posteriores no hace nada, por lo que
    los cambios del usuario (borrar/renombrar) se respetan completamente.
    """
    # Verificar si el seed ya corrió
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'seeded'").fetchone()
    if row:
        return  # Ya se sembró — respetar el estado actual del usuario

    conn.executescript("""
        INSERT OR IGNORE INTO tags (name, category, color) VALUES
            ('sfw', 'contenido', '#4AFF9E'),
            ('nsfw', 'contenido', '#FF4A4A'),
            ('gore', 'contenido', '#8B0000'),
            ('ecchi', 'contenido', '#FF7A9E'),
            ('foto', 'tipo', '#4AFFC3'),
            ('video', 'tipo', '#FF7A4A'),
            ('gif', 'tipo', '#4A9EFF'),
            ('screenshot', 'tipo', '#4AFF9E'),
            ('arte', 'tipo', '#FF9E4A'),
            ('meme', 'tipo', '#FFD700'),
            ('cosplay', 'tipo', '#FF4ACD'),
            ('anime', 'origen', '#FF4ACD'),
            ('caricatura', 'origen', '#4AFFD5'),
            ('comic', 'origen', '#FF6A4A'),
            ('videojuego', 'origen', '#4A9EFF'),
            ('pelicula', 'origen', '#9E4AFF'),
            ('serie', 'origen', '#4AFFF0'),
            ('vida_real', 'origen', '#A4A4A4'),
            ('dc', 'franquicia', '#4A6AFF'),
            ('marvel', 'franquicia', '#FF4A4A'),
            ('indie', 'franquicia', '#AAAAAA'),
            ('familia', 'tema', '#FFD1DC'),
            ('amigos', 'tema', '#FFE4A1'),
            ('pareja', 'tema', '#FF9ECF'),
            ('mascota', 'tema', '#C1FF9E'),
            ('comida', 'tema', '#FFA54A'),
            ('ropa', 'tema', '#A14AFF'),
            ('tecnologia', 'tema', '#4A9EFF'),
            ('trabajo', 'tema', '#8AFFC1'),
            ('yo', 'persona', '#FFFFFF'),
            ('novia', 'persona', '#FF69B4'),
            ('amigo', 'persona', '#87CEEB'),
            ('aesthetic', 'estilo', '#FFB6C1'),
            ('dibujo', 'estilo', '#FF9E4A'),
            ('render_3d', 'estilo', '#4A9EFF'),
            ('realista', 'estilo', '#A4A4A4'),
            ('anime_style', 'estilo', '#FF4ACD'),
            ('blender', 'tecnica', '#FF9E4A'),
            ('vrchat', 'tecnica', '#4AFFD5'),
            ('pc', 'tecnica', '#4A9EFF'),
            ('programacion', 'tecnica', '#00FF7F'),
            ('ciberseguridad', 'tecnica', '#00CED1'),
            ('feliz', 'emocion', '#FFFF7A'),
            ('triste', 'emocion', '#7A7AFF'),
            ('terror', 'emocion', '#8B0000'),
            ('epico', 'emocion', '#FF8C00'),
            ('relajante', 'emocion', '#98FB98'),
            ('pfp', 'uso', '#FF69B4'),
            ('wallpaper', 'uso', '#1E90FF'),
            ('referencia', 'uso', '#32CD32'),
            ('inspiracion', 'uso', '#FFD700'),
            ('archivo', 'uso', '#A9A9A9');

        INSERT OR IGNORE INTO categories (name) VALUES
            ('contenido'),('tipo'),('origen'),('franquicia'),
            ('tema'),('persona'),('estilo'),('tecnica'),
            ('emocion'),('uso'),('general');
    """)

    # Marcar como completado — nunca más volverá a correr
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", ("seeded", "1"))
    conn.commit()


# ── Fotos ─────────────────────────────────────────────────────────────────────

# ¿Tiene alguna etiqueta oculta? (alias de photos: `p`)
# (subconsulta no correlacionada: SQLite la calcula una vez por consulta)
_HIDDEN_EXPR = (
    "p.id IN (SELECT hpt.photo_id FROM photo_tags hpt JOIN tags ht ON ht.id = hpt.tag_id WHERE ht.hidden = 1)"
)
_PHOTO_COLUMNS = (
    "p.id, p.path, p.filename, p.year, p.month, p.media_type, p.duration, "
    "p.filesize, p.width, p.height, p.added_at, p.mtime, p.rating, p.favorite, p.note, "
    f"{_HIDDEN_EXPR} AS hidden"
)


def folder_like_pattern(folder: str) -> str:
    """
    Patrón LIKE (con ESCAPE '!') que coincide con los archivos dentro de
    `folder` y sus subcarpetas, pero NO con carpetas hermanas que empiezan
    igual (D:\\Fotos no incluye D:\\Fotos2). '%' y '_' en la ruta se toman
    literales.
    """
    norm = os.path.normpath(folder).rstrip("/\\")
    escaped = norm.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + os.sep + "%"


def photo_exists(path: str) -> bool:
    conn = get_connection()
    return conn.execute("SELECT 1 FROM photos WHERE path = ?", (path,)).fetchone() is not None


_UPSERT_SQL = """
    INSERT INTO photos (path, filename, media_type, year, month,
                        filesize, width, height, duration, mtime)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(path) DO UPDATE SET
        filename   = excluded.filename,
        media_type = excluded.media_type,
        year       = excluded.year,
        month      = excluded.month,
        width      = excluded.width,
        height     = excluded.height,
        duration   = excluded.duration,
        -- El md5 deja de valer si el archivo cambió. Un mtime NULL es de antes
        -- de v3: si el tamaño coincide se conserva para no recalcular todo.
        md5 = CASE
                WHEN photos.filesize IS excluded.filesize
                 AND (photos.mtime IS NULL OR photos.mtime = excluded.mtime)
                THEN photos.md5
                ELSE NULL
              END,
        filesize   = excluded.filesize,
        mtime      = excluded.mtime
"""


@dataclass(frozen=True)
class PhotoRecord:
    """Datos que el indexador guarda de un archivo."""

    path: str
    filename: str
    year: int | None
    month: int | None
    filesize: int
    width: int | None = None
    height: int | None = None
    media_type: str = "image"
    duration: float | None = None
    mtime: float | None = None

    def params(self) -> tuple:
        return (
            self.path, self.filename, self.media_type, self.year, self.month,
            self.filesize, self.width, self.height, self.duration, self.mtime,
        )  # fmt: skip


def upsert_photo(
    path: str,
    filename: str,
    year: int | None,
    month: int | None,
    filesize: int,
    width: int | None = None,
    height: int | None = None,
    media_type: str = "image",
    duration: float | None = None,
    mtime: float | None = None,
) -> int:
    rec = PhotoRecord(path, filename, year, month, filesize, width, height, media_type, duration, mtime)
    with transaction() as conn:
        conn.execute(_UPSERT_SQL, rec.params())
        return conn.execute("SELECT id FROM photos WHERE path = ?", (path,)).fetchone()[0]


def upsert_photos(records: list[PhotoRecord]) -> None:
    """Inserta/actualiza muchos registros en UNA transacción (indexación por lotes)."""
    if not records:
        return
    with transaction() as conn:
        conn.executemany(_UPSERT_SQL, [r.params() for r in records])


def set_mtimes(pairs: list[tuple[float, str]]) -> None:
    """[(mtime, ruta)] — anota el mtime sin tocar el resto del registro."""
    if not pairs:
        return
    with transaction() as conn:
        conn.executemany("UPDATE photos SET mtime = ? WHERE path = ?", pairs)


def get_photo_ids_by_paths(paths: list[str]) -> list[int]:
    ids: list[int] = []
    conn = get_connection()
    for chunk in _chunks(paths):
        ph = ",".join("?" * len(chunk))
        ids.extend(r[0] for r in conn.execute(f"SELECT id FROM photos WHERE path IN ({ph})", chunk))
    return ids


def get_index_state(folder: str) -> dict[str, tuple[int | None, float | None, int | None]]:
    """{ruta: (tamaño, mtime, año)} de lo ya indexado dentro de `folder` (indexación incremental)."""
    rows = get_connection().execute(
        "SELECT path, filesize, mtime, year FROM photos WHERE path LIKE ? ESCAPE '!'",
        (folder_like_pattern(folder),),
    )
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def update_photo_md5(photo_id: int, md5: str):
    with transaction() as conn:
        conn.execute("UPDATE photos SET md5 = ? WHERE id = ?", (md5, photo_id))


def _row_to_photo(row: sqlite3.Row) -> Photo:
    keys = row.keys()
    return Photo(
        id=row["id"],
        path=row["path"],
        filename=row["filename"],
        year=row["year"],
        month=row["month"],
        media_type=row["media_type"] or "image",
        duration=row["duration"],
        filesize=row["filesize"] if "filesize" in keys else None,
        width=row["width"] if "width" in keys else None,
        height=row["height"] if "height" in keys else None,
        added_at=row["added_at"] if "added_at" in keys else None,
        md5=row["md5"] if "md5" in keys else None,
        mtime=row["mtime"] if "mtime" in keys else None,
        rating=(row["rating"] or 0) if "rating" in keys else 0,
        favorite=bool(row["favorite"]) if "favorite" in keys else False,
        note=row["note"] if "note" in keys else None,
        hidden=bool(row["hidden"]) if "hidden" in keys else False,
    )


@dataclass(frozen=True)
class PhotoFilter:
    """Filtros de la galería. Todos opcionales; se combinan con AND."""

    tag_ids: list[int] | None = None  # la foto debe tener TODAS
    hidden_tag_ids: set[int] | None = None  # excluir fotos con alguna
    search: str | None = None  # LIKE en filename o en la nota
    folder: str | None = None  # carpeta y subcarpetas
    untagged_only: bool = False
    # Fase 6 ─ etiquetas con jerarquía ya expandida (etiqueta + descendientes)
    tag_groups: list[list[int]] | None = None  # AND de grupos; de cada grupo, alguna
    any_tag_ids: list[int] | None = None  # OR: al menos una
    exclude_tag_ids: list[int] | None = None  # NOT: ninguna
    # Fase 6 ─ atributos
    media_type: str | None = None  # "image" | "video"
    year_from: int | None = None
    year_to: int | None = None
    min_pixels: int | None = None  # ancho × alto
    orientation: str | None = None  # "landscape" | "portrait" | "square"
    min_duration: float | None = None  # segundos (solo videos)
    max_duration: float | None = None
    min_rating: int | None = None
    favorites_only: bool = False
    has_note: bool = False
    # Fase 7 ─ revisión por etiqueta
    not_rejected_for: int | None = None  # sin un "no" para esta etiqueta
    rejected_for: int | None = None  # con un "no" para esta etiqueta


def _build_where(f: PhotoFilter) -> tuple[str, list[object]]:
    """WHERE parametrizado para un PhotoFilter (alias de photos: `p`)."""
    clauses: list[str] = []
    params: list[object] = []

    if f.hidden_tag_ids:
        placeholders = ",".join("?" * len(f.hidden_tag_ids))
        clauses.append(f"p.id NOT IN (SELECT photo_id FROM photo_tags WHERE tag_id IN ({placeholders}))")
        params.extend(f.hidden_tag_ids)

    for tid in f.tag_ids or []:
        clauses.append("p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)")
        params.append(tid)

    for group in f.tag_groups or []:
        if group:
            ph = ",".join("?" * len(group))
            clauses.append(f"p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id IN ({ph}))")
            params.extend(group)

    if f.any_tag_ids:
        ph = ",".join("?" * len(f.any_tag_ids))
        clauses.append(f"p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id IN ({ph}))")
        params.extend(f.any_tag_ids)

    if f.exclude_tag_ids:
        ph = ",".join("?" * len(f.exclude_tag_ids))
        clauses.append(f"p.id NOT IN (SELECT photo_id FROM photo_tags WHERE tag_id IN ({ph}))")
        params.extend(f.exclude_tag_ids)

    if f.search:
        clauses.append("(p.filename LIKE ? OR p.note LIKE ?)")
        params.extend([f"%{f.search}%", f"%{f.search}%"])

    if f.media_type:
        clauses.append("p.media_type = ?")
        params.append(f.media_type)
    if f.year_from is not None:
        clauses.append("p.year >= ?")
        params.append(f.year_from)
    if f.year_to is not None:
        clauses.append("p.year <= ?")
        params.append(f.year_to)
    if f.min_pixels:
        clauses.append("p.width * p.height >= ?")
        params.append(f.min_pixels)
    # Con un 2 % de tolerancia, 1000×990 cuenta como cuadrada
    if f.orientation == "landscape":
        clauses.append("p.width * 50 > p.height * 51")
    elif f.orientation == "portrait":
        clauses.append("p.height * 50 > p.width * 51")
    elif f.orientation == "square":
        clauses.append("p.width * 50 BETWEEN p.height * 49 AND p.height * 51")
    if f.min_duration is not None:
        clauses.append("p.duration >= ?")
        params.append(f.min_duration)
    if f.max_duration is not None:
        clauses.append("p.duration < ?")
        params.append(f.max_duration)
    if f.min_rating:
        clauses.append("p.rating >= ?")
        params.append(f.min_rating)
    if f.favorites_only:
        clauses.append("p.favorite = 1")
    if f.not_rejected_for is not None:
        clauses.append("p.id NOT IN (SELECT photo_id FROM tag_rejections WHERE tag_id = ?)")
        params.append(f.not_rejected_for)
    if f.rejected_for is not None:
        clauses.append("p.id IN (SELECT photo_id FROM tag_rejections WHERE tag_id = ?)")
        params.append(f.rejected_for)
    if f.has_note:
        clauses.append("p.note IS NOT NULL AND p.note != ''")

    if f.folder:
        clauses.append("p.path LIKE ? ESCAPE '!'")
        params.append(folder_like_pattern(f.folder))

    if f.untagged_only:
        clauses.append("p.id NOT IN (SELECT DISTINCT photo_id FROM photo_tags)")

    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def get_photos(
    tag_ids: list[int] | None = None,
    hidden_tag_ids: set[int] | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    sort_field: SortField = SortField.DATE,
    sort_order: SortOrder = SortOrder.DESC,
    folder: str | None = None,
    untagged_only: bool = False,
) -> list[Photo]:
    where_sql, params = _build_where(
        PhotoFilter(
            tag_ids,
            hidden_tag_ids,
            search,
            folder,
            untagged_only,
        )
    )
    order_sql = sort_to_sql(sort_field, sort_order)
    rows = (
        get_connection()
        .execute(
            f"""
        SELECT {_PHOTO_COLUMNS}
        FROM photos p
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """,
            [*params, limit, offset],
        )
        .fetchall()
    )
    return [_row_to_photo(r) for r in rows]


def get_photo_count(
    tag_ids: list[int] | None = None,
    hidden_tag_ids: set[int] | None = None,
    search: str | None = None,
    folder: str | None = None,
    untagged_only: bool = False,
) -> int:
    where_sql, params = _build_where(
        PhotoFilter(
            tag_ids,
            hidden_tag_ids,
            search,
            folder,
            untagged_only,
        )
    )
    row = get_connection().execute(f"SELECT COUNT(*) FROM photos p {where_sql}", params).fetchone()
    return row[0]


def query_photos(
    f: PhotoFilter,
    sort_field: SortField = SortField.DATE,
    sort_order: SortOrder = SortOrder.DESC,
    limit: int = -1,
    offset: int = 0,
) -> list[Photo]:
    """Como get_photos pero con un PhotoFilter completo (galería, fase 6)."""
    where_sql, params = _build_where(f)
    rows = get_connection().execute(
        f"SELECT {_PHOTO_COLUMNS} FROM photos p {where_sql} "
        f"ORDER BY {sort_to_sql(sort_field, sort_order)} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return [_row_to_photo(r) for r in rows]


def count_filtered(f: PhotoFilter) -> int:
    where_sql, params = _build_where(f)
    return get_connection().execute(f"SELECT COUNT(*) FROM photos p {where_sql}", params).fetchone()[0]


def _and(where_sql: str, condition: str) -> str:
    return f"{where_sql} AND ({condition})" if where_sql else f"WHERE {condition}"


def get_photo_ids(
    f: PhotoFilter,
    sort_field: SortField = SortField.DATE,
    sort_order: SortOrder = SortOrder.DESC,
    limit: int = -1,
    offset: int = 0,
) -> list[int]:
    """Solo los ids, en el mismo orden que get_photos (selecciones grandes de la galería)."""
    where_sql, params = _build_where(f)
    rows = get_connection().execute(
        f"SELECT p.id FROM photos p {where_sql} ORDER BY {sort_to_sql(sort_field, sort_order)} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )
    return [r[0] for r in rows]


NO_MONTH = 0  # count_before_date: "las fotos de ese año que no tienen mes"


def count_before_date(f: PhotoFilter, year: int | None, month: int | None, order: SortOrder) -> int:
    """
    Cuántas fotos van antes de la primera de (year, month) en el orden por fecha:
    es la fila a la que saltar en la galería. month=None = el año completo;
    year=None = las fotos sin fecha; month=NO_MONTH = las de ese año sin mes.
    SQLite pone los NULL primero en ASC y al final en DESC, y estas
    condiciones respetan eso.
    """
    extra: list[object]
    if order == SortOrder.DESC:
        if year is None:
            cond, extra = "p.year IS NOT NULL", []
        elif month is None:
            cond, extra = "p.year > ?", [year]
        elif month == NO_MONTH:
            cond, extra = "p.year > ? OR (p.year = ? AND p.month IS NOT NULL)", [year, year]
        else:
            cond, extra = "p.year > ? OR (p.year = ? AND p.month > ?)", [year, year, month]
    else:
        if year is None:
            return 0
        if month is None or month == NO_MONTH:
            cond, extra = "p.year IS NULL OR p.year < ?", [year]
        else:
            cond = "p.year IS NULL OR p.year < ? OR (p.year = ? AND (p.month IS NULL OR p.month < ?))"
            extra = [year, year, month]
    where_sql, params = _build_where(f)
    sql = f"SELECT COUNT(*) FROM photos p {_and(where_sql, cond)}"
    return get_connection().execute(sql, [*params, *extra]).fetchone()[0]


def get_date_histogram(f: PhotoFilter) -> list[tuple[int | None, int | None, int]]:
    """[(año, mes, n.º de fotos)] para la línea de tiempo (con los filtros actuales)."""
    where_sql, params = _build_where(f)
    rows = get_connection().execute(
        f"SELECT p.year, p.month, COUNT(*) FROM photos p {where_sql} GROUP BY p.year, p.month", params
    )
    return [(r[0], r[1], r[2]) for r in rows]


def get_folder_counts() -> list[tuple[str, int]]:
    """[(carpeta, n.º de archivos directamente en ella)] ordenado por carpeta."""
    # rtrim(path, <todos los caracteres de path salvo '\'>) corta todo lo que
    # sigue a la última barra: es dirname() en SQL, y el GROUP BY lo hace SQLite.
    # `folder` es una columna calculada e indexada (migración v3)
    rows = get_connection().execute("SELECT folder, COUNT(*) FROM photos GROUP BY folder")
    counts: dict[str, int] = {}
    for folder, n in rows:
        # `folder` termina en "\" (o es ""): dirname(folder + "x") da exactamente
        # lo mismo que dirname(ruta original), incluidas raíces "G:\" y UNC.
        key = os.path.dirname(folder + "x")
        counts[key] = counts.get(key, 0) + n
    return sorted(counts.items())


def get_root_counts() -> list[tuple[str, int]]:
    """[(unidad, registros)] p. ej. [("G:\\", 171840)]."""
    rows = (
        get_connection()
        .execute(r"""
        SELECT CASE WHEN path GLOB '[A-Za-z]:\*' THEN substr(path, 1, 3) END AS root, COUNT(*)
        FROM photos GROUP BY root
    """)
        .fetchall()
    )
    counts: dict[str, int] = {}
    for root, n in rows:
        if root is not None:
            counts[root] = counts.get(root, 0) + n
    if any(root is None for root, _n in rows):
        # Rutas sin letra de unidad (UNC \\servidor\recurso, relativas…): pocas, en Python
        for (path,) in get_connection().execute(r"SELECT path FROM photos WHERE path NOT GLOB '[A-Za-z]:\*'"):
            anchor = Path(path).anchor or "(sin unidad)"
            counts[anchor] = counts.get(anchor, 0) + 1
    return sorted(counts.items())


def get_totals() -> tuple[int, int]:
    conn = get_connection()
    return (
        conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
    )


def get_paths_with_mtime(hidden: bool | None = None) -> list[tuple[str, float | None]]:
    """[(ruta, mtime)]; `hidden`: solo las fotos ocultas (True) o solo las visibles (False)."""
    where = "" if hidden is None else f"WHERE {'' if hidden else 'NOT '}{_HIDDEN_EXPR}"
    return [(r[0], r[1]) for r in get_connection().execute(f"SELECT p.path, p.mtime FROM photos p {where}")]


def get_hidden_photos() -> list[Photo]:
    """Las fotos con alguna etiqueta oculta."""
    rows = get_connection().execute(f"SELECT {_PHOTO_COLUMNS} FROM photos p WHERE {_HIDDEN_EXPR}")
    return [_row_to_photo(r) for r in rows]


def get_photos_by_ids(photo_ids: list[int]) -> list[Photo]:
    out: list[Photo] = []
    conn = get_connection()
    for chunk in _chunks(photo_ids):
        ph = ",".join("?" * len(chunk))
        out.extend(
            _row_to_photo(r)
            for r in conn.execute(f"SELECT {_PHOTO_COLUMNS} FROM photos p WHERE p.id IN ({ph})", chunk)
        )
    return out


def get_duplicate_candidates_without_md5() -> list[Photo]:
    """Fotos sin md5 cuyo tamaño comparte otra foto (las únicas que pueden ser duplicados exactos)."""
    rows = get_connection().execute(f"""
        SELECT {_PHOTO_COLUMNS}, p.md5 FROM photos p
        WHERE p.md5 IS NULL AND p.filesize > 0
          AND p.filesize IN (SELECT filesize FROM photos GROUP BY filesize HAVING COUNT(*) > 1)
        ORDER BY p.path
    """)
    return [_row_to_photo(r) for r in rows]


def get_photo_by_id(photo_id: int) -> Photo | None:
    conn = get_connection()
    row = conn.execute(f"SELECT {_PHOTO_COLUMNS}, p.md5 FROM photos p WHERE p.id = ?", (photo_id,)).fetchone()
    return _row_to_photo(row) if row else None


def get_all_photos_for_duplicates() -> list[Photo]:
    """Todas las fotos, con su md5 (None si aún no se calculó)."""
    conn = get_connection()
    rows = conn.execute(f"SELECT {_PHOTO_COLUMNS}, p.md5 FROM photos p").fetchall()
    return [_row_to_photo(r) for r in rows]


def get_all_photo_paths() -> list[tuple[int, str]]:
    conn = get_connection()
    return [(r[0], r[1]) for r in conn.execute("SELECT id, path FROM photos").fetchall()]


# Columnas de photos que se guardan en la papelera (todas menos id)
_TRASH_PHOTO_FIELDS = (
    "path",
    "filename",
    "media_type",
    "year",
    "month",
    "filesize",
    "width",
    "height",
    "duration",
    "md5",
    "added_at",
    "rating",
    "favorite",
    "note",
)


def _merge_extras(conn: sqlite3.Connection, target_id: int, rating, favorite, note) -> None:
    """Al fusionar dos registros de la misma foto: la valoración más alta, favorita si alguna lo era, la nota que haya."""
    conn.execute(
        "UPDATE photos SET rating = MAX(rating, ?), favorite = MAX(favorite, ?), "
        "note = CASE WHEN note IS NULL OR note = '' THEN ? ELSE note END WHERE id = ?",
        (rating or 0, int(bool(favorite)), note, target_id),
    )


def _chunks(items: list, size: int = 500):
    # SQLite limita la cantidad de parámetros por consulta
    for i in range(0, len(items), size):
        yield items[i : i + size]


def delete_photos(photo_ids: list[int], reason: str = "Eliminado") -> int:
    """
    Quita registros de la DB. No toca archivos del disco.
    Antes de borrar, copia cada registro y sus etiquetas a la papelera
    interna (deleted_photos) en la misma transacción, para poder restaurarlos.
    """
    if not photo_ids:
        return 0
    batch_id = uuid.uuid4().hex
    cols = ", ".join(_TRASH_PHOTO_FIELDS)
    with transaction() as conn:
        for chunk in _chunks(photo_ids):
            ph = ",".join("?" * len(chunk))
            photos = conn.execute(f"SELECT id, {cols} FROM photos WHERE id IN ({ph})", chunk).fetchall()
            tags: dict[int, list[dict]] = {}
            for r in conn.execute(
                f"""
                SELECT pt.photo_id, t.name, t.category, t.color
                FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                WHERE pt.photo_id IN ({ph})
            """,
                chunk,
            ):
                tags.setdefault(r[0], []).append({"name": r[1], "category": r[2], "color": r[3]})
            conn.executemany(
                "INSERT INTO deleted_photos (batch_id, reason, path, photo_json, tags_json) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        batch_id,
                        reason,
                        r["path"],
                        json.dumps({k: r[k] for k in _TRASH_PHOTO_FIELDS}, ensure_ascii=False),
                        json.dumps(tags.get(r["id"], []), ensure_ascii=False),
                    )
                    for r in photos
                ],
            )
            conn.execute(f"DELETE FROM photos WHERE id IN ({ph})", chunk)
    logger.info("%d registros a la papelera interna (lote %s, %s)", len(photo_ids), batch_id, reason)
    return len(photo_ids)


# ── Papelera interna ──────────────────────────────────────────────────────────


def list_trash_batches() -> list[TrashBatch]:
    rows = (
        get_connection()
        .execute("""
        SELECT batch_id, reason, MIN(deleted_at) AS deleted_at, COUNT(*) AS n,
               SUM(CASE WHEN tags_json != '[]' THEN 1 ELSE 0 END) AS tagged
        FROM deleted_photos
        GROUP BY batch_id
        ORDER BY deleted_at DESC, MIN(id) DESC
    """)
        .fetchall()
    )
    return [
        TrashBatch(
            batch_id=r["batch_id"],
            reason=r["reason"],
            deleted_at=r["deleted_at"],
            count=r["n"],
            tagged=r["tagged"],
        )
        for r in rows
    ]


def count_trash() -> int:
    return get_connection().execute("SELECT COUNT(*) FROM deleted_photos").fetchone()[0]


def _get_or_create_tag(conn: sqlite3.Connection, name: str, category: str | None, color: str | None) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    category = category or "general"
    conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
    return conn.execute(
        "INSERT INTO tags (name, category, color) VALUES (?, ?, ?) RETURNING id",
        (name, category, color or "#4A9EFF"),
    ).fetchone()[0]


def restore_trash_batch(batch_id: str) -> tuple[int, int]:
    """
    Restaura un lote de la papelera. Devuelve (restaurados, fusionados):
    si la ruta ya volvió a indexarse, sus etiquetas se agregan a ese registro.
    Las etiquetas que se hayan borrado mientras tanto se vuelven a crear.
    """
    restored = merged = 0
    with transaction() as conn:
        entries = conn.execute(
            "SELECT id, photo_json, tags_json FROM deleted_photos WHERE batch_id = ?", (batch_id,)
        ).fetchall()
        for e in entries:
            data = json.loads(e["photo_json"])
            existing = conn.execute("SELECT id FROM photos WHERE path = ?", (data["path"],)).fetchone()
            if existing:
                photo_id = existing[0]
                _merge_extras(conn, photo_id, data.get("rating"), data.get("favorite"), data.get("note"))
                merged += 1
            else:
                cols = [k for k in _TRASH_PHOTO_FIELDS if k in data]
                photo_id = conn.execute(
                    f"INSERT INTO photos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                    "RETURNING id",
                    [data[k] for k in cols],
                ).fetchone()[0]
                restored += 1
            for t in json.loads(e["tags_json"]):
                tag_id = _get_or_create_tag(conn, t["name"], t.get("category"), t.get("color"))
                conn.execute(
                    "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
                    (photo_id, tag_id),
                )
        conn.execute("DELETE FROM deleted_photos WHERE batch_id = ?", (batch_id,))
    logger.info("Lote %s restaurado: %d restaurados, %d fusionados", batch_id, restored, merged)
    return restored, merged


def delete_trash_batch(batch_id: str) -> int:
    with transaction() as conn:
        return conn.execute("DELETE FROM deleted_photos WHERE batch_id = ?", (batch_id,)).rowcount


def purge_trash(older_than_days: int | None = None) -> int:
    """Vacía la papelera (todo, o solo lo más viejo que N días)."""
    with transaction() as conn:
        if older_than_days is None:
            return conn.execute("DELETE FROM deleted_photos").rowcount
        return conn.execute(
            "DELETE FROM deleted_photos WHERE deleted_at < datetime('now', ?)",
            (f"-{int(older_than_days)} days",),
        ).rowcount


# ── Reubicar carpetas / unidades ──────────────────────────────────────────────


def _strip_seps(folder: str) -> str:
    return os.path.normpath(folder).rstrip("/\\")


def get_relocation_plan(old_folder: str, new_folder: str) -> list[tuple[int, str, int | None]]:
    """
    [(photo_id, ruta_nueva, id_existente_o_None)] para mover old_folder → new_folder.
    id_existente: ya hay un registro con la ruta nueva (p. ej. se re-indexó).
    """
    old_n, new_n = _strip_seps(old_folder), _strip_seps(new_folder)
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, path FROM photos WHERE path LIKE ? ESCAPE '!'", (folder_like_pattern(old_folder),)
    ).fetchall()
    plan = []
    for pid, path in rows:
        new_path = new_n + path[len(old_n) :]
        existing = conn.execute("SELECT id FROM photos WHERE path = ?", (new_path,)).fetchone()
        plan.append((pid, new_path, existing[0] if existing else None))
    return plan


def apply_relocation(plan: list[tuple[int, str, int | None]]) -> tuple[int, int]:
    """
    Aplica un plan de get_relocation_plan. Devuelve (movidos, fusionados).
    Fusionar = pasar las etiquetas al registro existente y quitar el viejo
    (no va a la papelera: no se pierde nada).
    """
    moved = merged = 0
    with transaction() as conn:
        for pid, new_path, existing_id in plan:
            if existing_id is None:
                conn.execute(
                    "UPDATE photos SET path = ?, filename = ? WHERE id = ?",
                    (new_path, os.path.basename(new_path), pid),
                )
                moved += 1
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) "
                    "SELECT ?, tag_id FROM photo_tags WHERE photo_id = ?",
                    (existing_id, pid),
                )
                old = conn.execute(
                    "SELECT rating, favorite, note FROM photos WHERE id = ?", (pid,)
                ).fetchone()
                _merge_extras(conn, existing_id, old[0], old[1], old[2])
                conn.execute("DELETE FROM photos WHERE id = ?", (pid,))
                merged += 1
    return moved, merged


# ── Asignaciones foto ↔ etiqueta (exportar / importar) ───────────────────────


def get_all_assignments() -> list[tuple[str, str, int | None, list[str]]]:
    """[(ruta, nombre, tamaño, [etiquetas])] de todas las fotos con etiquetas."""
    rows = (
        get_connection()
        .execute("""
        SELECT p.path, p.filename, p.filesize, t.name
        FROM photo_tags pt
        JOIN photos p ON p.id = pt.photo_id
        JOIN tags   t ON t.id = pt.tag_id
        ORDER BY p.path, t.name
    """)
        .fetchall()
    )
    out: list[tuple[str, str, int | None, list[str]]] = []
    for path, filename, filesize, tag in rows:
        if out and out[-1][0] == path:
            out[-1][3].append(tag)
        else:
            out.append((path, filename, filesize, [tag]))
    return out


def get_photo_lookup() -> tuple[dict[str, int], dict[tuple[str, int | None], list[int]]]:
    """Índices para emparejar fotos: {ruta: id} y {(nombre_minúsculas, tamaño): [ids]}."""
    by_path: dict[str, int] = {}
    by_name: dict[tuple[str, int | None], list[int]] = {}
    for pid, path, filename, filesize in get_connection().execute(
        "SELECT id, path, filename, filesize FROM photos"
    ):
        by_path[path] = pid
        by_name.setdefault((filename.lower(), filesize), []).append(pid)
    return by_path, by_name


def add_assignments(pairs: list[tuple[int, int]]) -> int:
    """Agrega pares (photo_id, tag_id). Devuelve cuántos eran nuevos."""
    with transaction() as conn:
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", pairs)
        return conn.total_changes - before


def get_tag_ids_by_name() -> dict[str, int]:
    return {r[1]: r[0] for r in get_connection().execute("SELECT id, name FROM tags")}


def get_photo_ids_with_tag(tag_id: int) -> list[int]:
    return [
        r[0] for r in get_connection().execute("SELECT photo_id FROM photo_tags WHERE tag_id = ?", (tag_id,))
    ]


def get_tagged_photo_ids() -> list[int]:
    return [r[0] for r in get_connection().execute("SELECT DISTINCT photo_id FROM photo_tags")]


def count_tagged(photo_ids: list[int]) -> int:
    """Cuántas de estas fotos tienen al menos una etiqueta."""
    if not photo_ids:
        return 0
    conn = get_connection()
    total = 0
    for chunk in _chunks(photo_ids):
        placeholders = ",".join("?" * len(chunk))
        total += conn.execute(
            f"SELECT COUNT(DISTINCT photo_id) FROM photo_tags WHERE photo_id IN ({placeholders})",
            chunk,
        ).fetchone()[0]
    return total


def get_folder_photo_ids(folder: str) -> list[int]:
    """IDs de las fotos dentro de `folder` y sus subcarpetas."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM photos WHERE path LIKE ? ESCAPE '!'", (folder_like_pattern(folder),)
    ).fetchall()
    return [r[0] for r in rows]


# ── Valoración, favoritas, notas (v4) ────────────────────────────────────────


def set_rating(photo_ids: list[int], rating: int) -> None:
    rating = max(0, min(5, int(rating)))
    with transaction() as conn:
        for chunk in _chunks(photo_ids):
            conn.execute(
                f"UPDATE photos SET rating = ? WHERE id IN ({','.join('?' * len(chunk))})", [rating, *chunk]
            )


def set_favorite(photo_ids: list[int], favorite: bool) -> None:
    with transaction() as conn:
        for chunk in _chunks(photo_ids):
            conn.execute(
                f"UPDATE photos SET favorite = ? WHERE id IN ({','.join('?' * len(chunk))})",
                [int(favorite), *chunk],
            )


def count_favorites(photo_ids: list[int]) -> int:
    conn = get_connection()
    return sum(
        conn.execute(
            f"SELECT COUNT(*) FROM photos WHERE favorite = 1 AND id IN ({','.join('?' * len(chunk))})", chunk
        ).fetchone()[0]
        for chunk in _chunks(photo_ids)
    )


def set_note(photo_id: int, note: str | None) -> None:
    note = (note or "").strip() or None
    with transaction() as conn:
        conn.execute("UPDATE photos SET note = ? WHERE id = ?", (note, photo_id))


def get_photo_extras() -> dict[str, tuple[str, int | None, int, bool, str | None]]:
    """{ruta: (nombre, tamaño, valoración, favorita, nota)} de las fotos que tienen alguna (exportar)."""
    rows = get_connection().execute(
        "SELECT path, filename, filesize, rating, favorite, note FROM photos "
        "WHERE rating > 0 OR favorite = 1 OR (note IS NOT NULL AND note != '')"
    )
    return {r[0]: (r[1], r[2], r[3], bool(r[4]), r[5]) for r in rows}


def merge_photo_extras(pairs: list[tuple[int, int, bool, str | None]]) -> int:
    """[(photo_id, valoración, favorita, nota)] — solo agrega (importar). Devuelve las fotos cambiadas."""
    with transaction() as conn:
        before = conn.total_changes
        for pid, rating, favorite, note in pairs:
            conn.execute(
                "UPDATE photos SET rating = MAX(rating, ?), favorite = MAX(favorite, ?), "
                "note = CASE WHEN note IS NULL OR note = '' THEN ? ELSE note END "
                "WHERE id = ? AND (rating < ? OR favorite < ? OR ((note IS NULL OR note = '') AND ? IS NOT NULL))",
                (rating, int(favorite), note, pid, rating, int(favorite), note),
            )
        return conn.total_changes - before


def add_rejections(tag_id: int, photo_ids: list[int]) -> None:
    """Marca estas fotos como "no tienen esta etiqueta" (revisión)."""
    with transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO tag_rejections (tag_id, photo_id) VALUES (?, ?)",
            [(tag_id, pid) for pid in photo_ids],
        )


def remove_rejections(tag_id: int, photo_ids: list[int]) -> None:
    with transaction() as conn:
        conn.executemany(
            "DELETE FROM tag_rejections WHERE tag_id = ? AND photo_id = ?",
            [(tag_id, pid) for pid in photo_ids],
        )


def get_rejected_ids(tag_id: int, photo_ids: list[int]) -> set[int]:
    conn = get_connection()
    out: set[int] = set()
    for chunk in _chunks(photo_ids):
        ph = ",".join("?" * len(chunk))
        out.update(
            r[0]
            for r in conn.execute(
                f"SELECT photo_id FROM tag_rejections WHERE tag_id = ? AND photo_id IN ({ph})",
                [tag_id, *chunk],
            )
        )
    return out


def get_ids_with_tag(tag_id: int, photo_ids: list[int]) -> set[int]:
    """Cuáles de estas fotos tienen la etiqueta (directamente)."""
    conn = get_connection()
    out: set[int] = set()
    for chunk in _chunks(photo_ids):
        ph = ",".join("?" * len(chunk))
        out.update(
            r[0]
            for r in conn.execute(
                f"SELECT photo_id FROM photo_tags WHERE tag_id = ? AND photo_id IN ({ph})", [tag_id, *chunk]
            )
        )
    return out


def get_all_rejections() -> list[tuple[str, str, int | None, list[str]]]:
    """[(ruta, nombre, tamaño, [etiquetas rechazadas])] para exportar."""
    rows = get_connection().execute("""
        SELECT p.path, p.filename, p.filesize, t.name
        FROM tag_rejections r JOIN photos p ON p.id = r.photo_id JOIN tags t ON t.id = r.tag_id
        ORDER BY p.path, t.name
    """)
    out: list[tuple[str, str, int | None, list[str]]] = []
    for path, filename, filesize, tag in rows:
        if out and out[-1][0] == path:
            out[-1][3].append(tag)
        else:
            out.append((path, filename, filesize, [tag]))
    return out


def get_rated_photo_ids() -> list[int]:
    return [r[0] for r in get_connection().execute("SELECT id FROM photos WHERE rating > 0")]


# ── Búsquedas guardadas (v4) ──────────────────────────────────────────────────


def list_saved_searches() -> list[tuple[int, str, str]]:
    """[(id, nombre, query_json)] por nombre."""
    return [
        (r[0], r[1], r[2])
        for r in get_connection().execute(
            "SELECT id, name, query_json FROM saved_searches ORDER BY name COLLATE NOCASE"
        )
    ]


def save_search(name: str, query_json: str) -> int:
    """Crea o reemplaza (mismo nombre) una búsqueda guardada."""
    name = name.strip()
    if not name:
        raise ValueError("El nombre no puede estar vacío.")
    with transaction() as conn:
        # COLLATE NOCASE de SQLite solo ignora mayúsculas en ASCII ("Mías" ≠ "MÍAS"):
        # se compara en Python para que el mismo nombre reemplace
        for sid, existing in conn.execute("SELECT id, name FROM saved_searches").fetchall():
            if existing.casefold() == name.casefold():
                conn.execute("UPDATE saved_searches SET query_json = ? WHERE id = ?", (query_json, sid))
                return sid
        return conn.execute(
            "INSERT INTO saved_searches (name, query_json) VALUES (?, ?) RETURNING id", (name, query_json)
        ).fetchone()[0]


def rename_saved_search(search_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("El nombre no puede estar vacío.")
    with transaction() as conn:
        for sid, existing in conn.execute("SELECT id, name FROM saved_searches").fetchall():
            if sid != search_id and existing.casefold() == name.casefold():
                raise ValueError(f"Ya hay una búsqueda llamada '{name}'.")
        conn.execute("UPDATE saved_searches SET name = ? WHERE id = ?", (name, search_id))


def update_saved_search_json(search_id: int, query_json: str) -> None:
    with transaction() as conn:
        conn.execute("UPDATE saved_searches SET query_json = ? WHERE id = ?", (query_json, search_id))


def delete_saved_search(search_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))


# ── Configuración del usuario ─────────────────────────────────────────────────


def get_setting(key: str, default: str | None = None) -> str | None:
    row = get_connection().execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ── Etiquetas de una foto ─────────────────────────────────────────────────────


def _row_to_tag(row: sqlite3.Row) -> Tag:
    keys = row.keys()
    return Tag(
        id=row["id"],
        name=row["name"],
        category=row["category"] or "general",
        color=row["color"] or "#4A9EFF",
        hidden=bool(row["hidden"]) if "hidden" in keys else False,
        sidebar_hidden=bool(row["sidebar_hidden"]) if "sidebar_hidden" in keys else False,
        parent_id=row["parent_id"] if "parent_id" in keys else None,
    )


def get_photo_tags(photo_id: int) -> list[Tag]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.category, t.color, t.hidden, t.sidebar_hidden, t.parent_id
        FROM tags t JOIN photo_tags pt ON pt.tag_id = t.id
        WHERE pt.photo_id = ?
        ORDER BY t.category, t.name
    """,
        (photo_id,),
    ).fetchall()
    return [_row_to_tag(r) for r in rows]


def set_photo_tags(photo_id: int, tag_ids: list[int]):
    with transaction() as conn:
        conn.execute("DELETE FROM photo_tags WHERE photo_id = ?", (photo_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            [(photo_id, tid) for tid in tag_ids],
        )


def add_tag_to_photo(photo_id: int, tag_id: int):
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", (photo_id, tag_id))


def add_tag_to_photos(photo_ids: list[int], tag_id: int):
    """Agrega una etiqueta a múltiples fotos de una vez."""
    with transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            [(pid, tag_id) for pid in photo_ids],
        )


def remove_tag_from_photo(photo_id: int, tag_id: int):
    with transaction() as conn:
        conn.execute("DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?", (photo_id, tag_id))


def remove_tag_from_photos(photo_ids: list[int], tag_id: int):
    """Quita una etiqueta de múltiples fotos de una vez."""
    with transaction() as conn:
        conn.executemany(
            "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?", [(pid, tag_id) for pid in photo_ids]
        )


# ── Etiquetas globales ────────────────────────────────────────────────────────


def get_all_tags(include_sidebar_hidden: bool = True) -> list[Tag]:
    conn = get_connection()
    if include_sidebar_hidden:
        rows = conn.execute(
            "SELECT id, name, category, color, hidden, sidebar_hidden, parent_id FROM tags ORDER BY category, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, category, color, hidden, sidebar_hidden, parent_id "
            "FROM tags WHERE sidebar_hidden = 0 ORDER BY category, name"
        ).fetchall()
    return [_row_to_tag(r) for r in rows]


def get_hidden_tag_ids() -> set[int]:
    conn = get_connection()
    rows = conn.execute("SELECT id FROM tags WHERE hidden = 1").fetchall()
    return {r[0] for r in rows}


def create_tag(name: str, category: str = "general", color: str = "#4A9EFF") -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tags (name, category, color) VALUES (?, ?, ?) RETURNING id",
            (name.strip().lower(), category, color),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        return conn.execute("SELECT id FROM tags WHERE name = ?", (name.strip().lower(),)).fetchone()[0]


class TagNameConflictError(ValueError):
    """Ya existe otra etiqueta con ese nombre."""


_KEEP = object()  # update_tag: no cambiar el padre


def update_tag(
    tag_id: int,
    name: str,
    category: str,
    color: str,
    parent_id: int | None | object = _KEEP,
    aliases: list[str] | None = None,
) -> None:
    """
    Cambia nombre, categoría y color (y opcionalmente padre y alias) en una
    sola transacción: si algo no es válido no cambia nada.
    """
    name = name.strip().lower()
    category = category.strip().lower() or "general"
    if not name:
        raise ValueError("El nombre de etiqueta no puede estar vacío.")
    with transaction() as conn:
        clash = conn.execute("SELECT id FROM tags WHERE name = ? AND id != ?", (name, tag_id)).fetchone()
        if clash:
            raise TagNameConflictError(f"Ya existe una etiqueta llamada '{name}'.")
        alias_clash = conn.execute(
            "SELECT 1 FROM tag_aliases WHERE alias = ? AND tag_id != ?", (name, tag_id)
        ).fetchone()
        if alias_clash:
            raise TagNameConflictError(f"'{name}' ya es un alias de otra etiqueta.")
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
        conn.execute(
            "UPDATE tags SET name = ?, category = ?, color = ? WHERE id = ?",
            (name, category, color, tag_id),
        )
        if parent_id is not _KEEP:
            _set_parent(conn, tag_id, parent_id)  # type: ignore[arg-type]
        if aliases is not None:
            _set_aliases(conn, tag_id, aliases)


def _set_parent(conn: sqlite3.Connection, tag_id: int, parent_id: int | None) -> None:
    if parent_id is not None:
        # Subir desde el padre propuesto: si se llega a tag_id, sería un ciclo
        node: int | None = parent_id
        while node is not None:
            if node == tag_id:
                raise ValueError("Una etiqueta no puede ser descendiente de sí misma.")
            row = conn.execute("SELECT parent_id FROM tags WHERE id = ?", (node,)).fetchone()
            if row is None:
                raise ValueError("La etiqueta padre no existe.")
            node = row[0]
    conn.execute("UPDATE tags SET parent_id = ? WHERE id = ?", (parent_id, tag_id))


def set_tag_parent(tag_id: int, parent_id: int | None) -> None:
    """Lanza ValueError si crearía un ciclo."""
    with transaction() as conn:
        _set_parent(conn, tag_id, parent_id)


def _set_aliases(conn: sqlite3.Connection, tag_id: int, aliases: list[str]) -> None:
    clean: list[str] = []
    for a in aliases:
        a = a.strip().lower()
        if a and a not in clean:
            clean.append(a)
    for a in clean:
        if conn.execute("SELECT 1 FROM tags WHERE name = ? AND id != ?", (a, tag_id)).fetchone():
            raise TagNameConflictError(f"'{a}' ya es el nombre de otra etiqueta.")
        if conn.execute("SELECT 1 FROM tags WHERE name = ? AND id = ?", (a, tag_id)).fetchone():
            raise ValueError(f"'{a}' es el nombre de la propia etiqueta.")
        if conn.execute("SELECT 1 FROM tag_aliases WHERE alias = ? AND tag_id != ?", (a, tag_id)).fetchone():
            raise TagNameConflictError(f"'{a}' ya es un alias de otra etiqueta.")
    conn.execute("DELETE FROM tag_aliases WHERE tag_id = ?", (tag_id,))
    conn.executemany("INSERT INTO tag_aliases (alias, tag_id) VALUES (?, ?)", [(a, tag_id) for a in clean])


def set_tag_aliases(tag_id: int, aliases: list[str]) -> None:
    with transaction() as conn:
        _set_aliases(conn, tag_id, aliases)


def get_tag_aliases() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for alias, tag_id in get_connection().execute("SELECT alias, tag_id FROM tag_aliases ORDER BY alias"):
        out.setdefault(tag_id, []).append(alias)
    return out


def resolve_tag_name(name: str) -> int | None:
    """Id de la etiqueta con ese nombre o alias (sin distinguir mayúsculas)."""
    name = name.strip().lower()
    conn = get_connection()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT tag_id FROM tag_aliases WHERE alias = ?", (name,)).fetchone()
    return row[0] if row else None


def get_tag_parents() -> dict[int, int | None]:
    return {r[0]: r[1] for r in get_connection().execute("SELECT id, parent_id FROM tags")}


def get_tag_photo_counts() -> dict[int, int]:
    """{tag_id: n.º de fotos} en una sola consulta (contadores del sidebar)."""
    return {
        r[0]: r[1]
        for r in get_connection().execute("SELECT tag_id, COUNT(*) FROM photo_tags GROUP BY tag_id")
    }


def merge_tags(source_id: int, target_id: int) -> int:
    """
    Fusiona `source` en `target`: sus fotos pasan a tener `target`, sus hijas
    y alias pasan a `target`, y su nombre queda como alias de `target` (lo que
    se escriba con el nombre viejo sigue funcionando). Devuelve cuántas fotos
    tenían `source`.
    """
    if source_id == target_id:
        raise ValueError("No se puede fusionar una etiqueta consigo misma.")
    with transaction() as conn:
        src = conn.execute("SELECT name FROM tags WHERE id = ?", (source_id,)).fetchone()
        if src is None or conn.execute("SELECT 1 FROM tags WHERE id = ?", (target_id,)).fetchone() is None:
            raise ValueError("La etiqueta no existe.")
        n = conn.execute("SELECT COUNT(*) FROM photo_tags WHERE tag_id = ?", (source_id,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) SELECT photo_id, ? FROM photo_tags WHERE tag_id = ?",
            (target_id, source_id),
        )
        # Si target desciende de source, sube al lugar de source; si no, las
        # hijas de source (que pasan a target) formarían un ciclo con él
        node = conn.execute("SELECT parent_id FROM tags WHERE id = ?", (target_id,)).fetchone()[0]
        while node is not None and node != source_id:
            node = conn.execute("SELECT parent_id FROM tags WHERE id = ?", (node,)).fetchone()[0]
        if node == source_id:
            conn.execute(
                "UPDATE tags SET parent_id = (SELECT parent_id FROM tags WHERE id = ?) WHERE id = ?",
                (source_id, target_id),
            )
        conn.execute("UPDATE tags SET parent_id = ? WHERE parent_id = ?", (target_id, source_id))
        conn.execute("UPDATE tag_aliases SET tag_id = ? WHERE tag_id = ?", (target_id, source_id))
        conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
        conn.execute("INSERT OR REPLACE INTO tag_aliases (alias, tag_id) VALUES (?, ?)", (src[0], target_id))
    logger.info("Etiqueta %d (%s) fusionada en %d: %d fotos", source_id, src[0], target_id, n)
    return n


def count_photos_with_tag(tag_id: int) -> int:
    return (
        get_connection().execute("SELECT COUNT(*) FROM photo_tags WHERE tag_id = ?", (tag_id,)).fetchone()[0]
    )


def get_tag(tag_id: int) -> Tag | None:
    row = (
        get_connection()
        .execute(
            "SELECT id, name, category, color, hidden, sidebar_hidden, parent_id FROM tags WHERE id = ?",
            (tag_id,),
        )
        .fetchone()
    )
    return _row_to_tag(row) if row else None


def set_tag_hidden(tag_id: int, hidden: bool):
    with transaction() as conn:
        conn.execute("UPDATE tags SET hidden = ? WHERE id = ?", (int(hidden), tag_id))


def set_tag_sidebar_hidden(tag_id: int, hidden: bool):
    with transaction() as conn:
        conn.execute("UPDATE tags SET sidebar_hidden = ? WHERE id = ?", (int(hidden), tag_id))


def delete_tag(tag_id: int):
    with transaction() as conn:
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


def get_tags_by_category(category: str) -> list[Tag]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, color, category, hidden, sidebar_hidden, parent_id FROM tags WHERE category = ? ORDER BY name",
        (category,),
    ).fetchall()
    return [_row_to_tag(r) for r in rows]


# ── Categorías ────────────────────────────────────────────────────────────────


def get_all_categories() -> list[str]:
    conn = get_connection()
    rows = conn.execute("SELECT name FROM categories ORDER BY name").fetchall()
    return [r[0] for r in rows]


def create_category(name: str) -> bool:
    name = name.strip().lower()
    if not name:
        return False
    with transaction() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        return cur.rowcount > 0


def rename_category(old_name: str, new_name: str):
    new_name = new_name.strip().lower()
    if not new_name or new_name == old_name:
        return
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (new_name,))
        conn.execute("UPDATE tags SET category = ? WHERE category = ?", (new_name, old_name))
        conn.execute("DELETE FROM categories WHERE name = ?", (old_name,))


def delete_category(name: str, move_to: str = "general"):
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (move_to,))
        conn.execute("UPDATE tags SET category = ? WHERE category = ?", (move_to, name))
        conn.execute("DELETE FROM categories WHERE name = ?", (name,))


# ── Estadísticas ──────────────────────────────────────────────────────────────


def get_stats() -> Stats:
    conn = get_connection()
    total_photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    years = [
        (r["year"], r["c"])
        for r in conn.execute(
            "SELECT year, COUNT(*) as c FROM photos WHERE year IS NOT NULL GROUP BY year ORDER BY year"
        ).fetchall()
    ]
    by_month = [
        (r["year"], r["month"], r["c"])
        for r in conn.execute(
            "SELECT year, month, COUNT(*) as c FROM photos "
            "WHERE year IS NOT NULL AND month IS NOT NULL "
            "GROUP BY year, month ORDER BY year, month"
        ).fetchall()
    ]
    by_type = {}
    for r in conn.execute("SELECT media_type, COUNT(*) as c FROM photos GROUP BY media_type").fetchall():
        by_type[r["media_type"] or "image"] = r["c"]

    top_tags = [
        (r["name"], r["c"])
        for r in conn.execute("""
            SELECT t.name, COUNT(pt.photo_id) as c
            FROM tags t JOIN photo_tags pt ON pt.tag_id = t.id
            GROUP BY t.id ORDER BY c DESC LIMIT 10
        """).fetchall()
    ]

    return Stats(
        total_photos=total_photos,
        total_tags=total_tags,
        years=years,
        by_month=by_month,
        by_type=by_type,
        top_tags=top_tags,
    )
