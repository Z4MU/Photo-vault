"""
PhotoVault - similarity.py
Casi-duplicados (fase 10): hash perceptual de una miniatura y búsqueda de
fotos parecidas (redimensionadas, recomprimidas, ráfagas). Sin Qt ni DB.

Hash: pHash de 64 bits (la imagen en gris a 32×32 → DCT → las 8×8
frecuencias más bajas comparadas con su mediana). Dos fotos son parecidas si
sus hashes difieren en pocos bits (distancia de Hamming). Se probó dHash y con
la colección real encadenaba capturas de pantalla y fondos lisos sin relación.
Una imagen casi lisa no tiene información: su hash es FLAT (0) y no se compara.

Grupos: no se encadenan (si A~B y B~C pero A≁C, C no entra en el grupo de A):
cada grupo tiene una foto central y todas se parecen a ella.

Búsqueda sin comparar todas contra todas (172k² = 1,5·10¹⁰ pares): si la
distancia es ≤ d, al partir el hash en d + 1 bandas al menos una banda es
idéntica (palomar). Solo se comparan las fotos que comparten alguna banda.
"""

import io
import logging
from collections.abc import Callable

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

HASH_BITS = 64
# Sensibilidad → distancia máxima (bits distintos de 64)
SENSITIVITY = {"strict": 3, "normal": 6, "loose": 8}
# Grupos de una banda más grandes que esto se comparan por tramos (memoria)
_BLOCK = 2048
# Hash de una imagen sin información (casi lisa): se guarda para no recalcularla, no se compara
FLAT = 0
_FLAT_STD = 4.0  # desviación de grises (0–255) por debajo de la cual es "lisa"
_N = 32
# Matriz de la DCT-II (la escala no importa: solo se compara con la mediana)
_DCT = np.cos(np.pi * (2 * np.arange(_N)[None, :] + 1) * np.arange(_N)[:, None] / (2 * _N))


def phash_image(img: Image.Image) -> int:
    """pHash de 64 bits como entero CON signo (así entra en una columna INTEGER de SQLite)."""
    small = np.asarray(img.convert("L").resize((_N, _N), Image.Resampling.LANCZOS), dtype=np.float64)
    if small.std() < _FLAT_STD:
        return FLAT
    low = (_DCT @ small @ _DCT.T)[:8, :8].flatten()
    bits = low > np.median(low[1:])  # sin el componente continuo (el brillo medio)
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return to_signed(value) or 1  # 0 queda reservado para FLAT


def hash_bytes(data: bytes) -> int | None:
    """pHash de una imagen en bytes (la miniatura JPEG del caché). None si no se pudo leer."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return phash_image(img)
    except Exception as e:
        logger.warning("No se pudo calcular el hash perceptual: %s", e)
        return None


def to_signed(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value


def distance(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


def find_similar(
    items: list[tuple[int, int]],
    max_distance: int,
    should_stop: Callable[[], bool] | None = None,
) -> list[list[int]]:
    """
    Grupos de ids parecidos. `items`: [(id, hash con signo)]; los FLAT se
    ignoran. Solo grupos de 2 o más; dentro de cada grupo, todos se parecen
    a la primera foto (el centro).
    """
    items = [it for it in items if it[1] != FLAT]
    if len(items) < 2:
        return []
    ids = np.array([i for i, _ in items], dtype=np.int64)
    hashes = np.array([h for _, h in items], dtype=np.int64).view(np.uint64)
    parent = list(range(len(items)))

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # compresión de camino
            parent[x], x = root, parent[x]
        return root

    def union_pairs(rows: np.ndarray, cols: np.ndarray) -> None:
        for a, b in zip(rows.tolist(), cols.tolist(), strict=True):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

    n_bands = max_distance + 1
    edges = np.linspace(0, HASH_BITS, n_bands + 1).astype(int)
    for band in range(n_bands):
        lo, hi = int(edges[band]), int(edges[band + 1])
        mask = np.uint64((1 << (hi - lo)) - 1)
        keys = (hashes >> np.uint64(lo)) & mask
        order = np.argsort(keys, kind="stable")
        sorted_keys = keys[order]
        # Inicio de cada tramo de claves iguales
        starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
        ends = np.r_[starts[1:], len(order)]
        for s, e in zip(starts.tolist(), ends.tolist(), strict=True):
            if e - s < 2:
                continue
            if should_stop and should_stop():
                return []
            members = order[s:e]
            hb = hashes[members]
            for r0 in range(0, len(members), _BLOCK):
                block = hb[r0 : r0 + _BLOCK]
                d = np.bitwise_count(block[:, None] ^ hb[None, :])
                rows, cols = np.nonzero(d <= max_distance)
                keep = cols > rows + r0  # cada par una vez, sin la diagonal
                if keep.any():
                    union_pairs(members[rows[keep] + r0], members[cols[keep]])

    components: dict[int, list[int]] = {}
    for idx in range(len(items)):
        components.setdefault(find(idx), []).append(idx)
    out: list[list[int]] = []
    for component in components.values():
        if len(component) < 2:
            continue
        for cluster in _split_around_centers(component, hashes, max_distance):
            if len(cluster) >= 2:
                out.append([int(ids[i]) for i in cluster])
    return out


def _split_around_centers(members: list[int], hashes: np.ndarray, max_distance: int) -> list[list[int]]:
    """
    Parte una componente conexa en grupos donde todos se parecen al primero:
    sin esto, una cadena A~B~C~… juntaba fotos sin relación.
    """
    if len(members) == 2:
        return [members]
    clusters: list[list[int]] = []
    centers: list[int] = []
    for m in members:
        h = int(hashes[m])
        for c, center in enumerate(centers):
            if (h ^ center).bit_count() <= max_distance:
                clusters[c].append(m)
                break
        else:
            centers.append(h)
            clusters.append([m])
    return clusters
