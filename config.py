"""
Typecast configuration for LuLuco TTS asset generation.

- Uses the official Typecast Python SDK (`typecast-python` -> import as `typecast`)
- Reads API key from environment variable: TYPECAST_API_KEY
- Keeps all tunables (model / voice_id / language / output params) in one place

Docs:
- SDK (Python): https://typecast.ai/docs/sdk/python
- REST API (TTS): https://typecast.ai/docs/api-reference/endpoint/text-to-speech/text-to-speech
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Dict, Any


# ----------------------------
# Environment
# ----------------------------

ENV_API_KEY = "TYPECAST_API_KEY"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}\n"
            f"Set it like: export {name}='...'\n"
            f"(Typecast docs use {ENV_API_KEY}.)"
        )
    return value


# ----------------------------
# Defaults (edit these)
# ----------------------------

@dataclass(frozen=True)
class TypecastTTSDefaults:
    # Model
    model: str = "ssfm-v21"

    # Voice (replace with your actual Lulu voice_id)
    # Example voice_id format: tc_62a8975e695ad26f7fb514d1
    voice_id: str = "tc_62fb679683a541c351dc7c3a" # Ella
    # voice_id: str = "tc_62fb678f9b93d9207fa8c032" # Leo

    # Language (ISO 639-3). If omitted, Typecast may auto-detect,
    # but for deterministic pipelines it's better to set explicitly.
    language: str = "eng"

    # Prompt controls (emotion)
    # Options per docs: normal, happy, sad, angry (voice-dependent)
    emotion_preset: str = "normal"
    # Range per SDK docs: 0.0 to 2.0
    emotion_intensity: float = 1.0

    # Output controls
    # volume range: 0 to 200
    volume: int = 100
    # audio_pitch range: -12 to +12 (semitones)
    audio_pitch: int = 0
    # audio_tempo range: 0.5 to 2.0
    audio_tempo: float = 1.0
    # audio_format: "wav" or "mp3"
    audio_format: str = "mp3"

    # Seed for reproducibility (optional)
    seed: Optional[int] = 42


DEFAULTS = TypecastTTSDefaults()


# ----------------------------
# Client factory (SDK)
# ----------------------------

def get_typecast_client(api_key: Optional[str] = None):
    """
    Returns an initialized Typecast SDK client.
    If api_key is not provided, reads TYPECAST_API_KEY from env.
    """
    from typecast.client import Typecast  # type: ignore

    key = api_key or os.getenv(ENV_API_KEY)
    if key:
        return Typecast(api_key=key)
    # SDK also supports reading from env, but we enforce it for clarity.
    return Typecast(api_key=require_env(ENV_API_KEY))


# ----------------------------
# REST API info (optional fallback)
# ----------------------------

TYPECAST_API_BASE_URL = "https://api.typecast.ai"
TYPECAST_TTS_ENDPOINT = "/v1/text-to-speech"
TYPECAST_API_KEY_HEADER = "X-API-KEY"


def tts_request_payload(
    *,
    text: str,
    voice_id: str = DEFAULTS.voice_id,
    model: str = DEFAULTS.model,
    language: Optional[str] = DEFAULTS.language,
    emotion_preset: str = DEFAULTS.emotion_preset,
    emotion_intensity: float = DEFAULTS.emotion_intensity,
    volume: int = DEFAULTS.volume,
    audio_pitch: int = DEFAULTS.audio_pitch,
    audio_tempo: float = DEFAULTS.audio_tempo,
    audio_format: str = DEFAULTS.audio_format,
    seed: Optional[int] = DEFAULTS.seed,
) -> Dict[str, Any]:
    """
    Builds a JSON-serializable payload matching the REST API schema.
    Useful if you ever bypass the SDK.
    """
    payload: Dict[str, Any] = {
        "voice_id": voice_id,
        "text": text,
        "model": model,
        "prompt": {
            "emotion_preset": emotion_preset,
            "emotion_intensity": emotion_intensity,
        },
        "output": {
            "volume": volume,
            "audio_pitch": audio_pitch,
            "audio_tempo": audio_tempo,
            "audio_format": audio_format,
        },
    }
    if language:
        payload["language"] = language
    if seed is not None:
        payload["seed"] = seed
    return payload
