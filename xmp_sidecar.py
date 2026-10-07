"""
PhotoVault - xmp_sidecar.py
Archivos .xmp "sidecar" junto a las fotos con sus etiquetas.

Para que las etiquetas no dependan solo de la DB: otros programas (digiKam,
darktable, Lightroom, XnView…) leen las palabras clave de estos archivos.

- Nombre: <foto>.<ext>.xmp (p. ej. IMG_1234.JPG.xmp), convención de
  digiKam/darktable. Al LEER también se acepta <foto>.xmp (Lightroom).
- Etiquetas en dc:subject; con categoría (y padres) en lr:hierarchicalSubject
  ("categoria|etiqueta" o "categoria|padre|etiqueta").
- Valoración (1–5 estrellas) en xmp:Rating, el estándar que leen Lightroom,
  digiKam y el Explorador de Windows.
- Solo se sobrescriben o borran sidecars creados por PhotoVault (marca
  photovault:managed). Un .xmp de otro programa nunca se toca.
"""

import logging
import os
import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "photovault": "https://github.com/Z4MU/Photo-vault/ns/1.0/",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

_MANAGED_ATTR = f"{{{NS['photovault']}}}managed"


class WriteResult(Enum):
    WRITTEN = "escrito"
    REMOVED = "eliminado"  # la foto quedó sin etiquetas
    UNCHANGED = "sin cambios"
    SKIPPED_FOREIGN = "omitido"  # existe un .xmp de otro programa
    ERROR = "error"


def sidecar_path(photo_path: str) -> Path:
    """Ruta donde PhotoVault escribe el sidecar: <foto>.<ext>.xmp"""
    return Path(photo_path + ".xmp")


def candidate_paths(photo_path: str) -> list[Path]:
    """Rutas donde buscar un sidecar al leer (las dos convenciones)."""
    p = Path(photo_path)
    return [sidecar_path(photo_path), p.with_suffix(".xmp")]


_RATING_ATTR = f"{{{NS['xmp']}}}Rating"


def build_xmp(tags: list[tuple[str, str | None]], rating: int = 0) -> str:
    """
    XMP con las etiquetas [(nombre, categoría)] y la valoración. La categoría
    puede traer los padres ("cat|padre"). Los textos se escapan solos.
    """
    q = lambda prefix, tag: f"{{{NS[prefix]}}}{tag}"  # noqa: E731

    meta = ET.Element(q("x", "xmpmeta"), {q("x", "xmptk"): "PhotoVault"})
    rdf = ET.SubElement(meta, q("rdf", "RDF"))
    attrs = {q("rdf", "about"): "", _MANAGED_ATTR: "True"}
    if rating:
        attrs[_RATING_ATTR] = str(int(rating))
    desc = ET.SubElement(rdf, q("rdf", "Description"), attrs)

    subject = ET.SubElement(ET.SubElement(desc, q("dc", "subject")), q("rdf", "Bag"))
    for name, _cat in tags:
        ET.SubElement(subject, q("rdf", "li")).text = name

    hier = ET.SubElement(ET.SubElement(desc, q("lr", "hierarchicalSubject")), q("rdf", "Bag"))
    for name, cat in tags:
        ET.SubElement(hier, q("rdf", "li")).text = f"{cat}|{name}" if cat else name

    ET.indent(meta, space=" ")
    body = ET.tostring(meta, encoding="unicode")
    return f'<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n{body}\n<?xpacket end="w"?>\n'


def parse_xmp(text: str) -> tuple[list[tuple[str, str | None]], bool]:
    """Lee las etiquetas de un XMP. Devuelve ([(nombre, categoría)], es_de_photovault)."""
    tags, managed, _rating = parse_xmp_full(text)
    return tags, managed


def _rating_of(root: ET.Element) -> int:
    for el in root.iter():
        raw = el.get(_RATING_ATTR)
        if raw is None:
            child = el.find("xmp:Rating", NS)
            raw = child.text if child is not None else None
        if raw:
            try:
                return max(0, min(5, round(float(raw))))  # -1 = "rechazada" en Lightroom → 0
            except ValueError:
                return 0
    return 0


