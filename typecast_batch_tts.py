#!/usr/bin/env python3
"""
Batch TTS generator for Typecast (typecast-python SDK).

- Reads a YAML manifest that defines "voice sets" (many lines across multiple voices/styles).
- Generates audio files deterministically (stable filenames), so you can re-run safely.
- Supports: model, voice_id, language, emotion preset/intensity, volume/pitch/tempo, output format.

Docs:
- https://typecast.ai/docs/sdk/python
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML
except ImportError as e:
    raise SystemExit("Missing dependency: pyyaml. Install with: pip install pyyaml") from e

from typecast.client import Typecast
from typecast.models import TTSRequest, Output, Prompt, LanguageCode


SAFE_CHARS = re.compile(r"[^a-zA-Z0-9._-]+")


def slugify(s: str, max_len: int = 80) -> str:
    s = s.strip().lower()
    s = SAFE_CHARS.sub("-", s).strip("-")
    return s[:max_len] if len(s) > max_len else s


def short_hash(s: str, n: int = 8) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def as_language_code(code: str) -> LanguageCode:
    """
    Map common BCP-47-ish codes to SDK LanguageCode enums.
    The SDK exposes LanguageCode.* (e.g., ENG, KOR, JPN).
    """
    c = code.strip().lower()
    mapping = {
        "en": LanguageCode.ENG,
        "en-us": LanguageCode.ENG,
        "en-gb": LanguageCode.ENG,
        "ko": LanguageCode.KOR,
        "ko-kr": LanguageCode.KOR,
        "ja": LanguageCode.JPN,
        "ja-jp": LanguageCode.JPN,
        "zh": LanguageCode.ZHO,
        "zh-cn": LanguageCode.ZHO,
        "es": LanguageCode.SPA,
        "fr": LanguageCode.FRA,
        "de": LanguageCode.DEU,
    }
    if c in mapping:
        return mapping[c]
    # best-effort: try direct enum name
    try:
        return getattr(LanguageCode, c.upper().replace("-", "_"))
    except Exception:
        raise ValueError(f"Unsupported/unknown language code: {code}. Please map it in as_language_code().")


@dataclass
class LineItem:
    set: str
    key: str
    text: str
    filename: str
    model: str
    voice_id: str
    language: str
    output_format: str
    prompt: Dict[str, Any]
    output: Dict[str, Any]


def load_manifest(path: Path) -> Tuple[Dict[str, Any], List[LineItem]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Manifest root must be a YAML mapping/dict.")

    defaults = data.get("defaults", {}) or {}
    sets = data.get("sets", []) or []

    items: List[LineItem] = []
    for s in sets:
        set_name = s.get("name")
        if not set_name:
            raise ValueError("Each set must have a name.")
        set_defaults = {**defaults, **(s.get("defaults") or {})}
        lines = s.get("lines", []) or []
        for ln in lines:
            key = ln.get("key")
            text = ln.get("text")
            if not key or not text:
                raise ValueError(f"Line in set '{set_name}' must include key and text.")
            # Effective params
            model = ln.get("model", set_defaults.get("model"))
            voice_id = ln.get("voice_id", set_defaults.get("voice_id"))
            language = ln.get("language", set_defaults.get("language", "en"))
            output_format = ln.get("output_format", set_defaults.get("output_format", "mp3"))

            if not model or not voice_id:
                raise ValueError(
                    f"Missing model/voice_id for set '{set_name}', line '{key}'. "
                    f"Provide in defaults or per-line."
                )

            prompt_cfg = {**(set_defaults.get("prompt") or {}), **(ln.get("prompt") or {})}
            output_cfg = {**(set_defaults.get("output") or {}), **(ln.get("output") or {})}

            # filename: use provided or derive from set/key
            if ln.get("filename"):
                filename = ln["filename"]
            else:
                base = f"{set_name}-{key}"
                filename = f"{slugify(base)}-{short_hash(text)}.{output_format}"

            items.append(
                LineItem(
                    set=set_name,
                    key=key,
                    text=text,
                    filename=filename,
                    model=model,
                    voice_id=voice_id,
                    language=language,
                    output_format=output_format,
                    prompt=prompt_cfg,
                    output=output_cfg,
                )
            )

    return data, items


def build_tts_request(item: LineItem) -> TTSRequest:
    prompt = None
    if item.prompt:
        # Typecast prompt uses emotion_preset / emotion_intensity per docs.
        # We pass through only known keys to avoid SDK errors if you add extras.
        known = {}
        if "emotion_preset" in item.prompt:
            known["emotion_preset"] = item.prompt["emotion_preset"]
        if "emotion_intensity" in item.prompt:
            known["emotion_intensity"] = float(item.prompt["emotion_intensity"])
        if known:
            prompt = Prompt(**known)

    output_kwargs: Dict[str, Any] = {"audio_format": item.output_format}
    # Optional audio controls (ranges per docs; validate lightly)
    for k in ("volume", "pitch", "tempo"):
        if k in item.output and item.output[k] is not None:
            output_kwargs[k] = item.output[k]

    return TTSRequest(
        text=item.text,
        model=item.model,
        voice_id=item.voice_id,
        language=as_language_code(item.language),
        prompt=prompt,
        output=Output(**output_kwargs),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-generate audio using Typecast Python SDK.")
    ap.add_argument("--manifest", type=str, default="voice_sets.yaml", help="Path to YAML manifest.")
    ap.add_argument("--out", type=str, default="out_audio", help="Output directory.")
    ap.add_argument("--dry-run", action="store_true", help="Print planned outputs without calling API.")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    ap.add_argument("--list-voices", action="store_true", help="List available voices (requires API key).")
    ap.add_argument("--model", type=str, default=None, help="Filter voices by model when listing (optional).")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    ensure_dir(out_dir)

    cli = Typecast()  # uses TYPECAST_API_KEY env var by default per docs

    if args.list_voices:
        # Voice discovery feature exists in docs; method name may vary by SDK version.
        # We try a few common names. If none exist, we print a helpful message.
        fn = None
        for name in ("list_voices", "voices", "get_voices"):
            if hasattr(cli, name):
                fn = getattr(cli, name)
                break
        if fn is None:
            print("This SDK version doesn't expose a voice listing helper on the client.")
            print("Check the docs 'Voice Discovery' section or use the REST endpoint GET /v1/voices.")
            return 2

        voices = fn(model=args.model) if args.model else fn()
        # Print a compact list: voice_id, model, name (if present), language (if present)
        for v in voices:
            vid = getattr(v, "voice_id", None) or getattr(v, "id", None) or ""
            model = getattr(v, "model", "") or ""
            name = getattr(v, "name", "") or ""
            lang = getattr(v, "language", "") or getattr(v, "lang", "") or ""
            print(f"{vid}\t{model}\t{name}\t{lang}")
        return 0

    _, items = load_manifest(manifest_path)

    planned = 0
    generated = 0
    skipped = 0

    for item in items:
        dest = out_dir / item.filename
        planned += 1

        if dest.exists() and not args.overwrite:
            skipped += 1
            continue

        if args.dry_run:
            print(f"[DRY] {item.set}/{item.key} -> {dest.name}")
            continue

        req = build_tts_request(item)
        resp = cli.text_to_speech(req)

        dest.write_bytes(resp.audio_data)
        generated += 1
        print(f"[OK] {item.set}/{item.key} -> {dest.name} ({getattr(resp, 'duration', '?')}s)")

    print(f"\nPlanned: {planned}, Generated: {generated}, Skipped(existing): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
