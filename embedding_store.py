"""
PhotoVault - embedding_store.py
Vectores CLIP de cada foto (fase 10) en ~/.photovault/embeddings.db.

Va aparte de la DB principal a propósito: son ~1 KB por foto (≈ 175 MB con
la colección real), se pueden regenerar, y no deben inflar los backups
diarios. Igual que el caché de miniaturas, se puede borrar sin perder nada.

Cada función abre y cierra su conexión (se usa desde hilos de trabajo y la
UI; abrir SQLite cuesta milisegundos). WAL: se puede leer mientras se escribe.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np

import config

logger = logging.getLogger(__name__)

DB_PATH = config.EMBEDDINGS_DB
# Cambia cada vez que se guardan o borran vectores (para invalidar la matriz en memoria)
_version = 0


def version() -> int:
    return _version


def _bump() -> None:
    global _version
    _version += 1


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            " photo_id INTEGER PRIMARY KEY, model TEXT NOT NULL, mtime REAL, vec BLOB NOT NULL)"
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def save(model: str, rows: list[tuple[int, float | None, np.ndarray]]) -> None:
    """[(photo_id, mtime del archivo al calcularlo, vector)]. Se guarda en float16 (1 KB)."""
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (photo_id, model, mtime, vec) VALUES (?, ?, ?, ?)",
            [(pid, model, mtime, np.asarray(vec, dtype=np.float16).tobytes()) for pid, mtime, vec in rows],
        )
    _bump()


def stored(model: str) -> dict[int, float | None]:
    """{photo_id: mtime} de los vectores de este modelo."""
    if not DB_PATH.exists():
        return {}
    with _connect() as conn:
        return dict(
            conn.execute("SELECT photo_id, mtime FROM embeddings WHERE model = ?", (model,)).fetchall()
        )


def count(model: str) -> int:
    if not DB_PATH.exists():
        return 0
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)).fetchone()[0]


def load(model: str, dim: int) -> tuple[np.ndarray, np.ndarray]:
    """(ids int64 (n,), vectores float16 (n, dim)) de este modelo."""
    if not DB_PATH.exists():
        return np.zeros(0, dtype=np.int64), np.zeros((0, dim), dtype=np.float16)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT photo_id, vec FROM embeddings WHERE model = ? ORDER BY photo_id", (model,)
        ).fetchall()
    ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
    vecs = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), dim)
    return ids, vecs


def get(model: str, photo_id: int, dim: int) -> np.ndarray | None:
    if not DB_PATH.exists():
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT vec FROM embeddings WHERE model = ? AND photo_id = ?", (model, photo_id)
        ).fetchone()
    return np.frombuffer(row[0], dtype=np.float16).astype(np.float32).reshape(dim) if row else None


def delete(photo_ids: list[int]) -> int:
    """Quita los vectores de estas fotos (des-indexadas). Devuelve cuántos borró."""
    if not photo_ids or not DB_PATH.exists():
        return 0
    removed = 0
    with _connect() as conn:
        for i in range(0, len(photo_ids), 500):
            chunk = photo_ids[i : i + 500]
            cur = conn.execute(
                f"DELETE FROM embeddings WHERE photo_id IN ({','.join('?' * len(chunk))})", chunk
            )
            removed += cur.rowcount
    if removed:
        _bump()
    return removed


def delete_other_models(model: str) -> None:
    if DB_PATH.exists():
        with _connect() as conn:
            conn.execute("DELETE FROM embeddings WHERE model != ?", (model,))
        _bump()


def clear() -> None:
    """Borra todos los vectores (se vuelven a calcular)."""
    for suffix in ("", "-wal", "-shm"):
        p = DB_PATH.with_name(DB_PATH.name + suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("No se pudo borrar %s: %s", p, e)
    _bump()


def size_mb() -> float:
    total = sum(
        p.stat().st_size
        for suffix in ("", "-wal")
        if (p := DB_PATH.with_name(DB_PATH.name + suffix)).exists()
    )
    return round(total / 1_048_576, 1)
