"""
PhotoVault - clip_model.py
Modelo CLIP local (fase 10): convierte fotos y textos en vectores de 512
números comparables entre sí ("perro en la playa" queda cerca de las fotos de
perros en la playa). Sin Qt ni DB.

Modelos (descarga aparte, ~225 MB, a ~/.photovault/models/<MODEL_ID>/):
- Imagen: CLIP ViT-B/32 de OpenAI (MIT), exportado a ONNX y cuantizado
  (Xenova/clip-vit-base-patch32).
- Texto: sentence-transformers/clip-ViT-B-32-multilingual-v1 (Apache 2.0),
  entrenado para caer en el mismo espacio que el de imagen en 50+ idiomas
  (español incluido). ONNX cuantizado + capa densa 768→512 (safetensors).

Las URLs apuntan a una revisión fija y se verifica el SHA-256 de cada archivo.
"""

import hashlib
import json
import logging
import os
import shutil
import struct
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

import config

logger = logging.getLogger(__name__)

MODEL_ID = "clip-b32-multilingual-v1"
DIM = 512
MODELS_DIR = config.MODELS_DIR
IMAGE_SIZE = 224
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
MAX_TOKENS = 128
# Hilos de onnxruntime: dejar CPU libre para la UI y la lectura de archivos
INFERENCE_THREADS = max(1, min(4, (os.cpu_count() or 2) // 2))

_X = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/d15189d7028b43f1d3e65039190477f6af591c2a"
_S = (
    "https://huggingface.co/sentence-transformers/clip-ViT-B-32-multilingual-v1/resolve/"
    "58edf8cada9e398793dca955574a48cbb7f18be2"
)


@dataclass(frozen=True)
class ModelFile:
    name: str  # nombre local
    url: str
    sha256: str
    size: int


FILES = (
    ModelFile(
        "vision.onnx",
        f"{_X}/onnx/vision_model_quantized.onnx",
        "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
        89_117_001,
    ),
    ModelFile(
        "text.onnx",
        f"{_S}/onnx/model_quint8_avx2.onnx",
        "fbc8fbeaa5237d96bd1bf430057c70d34de8334a463e933a5faa305f6caeed9c",
        135_377_779,
    ),
    ModelFile(
        "dense.safetensors",
        f"{_S}/2_Dense/model.safetensors",
        "d12568dc7300970a4d3dbb49068ad16cd89b99840b74b026f8e48071e9414f74",
        1_572_984,
    ),
    ModelFile(
        "tokenizer.json",
        f"{_S}/tokenizer.json",
        "5b4e1a8171c81dfd666ae40265b9530c6e0b3d53923fe8ac493dcc84229adf81",
        1_961_847,
    ),
)
DOWNLOAD_SIZE = sum(f.size for f in FILES)


class ModelNotInstalledError(RuntimeError):
    pass


def model_dir() -> Path:
    return MODELS_DIR / MODEL_ID


def is_installed() -> bool:
    d = model_dir()
    return all((d / f.name).is_file() and (d / f.name).stat().st_size == f.size for f in FILES)


def download(
    progress_callback: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """
    Descarga los archivos que falten (verificando el SHA-256). Devuelve False
    si se canceló. Lanza OSError/ValueError si falla la red o un hash.
    """
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    done = sum(f.size for f in FILES if (d / f.name).is_file() and (d / f.name).stat().st_size == f.size)
    for f in FILES:
        target = d / f.name
        if target.is_file() and target.stat().st_size == f.size:
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        logger.info("Descargando %s (%d MB)", f.url, f.size // 1_048_576)
        req = urllib.request.Request(f.url, headers={"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
            while True:
                if should_stop and should_stop():
                    out.close()
                    tmp.unlink(missing_ok=True)
                    return False
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress_callback:
                    progress_callback(done, DOWNLOAD_SIZE)
        if digest.hexdigest() != f.sha256:
            tmp.unlink(missing_ok=True)
            raise ValueError(
                f"El archivo descargado {f.name} no coincide con el esperado (¿descarga dañada?)"
            )
        os.replace(tmp, target)
    return True


def remove() -> None:
    shutil.rmtree(model_dir(), ignore_errors=True)


# ── Inferencia ────────────────────────────────────────────────────────────────


def preprocess(img: Image.Image) -> np.ndarray:
    """Como CLIPImageProcessor: lado corto a 224 (bicúbico), recorte central 224×224, normalizar. → (3, 224, 224)"""
    img = img.convert("RGB")
    w, h = img.size
    scale = IMAGE_SIZE / min(w, h)
    img = img.resize(
        (max(IMAGE_SIZE, round(w * scale)), max(IMAGE_SIZE, round(h * scale))), Image.Resampling.BICUBIC
    )
    w, h = img.size
    left, top = (w - IMAGE_SIZE) // 2, (h - IMAGE_SIZE) // 2
    img = img.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))
    arr = (np.asarray(img, dtype=np.float32) / 255.0 - _MEAN) / _STD
    return arr.transpose(2, 0, 1)


def _normalize(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return (v / np.maximum(norms, 1e-12)).astype(np.float32)


def _load_dense(path: Path) -> np.ndarray:
    """Pesos de la capa densa (512×768) de un .safetensors sin depender de torch."""
    raw = path.read_bytes()
    (n,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + n])
    info = header["linear.weight"]
    start, end = info["data_offsets"]
    return np.frombuffer(raw[8 + n + start : 8 + n + end], dtype=np.float32).reshape(info["shape"])


class ClipModel:
    """Se carga una vez (≈ 1 s) y se puede usar desde varios hilos."""

    def __init__(self) -> None:
        if not is_installed():
            raise ModelNotInstalledError("El modelo de búsqueda por contenido no está descargado.")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = model_dir()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = INFERENCE_THREADS
        opts.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        self._vision = ort.InferenceSession(str(d / "vision.onnx"), opts, providers=providers)
        self._text = ort.InferenceSession(str(d / "text.onnx"), opts, providers=providers)
        self._dense = _load_dense(d / "dense.safetensors")
        self._tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self._tok.enable_truncation(MAX_TOKENS)
        self._tok.enable_padding()
        self._lock = threading.Lock()  # el tokenizador no es seguro entre hilos

    def encode_images(self, images: list[Image.Image]) -> np.ndarray:
        """(n, 512) normalizados."""
        if not images:
            return np.zeros((0, DIM), dtype=np.float32)
        batch = np.stack([preprocess(im) for im in images])
        (emb,) = self._vision.run(["image_embeds"], {"pixel_values": batch})
        return _normalize(emb)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        """(n, 512) normalizados: media de los tokens + capa densa (como sentence-transformers)."""
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        with self._lock:
            encs = self._tok.encode_batch(texts)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        (hidden,) = self._text.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": mask})
        m = mask[:, :, None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
        return _normalize(pooled @ self._dense.T)


_model: ClipModel | None = None
_model_lock = threading.Lock()


def get_model() -> ClipModel:
    """El modelo cargado (se carga la primera vez). Lanza ModelNotInstalledError."""
    global _model
    with _model_lock:
        if _model is None:
            _model = ClipModel()
        return _model


def unload() -> None:
    global _model
    with _model_lock:
        _model = None
