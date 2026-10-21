"""ElevenLabs narration: one continuous eleven_v3 take per episode.

Uses the documented text-to-speech "with-timestamps" endpoint so we get character
timings for free alongside the audio. If that endpoint does not return usable
timings, the caller falls back to silence detection on the audio.
"""
from __future__ import annotations

import base64
from pathlib import Path

from . import env, http

BASE = "https://api.elevenlabs.io/v1"


def synthesize(text: str, voice: dict, out_mp3: Path) -> dict | None:
    """Write the take to out_mp3. Returns the alignment dict (or None)."""
    key = env.require("ELEVENLABS_API_KEY", "Get one at https://elevenlabs.io (Profile, API keys).")
    voice_id = voice.get("voice_id")
    if not voice_id or voice_id.startswith("REPLACE_"):
        raise SystemExit("voice.voice_id in show-bible.yaml is not set. Pick a voice in ElevenLabs "
                         "(Voices, copy the voice ID) and paste it into the bible.")
    body = {
        "text": text,
        "model_id": voice.get("model_id", "eleven_v3"),
        "voice_settings": {k: voice[k] for k in ("stability", "similarity_boost", "style", "use_speaker_boost")
                           if k in voice},
    }
    headers = {"xi-api-key": key, "Accept": "application/json"}
    resp = http.request("POST", f"{BASE}/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128",
                        headers=headers, body=body, paid=True, timeout=600)
    audio = resp.get("audio_base64") if isinstance(resp, dict) else None
    if not audio:
        raise RuntimeError("ElevenLabs returned no audio_base64")
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    out_mp3.write_bytes(base64.b64decode(audio))
    return resp.get("alignment") or resp.get("normalized_alignment")
