"""
PhotoVault - smart.py
Inteligencia local (fase 10), capa de servicios: sugerir etiquetas por
carpeta, buscar fotos parecidas (hash perceptual) y búsqueda por contenido con
CLIP (texto → fotos, foto → fotos, etiquetas y contenido adulto por parecido).
La UI lo usa igual que `services`; no tiene SQL.
"""

import io
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

import clip_model
import config
import database as db
import embedding_store
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


# ── Búsqueda por contenido (#55) y etiquetas por contenido (#56) ──────────────

# Medido con 3.000 fotos reales: con el modelo multilingüe el parecido texto-foto
# está muy comprimido (mediana ~0,22, lo relevante ~0,28), así que un umbral fijo
# no sirve. Aparece lo que destaca TEXT_MIN_Z desviaciones sobre el promedio de esa búsqueda.
TEXT_MIN_Z = 2.5
RESULTS_MAX = 1000  # búsqueda por texto o por foto: las más parecidas
# Foto-foto: el 99 % de los pares al azar está por debajo de ~0,81
SIMILAR_MIN_SCORE = 0.83
TAG_MIN_EXAMPLES = 5  # con menos fotos etiquetadas se usa el nombre de la etiqueta
# Se sugiere una etiqueta si la foto se parece a su grupo tanto como este
# percentil de sus propias fotos (umbral distinto para cada etiqueta)
SUGGEST_PERCENTILE = 25
_CHUNK_ROWS = 20_000  # producto por tramos: la matriz entera en float32 ocuparía ~350 MB
_ANALYZE_BATCH = 16
_ANALYZE_SIDE = 448  # lado al leer el original (draft de JPEG: rápido)

ADULT_PROMPTS = (
    "a nude person",
    "explicit sexual content",
    "pornographic image",
    "naked anime girl, hentai",
    "topless woman",
)
SAFE_PROMPTS = (
    "a normal photo",
    "a person wearing clothes",
    "a screenshot",
    "a landscape",
    "a cartoon character fully clothed",
    "food",
    "a pet",
)

_matrix: tuple[int, np.ndarray, np.ndarray] | None = None  # (versión del almacén, ids, vectores)
_ranking_cache: dict[tuple, list[int]] = {}
_text_cache: dict[str, np.ndarray] = {}
_centroids: tuple[tuple, dict[int, tuple[np.ndarray, float]]] | None = None


AUTO_ANALYZE_SETTING = "ai_auto_analyze"


def content_model_installed() -> bool:
    return clip_model.is_installed()


def is_auto_analyze() -> bool:
    """¿Analizar solas las fotos nuevas después de indexar? (por defecto sí)"""
    return db.get_setting(AUTO_ANALYZE_SETTING, "1") == "1"


def set_auto_analyze(enabled: bool) -> None:
    db.set_setting(AUTO_ANALYZE_SETTING, "1" if enabled else "0")


def forget_content_analysis() -> None:
    """Borra los vectores (se pueden volver a calcular)."""
    global _matrix
    embedding_store.clear()
    _matrix = None
    _ranking_cache.clear()


def analysis_status() -> tuple[int, int]:
    """(fotos analizadas, total de fotos)."""
    return embedding_store.count(clip_model.MODEL_ID), db.get_totals()[0]


def _load_for_clip(p: Photo) -> Image.Image | None:
    """Imagen para CLIP: el original reducido al leerlo; un video, su miniatura grande."""
    try:
        if p.is_video:
            jpeg = thumbnail_cache.get_photo_thumbnail(p, config.THUMB_SIZE_LARGE)
            if not jpeg:
                return None
            with Image.open(io.BytesIO(jpeg)) as im:
                return im.convert("RGB")
        with Image.open(p.path) as im:
            im.draft("RGB", (_ANALYZE_SIDE, _ANALYZE_SIDE))
            rotated = ImageOps.exif_transpose(im) or im
            rotated.thumbnail((_ANALYZE_SIDE, _ANALYZE_SIDE))
            return rotated.convert("RGB")
    except Exception as e:
        logger.warning("No se pudo leer %s para analizarla: %s", p.path, e)
        return None


