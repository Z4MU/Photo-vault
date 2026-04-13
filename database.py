"""
PhotoVault - database.py
Maneja toda la interacción con SQLite.
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path.home() / ".photovault" / "photovault.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Crea las tablas si no existen."""
    with get_connection() as conn:
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
            ("media_type",     "TEXT DEFAULT 'image'"),
            ("duration",       "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {typedef}")
            except Exception:
                pass
        for col, typedef in [("sidebar_hidden", "INTEGER DEFAULT 0")]:
            try:
                conn.execute(f"ALTER TABLE tags ADD COLUMN {col} {typedef}")
            except Exception:
                pass

        # Poblar categories desde tags existentes (migración base de datos vieja)
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


def upsert_photo(path: str, filename: str, year: int, month: int,
                 filesize: int, width: int = None, height: int = None,
                 media_type: str = "image", duration: float = None) -> int:
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO photos (path, filename, media_type, year, month, filesize, width, height, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                filename=excluded.filename,
                media_type=excluded.media_type,
                year=excluded.year,
                month=excluded.month,
                filesize=excluded.filesize,
                duration=excluded.duration
            RETURNING id
        """, (path, filename, media_type, year, month, filesize, width, height, duration))
        row = cur.fetchone()
        return row[0]


def get_photos(tag_ids: list[int] = None, hidden_tag_ids: set[int] = None,
               search: str = None, limit: int = 200, offset: int = 0):
    """
    Devuelve fotos filtradas.
    - tag_ids: lista de IDs de etiquetas que la foto DEBE tener (AND)
    - hidden_tag_ids: fotos que tengan CUALQUIERA de estas etiquetas se excluyen
    - search: busca en el nombre de archivo
    """
    params = []
    where_clauses = []

    # Excluir fotos con etiquetas ocultas
    if hidden_tag_ids:
        placeholders = ",".join("?" * len(hidden_tag_ids))
        where_clauses.append(f"""
            p.id NOT IN (
                SELECT photo_id FROM photo_tags
                WHERE tag_id IN ({placeholders})
            )
        """)
        params.extend(hidden_tag_ids)

    # Filtrar por etiquetas seleccionadas
    if tag_ids:
        for tid in tag_ids:
            where_clauses.append("""
                p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)
            """)
            params.append(tid)

    # Búsqueda por nombre
    if search:
        where_clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT p.id, p.path, p.filename, p.year, p.month, p.media_type, p.duration
            FROM photos p
            {where_sql}
            ORDER BY p.year DESC, p.month DESC, p.filename
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
    return rows


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
            where_clauses.append("p.id IN (SELECT photo_id FROM photo_tags WHERE tag_id = ?)")
            params.append(tid)

    if search:
        where_clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with get_connection() as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM photos p {where_sql}", params).fetchone()
    return row[0]


def get_photo_tags(photo_id: int):
    with get_connection() as conn:
        return conn.execute("""
            SELECT t.id, t.name, t.category, t.color
            FROM tags t
            JOIN photo_tags pt ON pt.tag_id = t.id
            WHERE pt.photo_id = ?
            ORDER BY t.category, t.name
        """, (photo_id,)).fetchall()


def set_photo_tags(photo_id: int, tag_ids: list[int]):
    with get_connection() as conn:
        conn.execute("DELETE FROM photo_tags WHERE photo_id = ?", (photo_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            [(photo_id, tid) for tid in tag_ids]
        )


def add_tag_to_photo(photo_id: int, tag_id: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
            (photo_id, tag_id)
        )


def remove_tag_from_photo(photo_id: int, tag_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?",
            (photo_id, tag_id)
        )


# ─── Etiquetas ────────────────────────────────────────────────────────────────

def get_all_tags(include_sidebar_hidden: bool = True):
    with get_connection() as conn:
        if include_sidebar_hidden:
            return conn.execute(
                "SELECT id, name, category, color, hidden, sidebar_hidden FROM tags ORDER BY category, name"
            ).fetchall()
        else:
            return conn.execute(
                "SELECT id, name, category, color, hidden, sidebar_hidden FROM tags WHERE sidebar_hidden = 0 ORDER BY category, name"
            ).fetchall()


def set_tag_sidebar_hidden(tag_id: int, hidden: bool):
    with get_connection() as conn:
        conn.execute("UPDATE tags SET sidebar_hidden = ? WHERE id = ?", (int(hidden), tag_id))


def get_hidden_tag_ids() -> set[int]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM tags WHERE hidden = 1").fetchall()
    return {r[0] for r in rows}


def create_tag(name: str, category: str = "general", color: str = "#4A9EFF") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tags (name, category, color) VALUES (?, ?, ?) RETURNING id",
            (name.strip().lower(), category, color)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        # Ya existía, devolver su id
        return conn.execute("SELECT id FROM tags WHERE name = ?", (name.strip().lower(),)).fetchone()[0]


def set_tag_hidden(tag_id: int, hidden: bool):
    with get_connection() as conn:
        conn.execute("UPDATE tags SET hidden = ? WHERE id = ?", (int(hidden), tag_id))


def delete_tag(tag_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))



def get_all_categories() -> list[str]:
    """Devuelve categorías de la tabla categories, ordenadas."""
    with get_connection() as conn:
        rows = conn.execute("SELECT name FROM categories ORDER BY name").fetchall()
    return [r[0] for r in rows]


def create_category(name: str) -> bool:
    """Crea una categoría nueva. Devuelve True si se creó, False si ya existía."""
    name = name.strip().lower()
    if not name:
        return False
    with get_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        return cur.rowcount > 0


def rename_category(old_name: str, new_name: str):
    """Renombra una categoría y actualiza todas las etiquetas que la usen."""
    new_name = new_name.strip().lower()
    if not new_name or new_name == old_name:
        return
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (new_name,))
        conn.execute("UPDATE tags SET category = ? WHERE category = ?", (new_name, old_name))
        conn.execute("DELETE FROM categories WHERE name = ?", (old_name,))


def delete_category(name: str, move_to: str = "general"):
    """Elimina una categoría moviendo sus etiquetas a otra."""
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (move_to,))
        conn.execute("UPDATE tags SET category = ? WHERE category = ?", (move_to, name))
        conn.execute("DELETE FROM categories WHERE name = ?", (name,))


def get_tags_by_category(category: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, name, color FROM tags WHERE category = ? ORDER BY name",
            (category,)
        ).fetchall()


def get_stats():
    with get_connection() as conn:
        total_photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        total_tags   = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        years        = conn.execute(
            "SELECT year, COUNT(*) as c FROM photos WHERE year IS NOT NULL GROUP BY year ORDER BY year"
        ).fetchall()
    return {"total_photos": total_photos, "total_tags": total_tags, "years": years}
