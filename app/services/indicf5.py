"""
Local IndicF5 text-to-speech on the GPU.

IndicF5 is a voice-cloning model: it speaks new text in the voice of a short
reference clip. Each persona speaker gets one reference clip under
data/voices/<speaker>.wav (+ .txt with its exact transcript). Drop your own
recording there to change the voice; if none exists, one is created once with
Sarvam so the local model has a natural Indian voice to clone.

The HF `AutoModel` wrapper is bypassed on purpose: it relies on torch.compile
(needs Triton, unavailable on Windows) and pydub/ffmpeg. f5_tts is used directly.
"""

import os
import threading
import time
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

REPO = "ai4bharat/IndicF5"
SAMPLE_RATE = 24000
VOICES_DIR = Path("data/voices")
NFE_STEPS = int(os.environ.get("INDICF5_NFE_STEPS", "16"))  # 32 = best quality, 16 = ~2x faster

REF_TEXT = {
    "hi-IN": "नमस्ते, मैं आपकी कैसे मदद कर सकता हूँ? आज का दिन बहुत अच्छा है, और मैं आपसे बात करके खुश हूँ।",
    "en-IN": "Hello, how can I help you today? It is a lovely day, and I am happy to speak with you.",
}

_lock = threading.Lock()
_model = None
_vocoder = None
_device = "cpu"
_refs: dict[str, tuple] = {}


class IndicF5Unavailable(RuntimeError):
    pass


def _load():
    global _model, _vocoder, _device
    if _model is not None:
        return
    with _lock:
        if _model is not None:
            return
        try:
            import torch
            from f5_tts.infer.utils_infer import load_model, load_vocoder
            from f5_tts.model import DiT
            from huggingface_hub import hf_hub_download
        except Exception as e:  # noqa: BLE001 - torch DLL errors are OSError
            raise IndicF5Unavailable(f"torch/f5_tts not importable: {e}") from e

        _device = "cuda" if torch.cuda.is_available() else "cpu"
        if _device == "cpu":
            log.warning("IndicF5 running on CPU: replies will be slow")
        token = os.environ.get("HF_TOKEN")
        t0 = time.time()
        ckpt = hf_hub_download(REPO, "model.safetensors", token=token)
        vocab = hf_hub_download(REPO, "checkpoints/vocab.txt", token=token)
        _vocoder = load_vocoder(vocoder_name="vocos", is_local=False, device=_device)
        model = load_model(DiT, dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4),
                           ckpt_path=ckpt, mel_spec_type="vocos", vocab_file=vocab, device=_device)
        _model = model.eval()
        log.info("IndicF5 loaded on %s in %.1fs", _device, time.time() - t0)


def _reference(speaker: str, language: str) -> tuple:
    lang = "hi-IN" if language != "en-IN" else "en-IN"
    key = f"{speaker}-{lang}"
    if key in _refs:
        return _refs[key]
    from f5_tts.infer.utils_infer import preprocess_ref_audio_text

    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    wav, txt = VOICES_DIR / f"{key}.wav", VOICES_DIR / f"{key}.txt"
    if not wav.exists():
        if not settings.sarvam_api_key:
            raise IndicF5Unavailable(f"No reference voice at {wav}; add a 5-10s clip and its transcript in {txt}")
        from app.services import tts

        wav.write_bytes(tts._sarvam_synthesize(REF_TEXT[lang], lang, speaker))
        txt.write_text(REF_TEXT[lang], encoding="utf-8")
        log.info("Created reference voice %s", wav)
    ref_audio, ref_text = preprocess_ref_audio_text(str(wav), txt.read_text(encoding="utf-8").strip(), show_info=lambda *a: None)
    _refs[key] = (ref_audio, ref_text)
    return _refs[key]


def generate(text: str, language: str, speaker: str | None) -> np.ndarray:
    """Float32 mono waveform at 24 kHz."""
    import torch
    from f5_tts.infer.utils_infer import infer_process

    _load()
    ref_audio, ref_text = _reference(speaker or "anand", language)
    with _lock, torch.inference_mode():
        audio, _, _ = infer_process(ref_audio, ref_text, text, _model, _vocoder, mel_spec_type="vocos",
                                    nfe_step=NFE_STEPS, show_info=lambda *a: None, progress=None, device=_device)
    return np.asarray(audio, dtype=np.float32)


def warmup(language: str = "hi-IN", speaker: str = "anand"):
    t0 = time.time()
    generate("नमस्ते।" if language != "en-IN" else "Hello.", language, speaker)
    log.info("IndicF5 warm in %.2fs", time.time() - t0)
