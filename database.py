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
from pathlib import Path
from typing import Optional

import backup
from models import Photo, SortField, SortOrder, Stats, Tag, TrashBatch, sort_to_sql

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".photovault" / "photovault.db"

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-8000")
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
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str,
                           column: str, typedef: str) -> None:
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
    _add_column_if_missing(conn, "photos", "duration",   "REAL")
    _add_column_if_missing(conn, "photos", "md5",        "TEXT")
    _add_column_if_missing(conn, "tags",   "hidden",         "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "tags",   "sidebar_hidden", "INTEGER DEFAULT 0")

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


_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _m001_baseline,
    _m002_internal_trash,
]

SCHEMA_VERSION = len(_MIGRATIONS)


def get_schema_version() -> int:
    return get_connection().execute("PRAGMA user_version").fetchone()[0]


def _apply_migration(conn: sqlite3.Connection, version: int,
                     migration: Callable[[sqlite3.Connection], None]) -> None:
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
        logger.exception("Falló la migración %d; la DB quedó en la versión %d",
                         version, version - 1)
        raise


def init_db() -> None:
    """
    Corre en cada arranque. Aplica las migraciones pendientes (con backup
    previo si la DB ya existía) y el seed inicial si nunca se hizo.
    """
    conn    = get_connection()
    current = get_schema_version()

    if current > SCHEMA_VERSION:
        raise DatabaseTooNewError(
            f"La base de datos está en la versión {current} del esquema, pero esta "
            f"versión de PhotoVault solo conoce hasta la {SCHEMA_VERSION}.\n"
            f"Actualiza PhotoVault para abrirla. No se modificó nada."
        )

    if current < SCHEMA_VERSION:
        has_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0] > 0
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
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'seeded'"
    ).fetchone()
    if row:
        return   # Ya se sembró — respetar el estado actual del usuario

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
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        ('seeded', '1')
    )
    conn.commit()


# ── Fotos ─────────────────────────────────────────────────────────────────────

