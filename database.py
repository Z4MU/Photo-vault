"""
PhotoVault - database.py
Maneja toda la interacción con SQLite.

Mejoras de arquitectura:
  - Conexiones por hilo con threading.local() en lugar de abrir una nueva
    conexión por cada operación. Esto elimina el overhead de sqlite3.connect()
    en cada llamada y es seguro en entornos multihilo (PyQt usa varios hilos).
  - upsert_photo ahora actualiza width y height correctamente.
  - Las funciones de lectura devuelven modelos tipados (Photo, Tag, Stats)
    en lugar de sqlite3.Row o dicts, para que la UI no dependa del esquema SQL.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from models import Photo, Tag, Stats

DB_PATH = Path.home() / ".photovault" / "photovault.db"

# ── Conexiones por hilo ───────────────────────────────────────────────────────
# Cada hilo tiene su propia conexión SQLite. sqlite3 no es thread-safe con una
# conexión compartida, y abrir una conexión por llamada es costoso para
# operaciones frecuentes como cargar miniaturas.

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """
    Devuelve la conexión SQLite del hilo actual.
    La crea la primera vez que el hilo la solicita.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-8000")   # 8 MB de caché por conexión
        _local.conn = conn
    return conn


def close_connection():
    """Cierra la conexión del hilo actual (útil al terminar un QThread)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


# ── Contexto de transacción ───────────────────────────────────────────────────

class _Transaction:
    """Context manager que hace commit/rollback sobre la conexión del hilo."""
    def __enter__(self):
        self._conn = get_connection()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False   # no suprimir excepciones


def transaction() -> "_Transaction":
    return _Transaction()


# ── Inicialización ────────────────────────────────────────────────────────────

def init_db():
    """Crea las tablas si no existen y aplica migraciones."""
    with transaction() as conn:
        conn.executescript("""
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
                added_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tags (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT UNIQUE NOT NULL COLLATE NOCASE,
                category       TEXT DEFAULT 'general',
                color          TEXT DEFAULT '#4A9EFF',
                hidden         INTEGER DEFAULT 0,
                sidebar_hidden INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS photo_tags (
                photo_id    INTEGER REFERENCES photos(id) ON DELETE CASCADE,
                tag_id      INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (photo_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS categories (
                name  TEXT PRIMARY KEY NOT NULL COLLATE NOCASE
            );

            CREATE INDEX IF NOT EXISTS idx_photos_year       ON photos(year);
            CREATE INDEX IF NOT EXISTS idx_photos_month      ON photos(month);
            CREATE INDEX IF NOT EXISTS idx_photo_tags_photo  ON photo_tags(photo_id);
            CREATE INDEX IF NOT EXISTS idx_photo_tags_tag    ON photo_tags(tag_id);
        """)

        # ── Migraciones para DBs viejas ───────────────────────────────────
        for col, typedef in [
            ("media_type", "TEXT DEFAULT 'image'"),
            ("duration",   "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {typedef}")
                conn.commit()
            except Exception:
                pass

        for col, typedef in [("sidebar_hidden", "INTEGER DEFAULT 0")]:
            try:
                conn.execute(f"ALTER TABLE tags ADD COLUMN {col} {typedef}")
                conn.commit()
            except Exception:
                pass

        # Poblar categories desde tags existentes (migración DB vieja)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name) "
                "SELECT DISTINCT category FROM tags WHERE category IS NOT NULL"
            )
        except Exception:
            pass

        # ── Etiquetas y categorías predefinidas ───────────────────────────
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


# ── Fotos ─────────────────────────────────────────────────────────────────────

def upsert_photo(path: str, filename: str, year: int, month: int,
                 filesize: int, width: int = None, height: int = None,
                 media_type: str = "image", duration: float = None) -> int:
    """
    Inserta o actualiza una foto. Ahora incluye width y height en el UPDATE
    (antes solo se actualizaban en INSERT, dejando nulls al re-indexar).
    """
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


def _row_to_photo(row: sqlite3.Row) -> Photo:
    """Convierte una fila SQLite al modelo Photo."""
    return Photo(
        id         = row["id"],
        path       = row["path"],
        filename   = row["filename"],
        year       = row["year"],
        month      = row["month"],
        media_type = row["media_type"] or "image",
        duration   = row["duration"],
    )


def get_photos(tag_ids: list[int] = None, hidden_tag_ids: set[int] = None,
               search: str = None, limit: int = 200, offset: int = 0) -> list[Photo]:
    params = []
    where_clauses = []

    if hidden_tag_ids:
        placeholders = ",".join("?" * len(hidden_tag_ids))
        where_clauses.append(f"""
            p.id NOT IN (
                SELECT photo_id FROM photo_tags
                WHERE tag_id IN ({placeholders})
            )
        """)
        params.extend(hidden_tag_ids)

    if tag_ids:
        for tid in tag_ids:
            where_clauses.append(
                "p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)"
            )
            params.append(tid)

    if search:
        where_clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_connection()
    rows = conn.execute(f"""
        SELECT p.id, p.path, p.filename, p.year, p.month, p.media_type, p.duration
        FROM photos p
        {where_sql}
        ORDER BY p.year DESC, p.month DESC, p.filename
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    return [_row_to_photo(r) for r in rows]


def get_photo_count(tag_ids: list[int] = None, hidden_tag_ids: set[int] = None,
                    search: str = None) -> int:
    params = []
    where_clauses = []

    if hidden_tag_ids:
        placeholders = ",".join("?" * len(hidden_tag_ids))
        where_clauses.append(f"""
            p.id NOT IN (
                SELECT photo_id FROM photo_tags
                WHERE tag_id IN ({placeholders})
            )
        """)
        params.extend(hidden_tag_ids)

    if tag_ids:
        for tid in tag_ids:
            where_clauses.append(
                "p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)"
            )
            params.append(tid)

    if search:
        where_clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_connection()
    row = conn.execute(
        f"SELECT COUNT(*) FROM photos p {where_sql}", params
    ).fetchone()
    return row[0]


def get_photo_by_id(photo_id: int) -> Optional[Photo]:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, path, filename, year, month, media_type, duration FROM photos WHERE id = ?",
        (photo_id,)
    ).fetchone()
    return _row_to_photo(row) if row else None


# ── Etiquetas de una foto ─────────────────────────────────────────────────────

def _row_to_tag(row: sqlite3.Row) -> Tag:
    return Tag(
        id             = row["id"],
        name           = row["name"],
        category       = row["category"] or "general",
        color          = row["color"] or "#4A9EFF",
        hidden         = bool(row["hidden"]) if "hidden" in row.keys() else False,
        sidebar_hidden = bool(row["sidebar_hidden"]) if "sidebar_hidden" in row.keys() else False,
    )


def get_photo_tags(photo_id: int) -> list[Tag]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.id, t.name, t.category, t.color, t.hidden, t.sidebar_hidden
        FROM tags t
        JOIN photo_tags pt ON pt.tag_id = t.id
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


def remove_tag_from_photo(photo_id: int, tag_id: int):
    with transaction() as conn:
        conn.execute(
            "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?",
            (photo_id, tag_id)
        )


# ── Etiquetas globales ────────────────────────────────────────────────────────

def get_all_tags(include_sidebar_hidden: bool = True) -> list[Tag]:
    conn = get_connection()
    if include_sidebar_hidden:
        rows = conn.execute(
            "SELECT id, name, category, color, hidden, sidebar_hidden "
            "FROM tags ORDER BY category, name"
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
        "SELECT id, name, color, category, hidden, sidebar_hidden "
        "FROM tags WHERE category = ? ORDER BY name",
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
    years        = [
        (r["year"], r["c"])
        for r in conn.execute(
            "SELECT year, COUNT(*) as c FROM photos "
            "WHERE year IS NOT NULL GROUP BY year ORDER BY year"
        ).fetchall()
    ]
    return Stats(total_photos=total_photos, total_tags=total_tags, years=years)