def parse_xmp_full(text: str) -> tuple[list[tuple[str, str | None]], bool, int]:
    """
    Lee un XMP. Devuelve ([(nombre, categoría)], es_de_photovault, valoración).
    La categoría sale de lr:hierarchicalSubject si existe ("cat|nombre" o
    "cat|sub|nombre" → categoría = primer nivel).
    """
    # ElementTree no entiende las instrucciones <?xpacket?> fuera del root
    start = text.find("<x:xmpmeta")
    if start < 0:
        start = text.find("<rdf:RDF")
    end_meta = text.rfind("</x:xmpmeta>")
    if start >= 0 and end_meta > start:
        text = text[start : end_meta + len("</x:xmpmeta>")]
    root = ET.fromstring(text)

    managed = any(el.get(_MANAGED_ATTR) == "True" for el in root.iter())

    names = [
        li.text.strip() for li in root.iterfind(".//dc:subject//rdf:li", NS) if li.text and li.text.strip()
    ]
    categories: dict[str, str] = {}
    for li in root.iterfind(".//lr:hierarchicalSubject//rdf:li", NS):
        if li.text and "|" in li.text:
            parts = [p.strip() for p in li.text.split("|") if p.strip()]
            if len(parts) >= 2:
                categories.setdefault(parts[-1].lower(), parts[0].lower())

    seen: set[str] = set()
    out: list[tuple[str, str | None]] = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append((key, categories.get(key)))
    return out, managed, _rating_of(root)


def read_sidecar_full(photo_path: str) -> tuple[list[tuple[str, str | None]], int] | None:
    """(etiquetas, valoración) del sidecar de una foto, o None si no tiene (o no se puede leer)."""
    for candidate in candidate_paths(photo_path):
        if candidate.exists():
            try:
                tags, _managed, rating = parse_xmp_full(
                    candidate.read_text(encoding="utf-8", errors="replace")
                )
                return tags, rating
            except (OSError, ET.ParseError) as e:
                logger.warning("No se pudo leer el sidecar %s: %s", candidate, e)
    return None


def read_sidecar(photo_path: str) -> list[tuple[str, str | None]] | None:
    """Etiquetas del sidecar de una foto, o None si no tiene (o no se puede leer)."""
    full = read_sidecar_full(photo_path)
    return full[0] if full is not None else None


def write_sidecar(photo_path: str, tags: list[tuple[str, str | None]], rating: int = 0) -> WriteResult:
    """
    Crea/actualiza el sidecar de PhotoVault. Sin etiquetas ni valoración, lo
    elimina (solo si es nuestro). Nunca toca un .xmp creado por otro programa.
    """
    target = sidecar_path(photo_path)
    has_data = bool(tags) or rating > 0
    try:
        if target.exists():
            current = target.read_text(encoding="utf-8", errors="replace")
            try:
                old_tags, managed, old_rating = parse_xmp_full(current)
            except ET.ParseError:
                managed, old_tags, old_rating = False, [], 0
            if not managed:
                logger.info("Sidecar de otro programa, no se modifica: %s", target)
                return WriteResult.SKIPPED_FOREIGN
            if not has_data:
                target.unlink()
                return WriteResult.REMOVED
            # Al leer, la categoría es solo el primer nivel de "cat|padre|etiqueta"
            new_cmp = sorted((n.lower(), c.split("|")[0] if c else None) for n, c in tags)
            if sorted(old_tags) == new_cmp and old_rating == rating:
                return WriteResult.UNCHANGED
        elif not has_data:
            return WriteResult.UNCHANGED

        if not target.parent.exists():
            # Unidad desconectada o carpeta movida: no crear carpetas nuevas
            logger.warning("No existe la carpeta de %s; sidecar no escrito", photo_path)
            return WriteResult.ERROR

        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(build_xmp(tags, rating), encoding="utf-8")
        os.replace(tmp, target)
        return WriteResult.WRITTEN
    except OSError as e:
        logger.warning("No se pudo escribir el sidecar %s: %s", target, e)
        return WriteResult.ERROR