def analyze_photos(
    progress_callback: ProgressCallback | None = None, should_stop: StopCheck | None = None
) -> int:
    """
    Calcula el vector CLIP de las fotos que no lo tienen (o cambiaron desde
    entonces). Lo oculto, solo desbloqueado. Se guarda por tandas: cancelar no
    pierde lo hecho. Devuelve cuántas analizó.
    """
    model = clip_model.get_model()
    stored = embedding_store.stored(clip_model.MODEL_ID)
    photos = db.get_photos(limit=-1)
    valid = {p.id for p in photos}
    embedding_store.delete([i for i in stored if i not in valid])  # des-indexadas
    show_hidden = privacy.show_hidden_content()
    todo = [
        p for p in photos if (p.id not in stored or stored[p.id] != p.mtime) and (show_hidden or not p.hidden)
    ]
    total = len(todo)
    if progress_callback:
        progress_callback(0, total)
    done = 0
    chunk_size = _ANALYZE_BATCH * 4
    with ThreadPoolExecutor(max_workers=THUMB_WORKERS) as pool:
        # Mientras el modelo procesa una tanda, los hilos ya leen la siguiente
        pending = [pool.submit(_load_for_clip, p) for p in todo[:chunk_size]]
        for start in range(0, total, chunk_size):
            chunk = todo[start : start + chunk_size]
            images = [f.result() for f in pending]
            if should_stop and should_stop():
                break
            nxt = todo[start + chunk_size : start + 2 * chunk_size]
            pending = [pool.submit(_load_for_clip, p) for p in nxt]
            rows: list[tuple[int, float | None, np.ndarray]] = []
            ok = [(p, im) for p, im in zip(chunk, images, strict=True) if im is not None]
            for b in range(0, len(ok), _ANALYZE_BATCH):
                part = ok[b : b + _ANALYZE_BATCH]
                vecs = model.encode_images([im for _, im in part])
                rows.extend((p.id, p.mtime, v) for (p, _), v in zip(part, vecs, strict=True))
            # Las que no se pudieron leer quedan con un vector nulo (no se reintentan
            # hasta que el archivo cambie, y nunca se parecen a nada)
            rows.extend(
                (p.id, p.mtime, np.zeros(clip_model.DIM))
                for p, im in zip(chunk, images, strict=True)
                if im is None
            )
            embedding_store.save(clip_model.MODEL_ID, rows)
            done += len(ok)
            if progress_callback:
                progress_callback(min(start + len(chunk), total), total)
        for f in pending:
            f.cancel()
    logger.info("Fotos analizadas por contenido: %d de %d", done, total)
    return done


def _vectors() -> tuple[np.ndarray, np.ndarray]:
    global _matrix
    v = embedding_store.version()
    if _matrix is None or _matrix[0] != v:
        ids, vecs = embedding_store.load(clip_model.MODEL_ID, clip_model.DIM)
        _matrix = (v, ids, vecs)
    return _matrix[1], _matrix[2]


def _scores(query: np.ndarray) -> np.ndarray:
    """Coseno de cada foto analizada con `query` (k vectores → (k, n))."""
    _ids, vecs = _vectors()
    q = np.atleast_2d(query).astype(np.float32)
    out = np.empty((q.shape[0], len(vecs)), dtype=np.float32)
    for s in range(0, len(vecs), _CHUNK_ROWS):
        out[:, s : s + _CHUNK_ROWS] = q @ vecs[s : s + _CHUNK_ROWS].astype(np.float32).T
    return out


def _text_vectors(texts: tuple[str, ...]) -> np.ndarray:
    missing = [t for t in texts if t not in _text_cache]
    if missing:
        for t, v in zip(missing, clip_model.get_model().encode_texts(missing), strict=True):
            _text_cache[t] = v
    return np.stack([_text_cache[t] for t in texts])


def _rank(scores: np.ndarray, min_score: float, limit: int | None) -> list[int]:
    ids, _vecs = _vectors()
    keep = np.flatnonzero(scores >= min_score)
    order = keep[np.argsort(-scores[keep], kind="stable")]
    if limit is not None:
        order = order[:limit]
    return ids[order].tolist()