_PHOTO_COLUMNS = (
    "p.id, p.path, p.filename, p.year, p.month, p.media_type, p.duration, "
    "p.filesize, p.width, p.height, p.added_at"
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


def upsert_photo(path: str, filename: str, year: int | None, month: int | None,
                 filesize: int, width: int | None = None, height: int | None = None,
                 media_type: str = "image", duration: float | None = None) -> int:
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO photos (path, filename, media_type, year, month,
                                filesize, width, height, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                filename   = excluded.filename,
                media_type = excluded.media_type,
                year       = excluded.year,
                month      = excluded.month,
                filesize   = excluded.filesize,
                width      = excluded.width,
                height     = excluded.height,
                duration   = excluded.duration
            RETURNING id
        """, (path, filename, media_type, year, month, filesize, width, height, duration))
        row = cur.fetchone()
        return row[0]


def update_photo_md5(photo_id: int, md5: str):
    with transaction() as conn:
        conn.execute("UPDATE photos SET md5 = ? WHERE id = ?", (md5, photo_id))


def _row_to_photo(row: sqlite3.Row) -> Photo:
    keys = row.keys()
    return Photo(
        id         = row["id"],
        path       = row["path"],
        filename   = row["filename"],
        year       = row["year"],
        month      = row["month"],
        media_type = row["media_type"] or "image",
        duration   = row["duration"],
        filesize   = row["filesize"] if "filesize" in keys else None,
        width      = row["width"]    if "width"    in keys else None,
        height     = row["height"]   if "height"   in keys else None,
        added_at   = row["added_at"] if "added_at" in keys else None,
        md5        = row["md5"]      if "md5"      in keys else None,
    )


def get_photos(tag_ids: list[int] = None, hidden_tag_ids: set[int] = None,
               search: str = None, limit: int = 200, offset: int = 0,
               sort_field: SortField = SortField.DATE,
               sort_order: SortOrder = SortOrder.DESC,
               folder: str = None,
               untagged_only: bool = False) -> list[Photo]:
    params = []
    where_clauses = []

    if hidden_tag_ids:
        placeholders = ",".join("?" * len(hidden_tag_ids))
        where_clauses.append(f"""
            p.id NOT IN (
                SELECT photo_id FROM photo_tags WHERE tag_id IN ({placeholders})
            )
        """)
        params.extend(hidden_tag_ids)

    if tag_ids:
        for tid in tag_ids:
            where_clauses.append("p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)")
            params.append(tid)

    if search:
        where_clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")

    if folder:
        where_clauses.append("p.path LIKE ? ESCAPE '!'")
        params.append(folder_like_pattern(folder))

    if untagged_only:
        where_clauses.append("p.id NOT IN (SELECT DISTINCT photo_id FROM photo_tags)")

    where_sql  = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    order_sql  = sort_to_sql(sort_field, sort_order)

    conn = get_connection()
    rows = conn.execute(f"""
        SELECT {_PHOTO_COLUMNS}
        FROM photos p
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    return [_row_to_photo(r) for r in rows]


def get_photo_count(tag_ids: list[int] = None, hidden_tag_ids: set[int] = None,
                    search: str = None, folder: str = None,
                    untagged_only: bool = False) -> int:
    params = []
    where_clauses = []

    if hidden_tag_ids:
        placeholders = ",".join("?" * len(hidden_tag_ids))
        where_clauses.append(f"""
            p.id NOT IN (
                SELECT photo_id FROM photo_tags WHERE tag_id IN ({placeholders})
            )
        """)
        params.extend(hidden_tag_ids)

    if tag_ids:
        for tid in tag_ids:
            where_clauses.append("p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)")
            params.append(tid)

    if search:
        where_clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")

    if folder:
        where_clauses.append("p.path LIKE ? ESCAPE '!'")
        params.append(folder_like_pattern(folder))

    if untagged_only:
        where_clauses.append("p.id NOT IN (SELECT DISTINCT photo_id FROM photo_tags)")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    conn = get_connection()
    row  = conn.execute(f"SELECT COUNT(*) FROM photos p {where_sql}", params).fetchone()
    return row[0]


def get_photo_by_id(photo_id: int) -> Optional[Photo]:
    conn = get_connection()
    row  = conn.execute(
        f"SELECT {_PHOTO_COLUMNS}, p.md5 FROM photos p WHERE p.id = ?",
        (photo_id,)
    ).fetchone()
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
    "path", "filename", "media_type", "year", "month", "filesize",
    "width", "height", "duration", "md5", "added_at",
)


def _chunks(items: list, size: int = 500):
    # SQLite limita la cantidad de parámetros por consulta
    for i in range(0, len(items), size):
        yield items[i:i + size]


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
            photos = conn.execute(
                f"SELECT id, {cols} FROM photos WHERE id IN ({ph})", chunk
            ).fetchall()
            tags: dict[int, list[dict]] = {}
            for r in conn.execute(f"""
                SELECT pt.photo_id, t.name, t.category, t.color
                FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                WHERE pt.photo_id IN ({ph})
            """, chunk):
                tags.setdefault(r[0], []).append(
                    {"name": r[1], "category": r[2], "color": r[3]}
                )
            conn.executemany(
                "INSERT INTO deleted_photos (batch_id, reason, path, photo_json, tags_json) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (batch_id, reason, r["path"],
                     json.dumps({k: r[k] for k in _TRASH_PHOTO_FIELDS}, ensure_ascii=False),
                     json.dumps(tags.get(r["id"], []), ensure_ascii=False))
                    for r in photos
                ],
            )
            conn.execute(f"DELETE FROM photos WHERE id IN ({ph})", chunk)
    logger.info("%d registros a la papelera interna (lote %s, %s)",
                len(photo_ids), batch_id, reason)
    return len(photo_ids)


# ── Papelera interna ──────────────────────────────────────────────────────────

def list_trash_batches() -> list[TrashBatch]:
    rows = get_connection().execute("""
        SELECT batch_id, reason, MIN(deleted_at) AS deleted_at, COUNT(*) AS n,
               SUM(CASE WHEN tags_json != '[]' THEN 1 ELSE 0 END) AS tagged
        FROM deleted_photos
        GROUP BY batch_id
        ORDER BY deleted_at DESC, MIN(id) DESC
    """).fetchall()
    return [TrashBatch(batch_id=r["batch_id"], reason=r["reason"], deleted_at=r["deleted_at"],
                       count=r["n"], tagged=r["tagged"]) for r in rows]


def count_trash() -> int:
    return get_connection().execute("SELECT COUNT(*) FROM deleted_photos").fetchone()[0]


def _get_or_create_tag(conn: sqlite3.Connection, name: str,
                       category: str | None, color: str | None) -> int:
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
            existing = conn.execute(
                "SELECT id FROM photos WHERE path = ?", (data["path"],)
            ).fetchone()
            if existing:
                photo_id = existing[0]
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
        new_path = new_n + path[len(old_n):]
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
                conn.execute("DELETE FROM photos WHERE id = ?", (pid,))
                merged += 1
    return moved, merged


# ── Asignaciones foto ↔ etiqueta (exportar / importar) ───────────────────────

def get_all_assignments() -> list[tuple[str, str, int | None, list[str]]]:
    """[(ruta, nombre, tamaño, [etiquetas])] de todas las fotos con etiquetas."""
    rows = get_connection().execute("""
        SELECT p.path, p.filename, p.filesize, t.name
        FROM photo_tags pt
        JOIN photos p ON p.id = pt.photo_id
        JOIN tags   t ON t.id = pt.tag_id
        ORDER BY p.path, t.name
    """).fetchall()
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
        conn.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)", pairs
        )
        return conn.total_changes - before


