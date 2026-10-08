"""
PhotoVault - updates.py
Aviso de versión nueva (fase 11). Capa de servicios, sin Qt: consulta la
última versión publicada en GitHub (solo lectura, una vez al día como mucho)
y dice si es más nueva que la instalada. Nunca descarga ni instala nada: la
UI ofrece abrir la página de la versión.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import config
import database as db

logger = logging.getLogger(__name__)

ENABLED_SETTING = "update_check"
LAST_CHECK_SETTING = "update_last_check"
SKIPPED_SETTING = "update_skipped_version"
CHECK_EVERY_S = 24 * 3600
TIMEOUT_S = 10


@dataclass(frozen=True)
class Release:
    version: str  # "3.1.0"
    url: str  # página de la versión en GitHub
    name: str
    notes: str


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v3.1.0' / '3.1' → (3, 1, 0). None si no parece una versión."""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(candidate: str, current: str = config.APP_VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def is_enabled() -> bool:
    return db.get_setting(ENABLED_SETTING, "1") == "1"


def set_enabled(enabled: bool) -> None:
    db.set_setting(ENABLED_SETTING, "1" if enabled else "0")


def is_due(now: float | None = None) -> bool:
    """¿Toca revisar? (activado y pasó un día desde la última vez)"""
    if not is_enabled():
        return False
    try:
        last = float(db.get_setting(LAST_CHECK_SETTING, "0") or 0)
    except ValueError:
        last = 0
    return (now if now is not None else time.time()) - last >= CHECK_EVERY_S


def skip_version(version: str) -> None:
    """No volver a avisar de esta versión."""
    db.set_setting(SKIPPED_SETTING, version)


def fetch_latest(url: str = config.RELEASES_API) -> Release | None:
    """La última versión publicada (None si no hay o no hay conexión). Lanza solo si `url` es inválida."""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        e.close()  # trae la respuesta abierta
        if e.code != 404:  # 404 = todavía no hay versiones publicadas
            logger.warning("No se pudo consultar la última versión: HTTP %s", e.code)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        logger.info("No se pudo consultar la última versión (¿sin conexión?): %s", e)
        return None
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None or data.get("draft") or data.get("prerelease"):
        return None
    return Release(
        version=tag.lstrip("v"),
        url=str(data.get("html_url") or config.REPO_URL),
        name=str(data.get("name") or tag),
        notes=str(data.get("body") or "")[:2000],
    )


def check_for_update(force: bool = False, progress_callback=None, should_stop=None) -> Release | None:
    """
    La versión nueva para avisar, o None. Sin `force` respeta la frecuencia y
    la versión que el usuario pidió saltar. (Firma de TaskWorker.)
    """
    if not force and not is_due():
        return None
    db.set_setting(LAST_CHECK_SETTING, str(time.time()))
    release = fetch_latest()
    if release is None or not is_newer(release.version):
        return None
    if not force and db.get_setting(SKIPPED_SETTING, "") == release.version:
        return None
    logger.info("Hay una versión nueva: %s", release.version)
    return release