def _tag_centroids() -> dict[int, tuple[np.ndarray, float]]:
    """
    {etiqueta: (promedio normalizado de sus fotos analizadas, umbral para
    sugerirla)}, solo las que tienen TAG_MIN_EXAMPLES o más. El umbral es el
    parecido de una foto típica de la etiqueta con su promedio. Se recalcula
    si cambian los vectores o las etiquetas.
    """
    global _centroids
    pairs = db.get_photo_tag_pairs()
    key = (embedding_store.version(), hash(tuple(pairs)))
    if _centroids is not None and _centroids[0] == key:
        return _centroids[1]
    ids, vecs = _vectors()
    out: dict[int, tuple[np.ndarray, float]] = {}
    if len(ids) and pairs:
        arr = np.array(pairs, dtype=np.int64)
        rows = np.clip(np.searchsorted(ids, arr[:, 0]), 0, len(ids) - 1)
        found = ids[rows] == arr[:, 0]
        arr, rows = arr[found], rows[found]
        for tag_id in np.unique(arr[:, 1]).tolist():
            sel = vecs[rows[arr[:, 1] == tag_id]].astype(np.float32)
            sel = sel[np.abs(sel).sum(axis=1) > 0]  # sin las que no se pudieron leer
            if len(sel) >= TAG_MIN_EXAMPLES:
                c = sel.mean(axis=0)
                c = c / max(float(np.linalg.norm(c)), 1e-9)
                out[int(tag_id)] = (c, float(np.percentile(sel @ c, SUGGEST_PERCENTILE)))
    _centroids = (key, out)
    return out


def tag_vector(tag_id: int) -> tuple[np.ndarray, str]:
    """
    Cómo "se ve" una etiqueta: el promedio de sus fotos analizadas (si hay
    TAG_MIN_EXAMPLES o más) o, si no, el texto de su nombre. → (vector, origen)
    """
    centroid = _tag_centroids().get(tag_id)
    if centroid is not None:
        return centroid[0], "fotos"
    tag = db.get_tag(tag_id)
    return _text_vectors((f"una foto de {tag.name if tag else ''}",))[0], "nombre"


def adult_scores() -> np.ndarray:
    adult = _scores(_text_vectors(ADULT_PROMPTS)).max(axis=0)
    safe = _scores(_text_vectors(SAFE_PROMPTS)).max(axis=0)
    return adult - safe


def semantic_ranking(semantic: str) -> list[int]:
    """
    Ids de las fotos analizadas, de la más parecida a la menos, para:
    - un texto ("perro en la playa"): las que destacan (TEXT_MIN_Z), hasta RESULTS_MAX;
    - "@foto:<id>": parecidas por contenido a esa foto;
    - "@etiqueta:<id>": todas, ordenadas por parecido con esa etiqueta (revisión);
    - "@adulto": todas, de la que más parece contenido adulto a la que menos (revisión).
    Sin el modelo o sin fotos analizadas devuelve [].
    """
    if not clip_model.is_installed():
        return []
    ids, _vecs = _vectors()
    if not len(ids):
        return []
    key: tuple = (semantic, embedding_store.version())
    if semantic.startswith("@etiqueta:"):
        key += (id(_tag_centroids()),)  # cambia si cambiaron las etiquetas
    if key in _ranking_cache:
        return _ranking_cache[key]
    if semantic.startswith("@foto:"):
        vec = embedding_store.get(clip_model.MODEL_ID, int(semantic.split(":", 1)[1]), clip_model.DIM)
        ranking = _rank(_scores(vec)[0], SIMILAR_MIN_SCORE, RESULTS_MAX) if vec is not None else []
    elif semantic.startswith("@etiqueta:"):
        vec, _src = tag_vector(int(semantic.split(":", 1)[1]))
        ranking = _rank(_scores(vec)[0], -np.inf, None)
    elif semantic == "@adulto":
        # Sin corte: el límite no es claro (dibujos), se revisan de la más probable a la menos
        ranking = _rank(adult_scores(), -np.inf, None)
    else:
        scores = _scores(_text_vectors((semantic.strip(),)))[0]
        ranking = _rank(scores, float(scores.mean() + TEXT_MIN_Z * scores.std()), RESULTS_MAX)
    if len(_ranking_cache) > 32:
        _ranking_cache.clear()
    _ranking_cache[key] = ranking
    return ranking


def content_tag_suggestions(photo_id: int, limit: int = 4) -> list[TagSuggestion]:
    """Etiquetas cuyas fotos se parecen a esta (solo las que tienen TAG_MIN_EXAMPLES analizadas)."""
    if not clip_model.is_installed():
        return []
    vec = embedding_store.get(clip_model.MODEL_ID, photo_id, clip_model.DIM)
    if vec is None or not vec.any():
        return []
    have = {t.id for t in db.get_photo_tags(photo_id)}
    centroids = _tag_centroids()
    out = []
    for tag in _visible_tags(db.get_all_tags()):
        if tag.id in have or tag.id not in centroids:
            continue
        centroid, threshold = centroids[tag.id]
        score = float(vec @ centroid)
        if score >= threshold:
            out.append(TagSuggestion(tag, round(score, 3), f"Se parece a tus fotos con «{tag.name}»"))
    out.sort(key=lambda s: -s.score)
    return out[:limit]