def get_tag_ids_by_name() -> dict[str, int]:
    return {r[1]: r[0] for r in get_connection().execute("SELECT id, name FROM tags")}


def get_photo_ids_with_tag(tag_id: int) -> list[int]:
    return [r[0] for r in get_connection().execute(
        "SELECT photo_id FROM photo_tags WHERE tag_id = ?", (tag_id,)
    )]


def get_tagged_photo_ids() -> list[int]:
    return [r[0] for r in get_connection().execute("SELECT DISTINCT photo_id FROM photo_tags")]


def count_tagged(photo_ids: list[int]) -> int:
    """Cuántas de estas fotos tienen al menos una etiqueta."""
    if not photo_ids:
        return 0
    conn  = get_connection()
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


# ── Configuración del usuario ─────────────────────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    row = get_connection().execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
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
        id             = row["id"],
        name           = row["name"],
        category       = row["category"] or "general",
        color          = row["color"] or "#4A9EFF",
        hidden         = bool(row["hidden"]) if "hidden" in keys else False,
        sidebar_hidden = bool(row["sidebar_hidden"]) if "sidebar_hidden" in keys else False,
    )


def get_photo_tags(photo_id: int) -> list[Tag]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.id, t.name, t.category, t.color, t.hidden, t.sidebar_hidden
        FROM tags t JOIN photo_tags pt ON pt.tag_id = t.id
        WHERE pt.photo_id = ?
        ORDER BY t.category, t.name
    """, (photo_id,)).fetchall()
    return [_row_to_tag(r) for r in rows]


def set_photo_tags(photo_id: int, tag_ids: list[int]):
    with transaction() as conn:
        conn.execute("DELETE FROM photo_tags WHERE photo_id = ?", (photo_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            [(photo_id, tid) for tid in tag_ids]
        )


def add_tag_to_photo(photo_id: int, tag_id: int):
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            (photo_id, tag_id)
        )


def add_tag_to_photos(photo_ids: list[int], tag_id: int):
    """Agrega una etiqueta a múltiples fotos de una vez."""
    with transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            [(pid, tag_id) for pid in photo_ids]
        )


def remove_tag_from_photo(photo_id: int, tag_id: int):
    with transaction() as conn:
        conn.execute(
            "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?",
            (photo_id, tag_id)
        )


def remove_tag_from_photos(photo_ids: list[int], tag_id: int):
    """Quita una etiqueta de múltiples fotos de una vez."""
    with transaction() as conn:
        conn.executemany(
            "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?",
            [(pid, tag_id) for pid in photo_ids]
        )


# ── Etiquetas globales ────────────────────────────────────────────────────────

def get_all_tags(include_sidebar_hidden: bool = True) -> list[Tag]:
    conn = get_connection()
    if include_sidebar_hidden:
        rows = conn.execute(
            "SELECT id, name, category, color, hidden, sidebar_hidden FROM tags ORDER BY category, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, category, color, hidden, sidebar_hidden "
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
            (name.strip().lower(), category, color)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        return conn.execute(
            "SELECT id FROM tags WHERE name = ?", (name.strip().lower(),)
        ).fetchone()[0]


class TagNameConflictError(ValueError):
    """Ya existe otra etiqueta con ese nombre."""


def update_tag(tag_id: int, name: str, category: str, color: str) -> None:
    """Cambia nombre, categoría y color de una etiqueta existente."""
    name     = name.strip().lower()
    category = category.strip().lower() or "general"
    if not name:
        raise ValueError("El nombre de etiqueta no puede estar vacío.")
    with transaction() as conn:
        clash = conn.execute(
            "SELECT id FROM tags WHERE name = ? AND id != ?", (name, tag_id)
        ).fetchone()
        if clash:
            raise TagNameConflictError(f"Ya existe una etiqueta llamada '{name}'.")
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
        conn.execute(
            "UPDATE tags SET name = ?, category = ?, color = ? WHERE id = ?",
            (name, category, color, tag_id),
        )


def count_photos_with_tag(tag_id: int) -> int:
    return get_connection().execute(
        "SELECT COUNT(*) FROM photo_tags WHERE tag_id = ?", (tag_id,)
    ).fetchone()[0]


def get_tag(tag_id: int) -> Optional[Tag]:
    row = get_connection().execute(
        "SELECT id, name, category, color, hidden, sidebar_hidden FROM tags WHERE id = ?",
        (tag_id,),
    ).fetchone()
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
        "SELECT id, name, color, category, hidden, sidebar_hidden FROM tags WHERE category = ? ORDER BY name",
        (category,)
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
    total_tags   = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

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
        total_photos = total_photos,
        total_tags   = total_tags,
        years        = years,
        by_month     = by_month,
        by_type      = by_type,
        top_tags     = top_tags,
    )
