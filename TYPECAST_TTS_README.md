# Typecast Batch TTS Generator

This folder contains:

- `typecast_batch_tts.py` — batch generator using **typecast-python** SDK
- `voice_sets.yaml` — a manifest listing all voice lines (voice sets) to generate

## Install

```bash
pip install typecast-python pyyaml
```

## Configure API key

```bash
export TYPECAST_API_KEY="YOUR_API_KEY"
```

(You can also pass the key directly to `Typecast(api_key="...")`, but env var is cleaner.) citeturn0search0

## Edit `voice_sets.yaml`

Set these at minimum:

- `defaults.model` (e.g., `ssfm-v21`)
- `defaults.voice_id` (a valid `voice_id` from your Typecast account)

The SDK’s quick start uses:
- `from typecast.client import Typecast`
- `cli.text_to_speech(TTSRequest(...))`
- save `response.audio_data` to a file citeturn0search0

## Run

```bash
python typecast_batch_tts.py --manifest voice_sets.yaml --out out_audio
```

Re-run safely:
- Existing files are skipped unless you pass `--overwrite`.

Dry run:

```bash
python typecast_batch_tts.py --manifest voice_sets.yaml --out out_audio --dry-run
```

## Optional: list voices

The docs mention "Voice Discovery" (listing/searching voices) citeturn0search0
Your installed SDK version may expose a helper (method name varies). Try:

```bash
python typecast_batch_tts.py --list-voices
python typecast_batch_tts.py --list-voices --model ssfm-v21
```

If your SDK does not expose a helper, use the REST `GET /v1/voices` endpoint described in the API reference. citeturn0search8
