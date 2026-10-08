"""
PhotoVault - privacy.py
Contenido oculto (fase 9): PIN, desbloqueo, código de recuperación y cifrado
de las miniaturas de lo oculto. Capa de servicios: la UI puede usarlo igual
que `services` (no tiene SQL; guarda todo con db.get/set_setting).

Cómo funciona:
- Al crear el PIN se genera una **clave maestra** aleatoria (32 bytes). Se
  guarda cifrada dos veces (AES-GCM): con una clave derivada del PIN (scrypt)
  y con una derivada del código de recuperación. Nunca se guarda en claro.
- Desbloquear = descifrar la clave maestra con el PIN (si el PIN es incorrecto,
  AES-GCM falla). La clave queda en memoria hasta `lock()`.
- Con la clave en memoria, las miniaturas de las fotos ocultas se guardan
  cifradas en otra carpeta (thumbnail_cache.PRIVATE_DIR) con nombres HMAC.
- Cambiar el PIN solo vuelve a cifrar la clave maestra: el caché sigue valiendo.

Límites (decírselos al usuario): la base de datos NO está cifrada (rutas y
nombres de etiquetas se pueden leer con otro programa) y un PIN corto se puede
adivinar probando todos fuera de la app. Protege de miradas, no de un experto.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import database as db
import thumbnail_cache

logger = logging.getLogger(__name__)

VAULT_SETTING = "privacy_vault"
ATTEMPTS_SETTING = "privacy_attempts"
OPTIONS_SETTING = "privacy_options"

PIN_MIN_LENGTH = 4
# scrypt: ~0,1 s por intento (los tests lo bajan)
KDF_N = 2**15
KDF_R = 8
KDF_P = 1
# Tras MAX_ATTEMPTS errores seguidos hay que esperar LOCKOUT_SECONDS × (errores // MAX_ATTEMPTS)
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 30
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I
_AAD_MASTER = b"photovault-master-key"
_AAD_THUMB = b"photovault-thumb"

PANIC_ACTIONS = {
    "minimize": "Bloquear, cerrar ventanas y minimizar",
    "close": "Bloquear y cerrar PhotoVault",
    "lock": "Solo bloquear y cerrar ventanas",
}

_master_key: bytes | None = None


class PrivacyError(Exception):
    """PIN inválido, sin PIN o bloqueado: el mensaje es para el usuario."""


class LockedOutError(PrivacyError):
    def __init__(self, seconds: int):
        super().__init__(f"Demasiados intentos. Espera {seconds} s.")
        self.seconds = seconds


# ── Estado ────────────────────────────────────────────────────────────────────


def has_pin() -> bool:
    return _load_vault() is not None


def is_unlocked() -> bool:
    return _master_key is not None


def hidden_locked() -> bool:
    """Hay PIN y no está desbloqueado: lo oculto (fotos y nombres de etiquetas) no se muestra."""
    return has_pin() and not is_unlocked()


def show_hidden_content() -> bool:
    """Las fotos con etiquetas ocultas se muestran solo con el contenido desbloqueado."""
    return is_unlocked()


def lock() -> None:
    global _master_key
    if _master_key is not None:
        logger.info("Contenido oculto bloqueado")
    _master_key = None
    thumbnail_cache.set_private_codec(None)


def _set_unlocked(master: bytes) -> None:
    global _master_key
    _master_key = master
    thumbnail_cache.set_private_codec(_ThumbCodec(master))


# ── PIN ───────────────────────────────────────────────────────────────────────


def pin_problem(pin: str) -> str | None:
    """Por qué no sirve un PIN nuevo (None si sirve)."""
    if len(pin) < PIN_MIN_LENGTH:
        return f"El PIN debe tener al menos {PIN_MIN_LENGTH} caracteres."
    if pin != pin.strip():
        return "El PIN no puede empezar ni terminar con espacios."
    return None


def set_pin(pin: str) -> str:
    """
    Crea el PIN (no debe haber uno) y deja el contenido desbloqueado.
    Devuelve el código de recuperación: mostrarlo una sola vez al usuario.
    """
    if has_pin():
        raise PrivacyError("Ya hay un PIN.")
    problem = pin_problem(pin)
    if problem:
        raise PrivacyError(problem)
    master = secrets.token_bytes(32)
    code = _new_recovery_code()
    _save_vault({"v": 1, "pin": _wrap(master, pin), "recovery": _wrap(master, _normalize_code(code))})
    _clear_attempts()
    _set_unlocked(master)
    logger.info("PIN de contenido oculto creado")
    return code


def unlock(pin: str) -> bool:
    """True si el PIN es correcto (queda desbloqueado). Lanza LockedOutError si hay que esperar."""
    return _unlock_with("pin", pin)


def unlock_with_recovery(code: str) -> bool:
    """Igual que unlock() pero con el código de recuperación (después: change_pin)."""
    return _unlock_with("recovery", _normalize_code(code))


def change_pin(new_pin: str) -> None:
    """Cambia el PIN. Requiere estar desbloqueado (la UI pide el actual antes)."""
    vault = _load_vault()
    if vault is None or _master_key is None:
        raise PrivacyError("Primero desbloquea el contenido oculto.")
    problem = pin_problem(new_pin)
    if problem:
        raise PrivacyError(problem)
    vault["pin"] = _wrap(_master_key, new_pin)
    _save_vault(vault)
    logger.info("PIN de contenido oculto cambiado")


def new_recovery_code() -> str:
    """Genera otro código de recuperación (el anterior deja de servir). Requiere estar desbloqueado."""
    vault = _load_vault()
    if vault is None or _master_key is None:
        raise PrivacyError("Primero desbloquea el contenido oculto.")
    code = _new_recovery_code()
    vault["recovery"] = _wrap(_master_key, _normalize_code(code))
    _save_vault(vault)
    return code


def remove_pin() -> None:
    """
    Quita el PIN (requiere estar desbloqueado). Lo oculto vuelve a quedar
    oculto sin forma de verlo hasta crear otro PIN; el caché cifrado se borra
    (sin la clave no sirve).
    """
    if _master_key is None:
        raise PrivacyError("Primero desbloquea el contenido oculto.")
    lock()
    db.set_setting(VAULT_SETTING, "")
    _clear_attempts()
    thumbnail_cache.clear_private_cache()
    logger.info("PIN de contenido oculto quitado")


def lockout_remaining() -> int:
    """Segundos que faltan para poder volver a intentar (0 = se puede)."""
    until = _load_attempts().get("until", 0)
    return max(0, int(round(until - time.time())))


# ── Opciones (bloqueo automático y modo pánico) ───────────────────────────────


@dataclass
class PrivacyOptions:
    autolock_minutes: int = 5  # 0 = nunca
    lock_on_minimize: bool = True
    panic_key: str = "F12"
    panic_action: str = "minimize"  # PANIC_ACTIONS


def get_options() -> PrivacyOptions:
    defaults = PrivacyOptions()
    try:
        raw = json.loads(db.get_setting(OPTIONS_SETTING, "") or "{}")
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    minutes = raw.get("autolock_minutes", defaults.autolock_minutes)
    action = raw.get("panic_action", defaults.panic_action)
    key = raw.get("panic_key", defaults.panic_key)
    return PrivacyOptions(
        autolock_minutes=max(0, min(240, minutes)) if isinstance(minutes, int) else defaults.autolock_minutes,
        lock_on_minimize=bool(raw.get("lock_on_minimize", defaults.lock_on_minimize)),
        panic_key=key if isinstance(key, str) else defaults.panic_key,
        panic_action=action if action in PANIC_ACTIONS else defaults.panic_action,
    )


def set_options(options: PrivacyOptions) -> None:
    db.set_setting(
        OPTIONS_SETTING,
        json.dumps(
            {
                "autolock_minutes": max(0, min(240, int(options.autolock_minutes))),
                "lock_on_minimize": bool(options.lock_on_minimize),
                "panic_key": options.panic_key,
                "panic_action": options.panic_action if options.panic_action in PANIC_ACTIONS else "minimize",
            }
        ),
    )


# Teclas que ya usa la app sin modificadores: no sirven como tecla de pánico
_RESERVED_KEYS = {"F1", "F5", "F11", "Esc", "Return", "Enter", "Space", "Del", "Backspace", "Tab"}


def panic_key_problem(key: str) -> str | None:
    """Por qué una tecla no sirve para el modo pánico (None si sirve). `key` en formato QKeySequence."""
    if not key:
        return None  # sin tecla = modo pánico desactivado
    if "," in key:
        return "Usa una sola tecla o combinación."
    if key in _RESERVED_KEYS:
        return f"«{key}» ya la usa PhotoVault."
    parts = key.split("+")
    base = parts[-1] or "+"
    has_modifier = len(parts) > 1
    if not has_modifier and (len(base) == 1 or base in ("Left", "Right", "Up", "Down", "PgUp", "PgDown")):
        return "Una letra, número o flecha sola se usa al etiquetar: agrega Ctrl o Alt, o usa una tecla F."
    return None


# ── Cifrado de miniaturas ─────────────────────────────────────────────────────


class _ThumbCodec:
    """Lo que thumbnail_cache necesita para el caché privado (no conoce la clave)."""

    def __init__(self, master: bytes):
        self._aes = AESGCM(hashlib.sha256(master + b"thumbs").digest())
        self._name_key = hashlib.sha256(master + b"names").digest()

    def name(self, text: str) -> str:
        # HMAC: el nombre del archivo no permite saber de qué foto es
        return hmac.new(self._name_key, text.encode(), hashlib.sha256).hexdigest()[:40]

    def encrypt(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, data, _AAD_THUMB)

    def decrypt(self, blob: bytes) -> bytes | None:
        try:
            return self._aes.decrypt(blob[:12], blob[12:], _AAD_THUMB)
        except (InvalidTag, ValueError):
            return None


# ── Interno ───────────────────────────────────────────────────────────────────


def _kdf(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=KDF_N, r=KDF_R, p=KDF_P, maxmem=128 * 1024 * 1024, dklen=32
    )


def _wrap(master: bytes, secret: str) -> dict:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ct = AESGCM(_kdf(secret, salt)).encrypt(nonce, master, _AAD_MASTER)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "n": KDF_N,
        "data": base64.b64encode(nonce + ct).decode("ascii"),
    }


def _unwrap(entry: dict, secret: str) -> bytes | None:
    try:
        salt = base64.b64decode(entry["salt"])
        blob = base64.b64decode(entry["data"])
        n = int(entry.get("n", KDF_N))
        key = hashlib.scrypt(
            secret.encode("utf-8"), salt=salt, n=n, r=KDF_R, p=KDF_P, maxmem=128 * 1024 * 1024, dklen=32
        )
        return AESGCM(key).decrypt(blob[:12], blob[12:], _AAD_MASTER)
    except (InvalidTag, KeyError, ValueError, TypeError):
        return None


def _unlock_with(kind: str, secret: str) -> bool:
    vault = _load_vault()
    if vault is None:
        raise PrivacyError("No hay un PIN creado.")
    wait = lockout_remaining()
    if wait:
        raise LockedOutError(wait)
    master = _unwrap(vault.get(kind, {}), secret) if secret else None
    if master is None:
        _register_failure()
        logger.warning("Intento fallido de desbloquear el contenido oculto (%s)", kind)
        return False
    _clear_attempts()
    _set_unlocked(master)
    logger.info("Contenido oculto desbloqueado")
    return True


def _load_vault() -> dict | None:
    raw = db.get_setting(VAULT_SETTING, "")
    if not raw:
        return None
    try:
        vault = json.loads(raw)
    except ValueError:
        logger.error("La configuración del PIN está dañada")
        return None
    return vault if isinstance(vault, dict) and "pin" in vault else None


def _save_vault(vault: dict) -> None:
    db.set_setting(VAULT_SETTING, json.dumps(vault))


def _load_attempts() -> dict:
    try:
        raw = json.loads(db.get_setting(ATTEMPTS_SETTING, "") or "{}")
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _register_failure() -> None:
    fails = int(_load_attempts().get("fails", 0)) + 1
    data: dict[str, float] = {"fails": fails}
    if fails % MAX_ATTEMPTS == 0:
        data["until"] = time.time() + LOCKOUT_SECONDS * (fails // MAX_ATTEMPTS)
    db.set_setting(ATTEMPTS_SETTING, json.dumps(data))


def _clear_attempts() -> None:
    db.set_setting(ATTEMPTS_SETTING, "")


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(20))
    return "-".join(raw[i : i + 5] for i in range(0, 20, 5))


def _normalize_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())
