"""
PhotoVault - smart.py
Inteligencia local (fase 10), capa de servicios: sugerir etiquetas por
carpeta y buscar fotos parecidas (hash perceptual). La UI lo usa igual que
`services`; no tiene SQL.
"""

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import database as db
import privacy
import similarity
import thumbnail_cache
from models import DuplicateGroup, Photo, Tag
from services import THUMB_WORKERS, ProgressCallback, StopCheck, _visible_tags

logger = logging.getLogger(__name__)


# ── Sugerir etiquetas por carpeta (#60) ───────────────────────────────────────

# Una etiqueta se sugiere si la tiene al menos esta parte de la carpeta…
MIN_SHARE = 0.2
# …y al menos estas fotos (con 1 de 2 no hay patrón)
MIN_COUNT = 2
PARENT_WEIGHT = 0.5  # la carpeta de arriba cuenta la mitad
NAME_MATCH_SCORE = 0.9  # la etiqueta aparece en el nombre de la carpeta


@dataclass
class TagSuggestion:
    tag: Tag
    score: float  # 0–1
    reason: str  # para el tooltip


def _folder_of(path: str) -> str:
    """Igual que la columna photos.folder: hasta la última barra, incluida."""
    return path[: path.rfind("\\") + 1]


def _parent_folder(folder: str) -> str:
    inner = folder.rstrip("\\")
    cut = inner.rfind("\\")
    return inner[: cut + 1] if cut > 0 else ""


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos, solo letras y números separados por un espacio."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9ñ]+", plain))


def suggest_tags(photo_ids: list[int], limit: int = 6) -> list[TagSuggestion]:
    """
    Etiquetas que probablemente correspondan a estas fotos, por lo que tienen
    las demás fotos de su carpeta (y de la de arriba, con menos peso) y por el
    nombre de la carpeta ("Playa 2019" → «playa»). No sugiere las que ya
    tienen todas, ni las ocultas si están bloqueadas.
    """
    if not photo_ids:
        return []
    photos = db.get_photos_by_ids(photo_ids)
    folders = {_folder_of(p.path) for p in photos}
    parents = {_parent_folder(f) for f in folders} - {""}
    stats = db.get_folder_tag_stats(sorted(folders | parents))
    tags = {t.id: t for t in _visible_tags(db.get_all_tags())}
    have_all = db.get_common_tag_ids(photo_ids)

    scores: dict[int, tuple[float, str]] = {}

    def offer(tag_id: int, score: float, reason: str) -> None:
        if tag_id in tags and tag_id not in have_all and score > scores.get(tag_id, (0.0, ""))[0]:
            scores[tag_id] = (score, reason)

    for folder_set, weight, where in (
        (folders, 1.0, "esta carpeta"),
        (parents, PARENT_WEIGHT, "la carpeta de arriba"),
    ):
        for folder in folder_set:
            total, counts = stats.get(folder, (0, {}))
            for tag_id, n in counts.items():
                share = n / total if total else 0
                if n >= MIN_COUNT and share >= MIN_SHARE:
                    offer(tag_id, share * weight, f"La tienen {n} de {total} fotos de {where}")

    # El nombre de la carpeta (las 2 últimas partes de la ruta, sin la unidad)
    names: set[str] = set()
    for folder in folders:
        parts = [x for x in folder.rstrip("\\").split("\\")[1:] if x]
        names.update(_normalize(x) for x in parts[-2:])
    if names:
        aliases = db.get_tag_aliases()
        for tag_id, tag in tags.items():
            for word in (tag.name, *aliases.get(tag_id, [])):
                w = _normalize(word)
                if w and any(f" {w} " in f" {name} " for name in names):
                    offer(tag_id, NAME_MATCH_SCORE, f"«{word}» aparece en el nombre de la carpeta")
                    break

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], tags[kv[0]].name))
    return [TagSuggestion(tags[tid], round(score, 3), reason) for tid, (score, reason) in ranked[:limit]]


# ── Casi-duplicados (#59) ─────────────────────────────────────────────────────


def compute_missing_phashes(
    progress_callback: ProgressCallback | None = None, should_stop: StopCheck | None = None
) -> int:
    """
    Calcula el hash perceptual de las fotos que no lo tienen, a partir de su
    miniatura (del caché; si falta, se genera). Las ocultas solo con el
    contenido desbloqueado. Devuelve cuántos calculó.
    """
    photos = [p for p in db.get_photos_without_phash() if not p.hidden or privacy.show_hidden_content()]
    total = len(photos)
    if progress_callback:
        progress_callback(0, total)

    def one(p: Photo) -> tuple[int, int] | None:
        jpeg = thumbnail_cache.get_photo_thumbnail(p)
        h = similarity.hash_bytes(jpeg) if jpeg else None
        return (p.id, h) if h is not None else None

    done = 0
    chunk_size = THUMB_WORKERS * 32
    with ThreadPoolExecutor(max_workers=THUMB_WORKERS) as pool:
        for start in range(0, total, chunk_size):
            if should_stop and should_stop():
                break
            chunk = photos[start : start + chunk_size]
            pairs = [r for r in pool.map(one, chunk) if r is not None]
            db.set_phashes(pairs)  # se guarda por tandas: cancelar no pierde lo hecho
            done += len(pairs)
            if progress_callback:
                progress_callback(min(start + len(chunk), total), total)
    logger.info("Hashes perceptuales calculados: %d de %d", done, total)
    return done


def get_similar_groups(
    sensitivity: str = "normal", should_stop: StopCheck | None = None
) -> list[DuplicateGroup]:
    """
    Grupos de fotos parecidas entre las que ya tienen hash. No repite los
    grupos de idénticas (mismo md5) y sin desbloquear no incluye lo oculto.
    Ordenados por tamaño.
    """
    max_distance = similarity.SENSITIVITY.get(sensitivity, similarity.SENSITIVITY["normal"])
    id_groups = similarity.find_similar(db.get_phashes(), max_distance, should_stop)
    if not id_groups:
        return []
    all_ids = [i for g in id_groups for i in g]
    by_id = {p.id: p for p in db.get_photos_by_ids(all_ids)}
    md5s = db.get_md5s(all_ids)
    show_hidden = privacy.show_hidden_content()
    groups = []
    for ids in id_groups:
        photos = sorted((by_id[i] for i in ids if i in by_id), key=lambda p: p.path)
        if len(photos) < 2 or (not show_hidden and any(p.hidden for p in photos)):
            continue
        group_md5s = {md5s.get(p.id) for p in photos}
        if len(group_md5s) == 1 and None not in group_md5s:
            continue  # todas idénticas: ya salen en "Idénticas"
        groups.append(DuplicateGroup(md5="", photos=photos, similar=True))
    groups.sort(key=lambda g: g.size, reverse=True)
    return groups


def find_similar_photos(
    sensitivity: str = "normal",
    progress_callback: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> list[DuplicateGroup]:
    """Para un TaskWorker: calcular los hashes que falten y agrupar."""
    compute_missing_phashes(progress_callback, should_stop)
    if should_stop and should_stop():
        return []
    return get_similar_groups(sensitivity, should_stop)
