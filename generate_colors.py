#!/usr/bin/env python3
"""
Generate TTS assets from colors.yaml utterances.

Why:
- Extract long and short utterances from colors.yaml
- Generate voice assets for color reactions
- Supports sampling for testing (N long, N short)

Usage examples:
  export TYPECAST_API_KEY="..."
  python generate_colors.py --yaml colors.yaml --out voice_assets_colors

  # Generate only 5 long and 3 short utterances
  python generate_colors.py --n-long 5 --n-short 3

  # Generate all utterances
  python generate_colors.py --all

  # Deterministic sampling
  python generate_colors.py --seed 123

  # Force overwrite
  python generate_colors.py --force
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import config

try:
    import yaml  # PyYAML
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "PyYAML is required. Install: pip install pyyaml\n"
        f"Import error: {e}"
    )

from typecast.models import TTSRequest, Output, Prompt  # type: ignore


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def iter_utterances(section: Any) -> List[Tuple[str, str]]:
    """Extract utterances from YAML section."""
    if section is None:
        return []
    if isinstance(section, list):
        out: List[Tuple[str, str]] = []
        for it in section:
            if not isinstance(it, dict) or "id" not in it or "text" not in it:
                raise ValueError(f"Invalid utterance item: {it}")
            out.append((str(it["id"]), str(it["text"])))
        return out
    raise ValueError(f"Unsupported YAML section type: {type(section)}")


def tts_one(cli, *, text: str, d: config.TypecastTTSDefaults) -> bytes:
    req = TTSRequest(
        text=text,
        model=d.model,
        voice_id=d.voice_id,
        output=Output(
            audio_format=d.audio_format,
            volume=d.volume,
            audio_pitch=d.audio_pitch,
            audio_tempo=d.audio_tempo,
        ),
        prompt=Prompt(
            emotion_preset=d.emotion_preset,
            emotion_intensity=d.emotion_intensity,
        ),
        language=d.language,
        seed=d.seed,
    )
    resp = cli.text_to_speech(req)
    return resp.audio_data


def sample(items: List[Tuple[str, str]], n: int, rng: random.Random) -> List[Tuple[str, str]]:
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return items
    return rng.sample(items, n)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Typecast TTS assets from colors.yaml")
    ap.add_argument("--yaml", dest="yaml_path", default="colors.yaml")
    ap.add_argument("--out", dest="out_dir", default="voice_assets_colors")
    ap.add_argument("--seed", type=int, default=42)

    ap.add_argument("--n-long", type=int, default=10, help="Number of long utterances to generate")
    ap.add_argument("--n-short", type=int, default=10, help="Number of short utterances to generate")
    ap.add_argument("--all", action="store_true", help="Generate all utterances (ignore n-long/n-short)")

    ap.add_argument("--sleep", type=float, default=0.25, help="Sleep between API calls")
    ap.add_argument("--force", action="store_true", help="Overwrite existing files")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without generating")
    args = ap.parse_args()

    yaml_path = Path(args.yaml_path).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not yaml_path.exists():
        print(f"[ERR] YAML not found: {yaml_path}", file=sys.stderr)
        return 2

    data: Dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    root = data.get("lulu_reaction_voice") or {}
    utterances = root.get("utterances") or {}

    rng = random.Random(args.seed)

    # Extract long and short utterances
    long_utterances = iter_utterances(utterances.get("long_by_color"))
    short_utterances = iter_utterances(utterances.get("short_by_color"))

    print(f"[INFO] Found {len(long_utterances)} long utterances, {len(short_utterances)} short utterances")

    # Sample or use all
    if args.all:
        selected_long = long_utterances
        selected_short = short_utterances
    else:
        selected_long = sample(long_utterances, args.n_long, rng)
        selected_short = sample(short_utterances, args.n_short, rng)

    d = config.DEFAULTS
    ext = "." + d.audio_format.lower().strip(".")

    # Plan: (subdir, id, text)
    plan: List[Tuple[Path, str, str]] = []
    for clip_id, text in selected_long:
        plan.append((out_dir / "long", clip_id, text))
    for clip_id, text in selected_short:
        plan.append((out_dir / "short", clip_id, text))

    if not plan:
        print("[WARN] No utterances selected.")
        return 0

    # Create dirs
    for subdir, _, _ in plan:
        ensure_dir(subdir)

    # Init client
    cli = None
    if not args.dry_run:
        cli = config.get_typecast_client()

    print(f"[INFO] Generating color utterances (seed={args.seed})")
    print(f"[INFO] Output: {out_dir}  format={d.audio_format}")
    print(f"[INFO] Selected: {len(selected_long)} long + {len(selected_short)} short")

    generated = 0
    skipped = 0
    failed = 0

    for i, (subdir, clip_id, text) in enumerate(plan, start=1):
        out_path = subdir / f"{clip_id}{ext}"
        if out_path.exists() and not args.force:
            skipped += 1
            continue

        print(f"[{i:03d}/{len(plan):03d}] {out_path.relative_to(out_dir)}  <-  {text}")

        if args.dry_run:
            generated += 1
            continue

        try:
            audio = tts_one(cli, text=text, d=d)
            write_bytes_atomic(out_path, audio)
            generated += 1
        except Exception as e:
            failed += 1
            print(f"[ERR] Failed {clip_id}: {e}", file=sys.stderr)

        if args.sleep > 0:
            time.sleep(args.sleep)

    print("\n[SUMMARY]")
    print(f"  generated: {generated}")
    print(f"  skipped:   {skipped}")
    print(f"  failed:    {failed}")
    print(f"  out_dir:   {out_dir}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())