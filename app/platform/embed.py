"""CLIP ViT-B/32 image embeddings (ONNX), used to recognise a photographed artwork."""

import hashlib
import io
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from app.platform.config import get_settings

DIM = 512
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


@lru_cache
def _session():
    import onnxruntime as ort

    path = Path(get_settings().models_dir) / "clip-vision.onnx"
    if not path.exists():
        raise RuntimeError(f"{path} missing: run `uv run python -m scripts.download_model`")
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _pixels(data: bytes) -> np.ndarray:
    """CLIP preprocessing: shortest side to 224 (bicubic), centre crop, normalize, NCHW."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    scale = 224 / min(img.size)
    img = img.resize((max(224, round(img.width * scale)), max(224, round(img.height * scale))), Image.BICUBIC)
    left, top = (img.width - 224) // 2, (img.height - 224) // 2
    arr = np.asarray(img.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255.0
    return ((arr - _MEAN) / _STD).transpose(2, 0, 1)[None]


def _normalize(v: np.ndarray) -> np.ndarray:
    return (v / (np.linalg.norm(v) or 1.0)).astype(np.float32)


def embed(data: bytes) -> np.ndarray:
    """A normalized 512-float vector; cosine similarity is then a dot product."""
    if get_settings().embed_provider == "none":
        # Tests: identical bytes give identical vectors, different bytes nearly orthogonal ones.
        seed = int.from_bytes(hashlib.sha256(data).digest()[:8], "big")
        return _normalize(np.random.default_rng(seed).standard_normal(DIM))
    session = _session()
    outputs = [o.name for o in session.get_outputs()]
    name = "image_embeds" if "image_embeds" in outputs else outputs[0]
    (vec,) = session.run([name], {session.get_inputs()[0].name: _pixels(data)})
    return _normalize(vec.reshape(-1)[:DIM])
