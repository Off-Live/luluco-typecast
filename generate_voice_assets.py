#!/usr/bin/env python3
"""
Generate offline TTS voice assets from content_sets.yaml using Typecast.

Inputs:
- content_sets.yaml (saved by you from the earlier spec)
- config.py (Typecast defaults + client factory)

Outputs:
- A folder tree with per-clip audio files (wav/mp3 per config.DEFAULTS.audio_format)

Refs (Typecast docs):
- Python SDK quickstart uses Typecast client + TTSRequest + Output, saving response.audio_data.
- REST endpoint is POST https://api.typecast.ai/v1/text-to-speech with X-API-KEY header.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Local config (generated earlier)
import config

try:
    import yaml  # PyYAML
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "PyYAML is required. Install: pip install pyyaml\n"
        f"Import error: {e}"
    )

from typecast.models import TTSRequest, Output, Prompt  # type: ignore


# ----------------------------
# Helpers
# ----------------------------

def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)

def iter_items(section: Any) -> Iterable[Tuple[str, str]]:
    """
    Returns (id, text) pairs from either:
    - list of {id, text}
    - dict-like {key: ...} (not expected, but tolerated)
    """
    if section is None:
        return []
    if isinstance(section, list):
        out = []
        for it in section:
            if not isinstance(it, dict) or "id" not in it or "text" not in it:
                raise ValueError(f"Invalid item: {it}")
            out.append((str(it["id"]), str(it["text"])))
        return out
    raise ValueError(f"Unsupported YAML section type: {type(section)}")

def tts_one(cli, *, text: str, voice_id: str, model: str, language: Optional[str],
            emotion_preset: str, emotion_intensity: float,
            volume: int, audio_pitch: int, audio_tempo: float, audio_format: str,
            seed: Optional[int]) -> bytes:
    req = TTSRequest(
        text=text,
        model=model,
        voice_id=voice_id,
        output=Output(
            audio_format=audio_format,
            volume=volume,
            audio_pitch=audio_pitch,
            audio_tempo=audio_tempo,
        ),
        prompt=Prompt(
            emotion_preset=emotion_preset,
            emotion_intensity=emotion_intensity,
        ),
        language=language,
        seed=seed,
    )
    resp = cli.text_to_speech(req)
    return resp.audio_data


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Typecast TTS assets from content_sets.yaml")
    ap.add_argument("--yaml", dest="yaml_path", default="content_sets.yaml",
                    help="Path to content_sets.yaml (default: ./content_sets.yaml)")
    ap.add_argument("--out", dest="out_dir", default="voice_assets",
                    help="Output directory (default: ./voice_assets)")
    ap.add_argument("--force", action="store_true",
                    help="Regenerate even if file exists")
    ap.add_argument("--sleep", type=float, default=0.25,
                    help="Sleep seconds between requests (default: 0.25)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print plan but do not call TTS")
    ap.add_argument("--only", nargs="*", default=None,
                    help="Only generate these groups (e.g. prefixes colors micro completion_not_enough)")
    args = ap.parse_args()

    yaml_path = Path(args.yaml_path).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not yaml_path.exists():
        print(f"[ERR] YAML not found: {yaml_path}", file=sys.stderr)
        return 2

    data: Dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    root = data.get("lulu_reaction_voice") or {}
    locale = root.get("locale", "en-US")
    version = root.get("version", "unknown")
    print(f"[INFO] Loaded {yaml_path.name} (version={version}, locale={locale})")

    # Defaults from config.py (you can edit config.DEFAULTS)
    d = config.DEFAULTS

    # Build generation plan: list of (group_key, subdir, id, text)
    plan: List[Tuple[str, Path, str, str]] = []

    def add_group(group_key: str, subdir: str, items: Iterable[Tuple[str, str]]):
        if args.only and group_key not in set(args.only):
            return
        for clip_id, text in items:
            plan.append((group_key, out_dir / subdir, clip_id, text))

    add_group("prefixes", "prefix", iter_items(root.get("prefixes")))
    add_group("suffixes", "suffix", iter_items(root.get("suffixes")))
    vars_section = root.get("variables") or {}
    add_group("colors", "color", iter_items((vars_section.get("colors"))))
    add_group("tools", "tool", iter_items((vars_section.get("tools"))))
    add_group("micro", "micro", iter_items(root.get("micro_reactions")))

    completion = root.get("completion") or {}
    add_group("completion_not_enough", "completion/not_enough", iter_items(completion.get("not_enough")))
    add_group("completion_enough", "completion/enough", iter_items(completion.get("enough")))

    if not plan:
        print("[WARN] No items to generate (check --only filters).")
        return 0

    # Determine extension
    ext = "." + (d.audio_format.lower().strip("."))
    print(f"[INFO] Output format: {d.audio_format}  (ext={ext})")
    print(f"[INFO] Output dir: {out_dir}")

    # Prepare dirs
    for _, subdir, _, _ in plan:
        ensure_dir(subdir)

    # Init client (requires TYPECAST_API_KEY env)
    cli = None
    if not args.dry_run:
        cli = config.get_typecast_client()

    # Generate
    generated = 0
    skipped = 0
    failed = 0

    for idx, (group_key, subdir, clip_id, text) in enumerate(plan, start=1):
        out_path = subdir / f"{clip_id}{ext}"

        if out_path.exists() and not args.force:
            skipped += 1
            continue

        # Safety: avoid accidental empty strings
        if not text or not text.strip():
            print(f"[WARN] Empty text for {group_key}:{clip_id} — skipping")
            skipped += 1
            continue

        print(f"[{idx:04d}/{len(plan):04d}] {group_key}:{clip_id} -> {out_path.name}")

        if args.dry_run:
            generated += 1
            continue

        try:
            audio = tts_one(
                cli,
                text=text,
                voice_id=d.voice_id,
                model=d.model,
                language=d.language,
                emotion_preset=d.emotion_preset,
                emotion_intensity=d.emotion_intensity,
                volume=d.volume,
                audio_pitch=d.audio_pitch,
                audio_tempo=d.audio_tempo,
                audio_format=d.audio_format,
                seed=d.seed,
            )
            write_bytes_atomic(out_path, audio)
            generated += 1
        except Exception as e:
            failed += 1
            print(f"[ERR] Failed {group_key}:{clip_id}: {e}", file=sys.stderr)

        if args.sleep > 0:
            time.sleep(args.sleep)

    print("\n[SUMMARY]")
    print(f"  generated: {generated}")
    print(f"  skipped:   {skipped}")
    print(f"  failed:    {failed}")
    print(f"  out_dir:   {out_dir}")

    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
