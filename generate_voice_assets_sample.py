#!/usr/bin/env python3
"""
Generate a small experimental subset of TTS assets from content_sets.yaml.

Why:
- Full generation can be 100+ clips.
- This script lets you quickly test voice quality, pacing, and file output.

Usage examples:
  export TYPECAST_API_KEY="..."
  python generate_voice_assets_sample.py --yaml content_sets.yaml --out voice_assets_sample

  # Generate only 3 prefixes, 5 colors, 3 micro reactions
  python generate_voice_assets_sample.py --n-prefix 3 --n-color 5 --n-micro 3

  # Deterministic sampling
  python generate_voice_assets_sample.py --seed 123

  # Force overwrite
  python generate_voice_assets_sample.py --force
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

def iter_items(section: Any) -> List[Tuple[str, str]]:
    if section is None:
        return []
    if isinstance(section, list):
        out: List[Tuple[str, str]] = []
        for it in section:
            if not isinstance(it, dict) or "id" not in it or "text" not in it:
                raise ValueError(f"Invalid item: {it}")
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
    ap = argparse.ArgumentParser(description="Generate a small subset of Typecast TTS assets")
    ap.add_argument("--yaml", dest="yaml_path", default="content_sets.yaml")
    ap.add_argument("--out", dest="out_dir", default="voice_assets_sample")
    ap.add_argument("--seed", type=int, default=42)

    ap.add_argument("--n-prefix", type=int, default=5)
    ap.add_argument("--n-suffix", type=int, default=3)
    ap.add_argument("--n-color", type=int, default=10)
    ap.add_argument("--n-tool", type=int, default=5)
    ap.add_argument("--n-micro", type=int, default=5)
    ap.add_argument("--n-completion-not-enough", type=int, default=3)
    ap.add_argument("--n-completion-enough", type=int, default=3)

    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    yaml_path = Path(args.yaml_path).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if not yaml_path.exists():
        print(f"[ERR] YAML not found: {yaml_path}", file=sys.stderr)
        return 2

    data: Dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    root = data.get("lulu_reaction_voice") or {}
    vars_section = root.get("variables") or {}
    completion = root.get("completion") or {}

    rng = random.Random(args.seed)

    prefixes = sample(iter_items(root.get("prefixes")), args.n_prefix, rng)
    suffixes = sample(iter_items(root.get("suffixes")), args.n_suffix, rng)
    colors = sample(iter_items(vars_section.get("colors")), args.n_color, rng)
    tools = sample(iter_items(vars_section.get("tools")), args.n_tool, rng)
    micros = sample(iter_items(root.get("micro_reactions")), args.n_micro, rng)
    comp_ne = sample(iter_items(completion.get("not_enough")), args.n_completion_not_enough, rng)
    comp_ok = sample(iter_items(completion.get("enough")), args.n_completion_enough, rng)

    d = config.DEFAULTS
    ext = "." + d.audio_format.lower().strip(".")

    # Plan: (subdir, id, text)
    plan: List[Tuple[Path, str, str]] = []
    for clip_id, text in prefixes:
        plan.append((out_dir / "prefix", clip_id, text))
    for clip_id, text in suffixes:
        plan.append((out_dir / "suffix", clip_id, text))
    for clip_id, text in colors:
        plan.append((out_dir / "color", clip_id, text))
    for clip_id, text in tools:
        plan.append((out_dir / "tool", clip_id, text))
    for clip_id, text in micros:
        plan.append((out_dir / "micro", clip_id, text))
    for clip_id, text in comp_ne:
        plan.append((out_dir / "completion" / "not_enough", clip_id, text))
    for clip_id, text in comp_ok:
        plan.append((out_dir / "completion" / "enough", clip_id, text))

    if not plan:
        print("[WARN] No items selected.")
        return 0

    # Create dirs
    for subdir, _, _ in plan:
        ensure_dir(subdir)

    # Init client
    cli = None
    if not args.dry_run:
        cli = config.get_typecast_client()

    print(f"[INFO] Generating sample assets (seed={args.seed})")
    print(f"[INFO] Output: {out_dir}  format={d.audio_format}")

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
