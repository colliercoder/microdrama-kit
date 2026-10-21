"""The show bible: the single source of creative truth every step reads.

Consistency across a season comes from this file, never from per-episode prompting.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import yamlish

BIBLE_FILE = "show-bible.yaml"
LEARNED_FILE = "learned-rules.yaml"   # written by `mdk learn`, merged into rules at load
STAGES = ("image", "video", "voice", "story")

PRODUCTION_DEFAULTS = {
    "backend": "openrouter",               # openrouter | higgsfield (experimental)
    "text_model": "google/gemini-2.5-flash",
    "image_model": "google/gemini-2.5-flash-image",
    "video_model": "google/veo-3.1-lite",
    "video_resolution": "720p",
    "higgsfield_image_model": "nano_banana_pro",
    "higgsfield_video_model": "veo3_1_lite",
    "max_rerolls": 2,
    "max_slowdown": 1.25,
    "elevenlabs_usd_per_1k_chars": 0.30,
    "output_width": 1080,
    "output_height": 1920,
}

VOICE_DEFAULTS = {
    "model_id": "eleven_v3",
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.5,
    "use_speaker_boost": True,
}

FORMAT_DEFAULTS = {"aspect_ratio": "9:16", "max_seconds": 59, "shots_per_episode": 7}

PLACEHOLDER_VOICE = "REPLACE_WITH_YOUR_ELEVENLABS_VOICE_ID"


class BibleError(ValueError):
    pass


@dataclass
class Bible:
    data: dict
    root: Path

    # -- loading ---------------------------------------------------------------
    @classmethod
    def load(cls, series_dir: Path) -> "Bible":
        path = series_dir / BIBLE_FILE
        if not path.is_file():
            raise BibleError(f"no {BIBLE_FILE} in {series_dir}. Run `python3 -m mdk init {series_dir}` first.")
        bible = cls.from_text(path.read_text(), series_dir)
        learned = series_dir / LEARNED_FILE
        if learned.is_file():
            extra = yamlish.loads(learned.read_text()) or {}
            rules = bible.data.setdefault("rules", {}) or {}
            bible.data["rules"] = rules
            for stage, items in extra.items():
                merged = list(rules.get(stage) or [])
                merged += [r for r in (items or []) if r not in merged]
                rules[stage] = merged
        return bible

    @classmethod
    def from_text(cls, text: str, root: Path) -> "Bible":
        data = yamlish.loads(text)
        if not isinstance(data, dict):
            raise BibleError("show bible must be a mapping at the top level")
        bible = cls(data, root)
        bible.validate()
        return bible

    def validate(self) -> None:
        problems = []
        series = self.data.get("series") or {}
        if not series.get("title"):
            problems.append("series.title is missing")
        chars = self.data.get("characters")
        if not isinstance(chars, list) or not chars:
            problems.append("characters must be a non-empty list")
        else:
            seen = set()
            for i, c in enumerate(chars):
                if not isinstance(c, dict) or not c.get("id"):
                    problems.append(f"characters[{i}] needs an id")
                    continue
                if c["id"] in seen:
                    problems.append(f"duplicate character id {c['id']!r}")
                seen.add(c["id"])
                if not c.get("description"):
                    problems.append(f"character {c['id']!r} needs a description")
                for j, era in enumerate(c.get("eras") or []):
                    if not isinstance(era, dict) or not era.get("id"):
                        problems.append(f"character {c['id']!r} eras[{j}] needs an id")
        fmt = self.format
        if fmt["aspect_ratio"] != "9:16":
            problems.append("format.aspect_ratio must be 9:16 for vertical shorts")
        if not (10 <= float(fmt["max_seconds"]) <= 180):
            problems.append("format.max_seconds should be between 10 and 180")
        if problems:
            raise BibleError("show bible problems:\n  - " + "\n  - ".join(problems))

    # -- accessors -------------------------------------------------------------
    @property
    def title(self) -> str:
        return self.data["series"]["title"]

    @property
    def format(self) -> dict:
        return {**FORMAT_DEFAULTS, **((self.data.get("series") or {}).get("format") or {})}

    @property
    def production(self) -> dict:
        return {**PRODUCTION_DEFAULTS, **(self.data.get("production") or {})}

    @property
    def voice(self) -> dict:
        return {**VOICE_DEFAULTS, **(self.data.get("voice") or {})}

    @property
    def characters(self) -> list[dict]:
        return self.data.get("characters") or []

    def character(self, cid: str) -> dict:
        for c in self.characters:
            if c["id"] == cid:
                return c
        raise BibleError(f"unknown character {cid!r}; known: {[c['id'] for c in self.characters]}")

    def era(self, cid: str, era_id: str | None) -> dict | None:
        eras = self.character(cid).get("eras") or []
        if not era_id:
            return eras[0] if eras else None
        for e in eras:
            if e.get("id") == era_id:
                return e
        raise BibleError(f"character {cid!r} has no era {era_id!r}")

    def ref_path(self, cid: str, era_id: str | None) -> Path:
        """Where a character's reference sheet lives (user-supplied or generated by `refs`)."""
        era = self.era(cid, era_id)
        if era and era.get("ref"):
            return self.root / era["ref"]
        suffix = f"-{era['id']}" if era else ""
        return self.root / "refs" / f"{cid}{suffix}.png"

    def location(self, lid: str | None) -> dict | None:
        for loc in self.data.get("locations") or []:
            if loc.get("id") == lid:
                return loc
        return None

    def negatives(self, stage: str) -> list[str]:
        """Things that must never appear. Same list for stills and clips: video models
        honor negatives less, so they are repeated in the motion prompt on purpose."""
        style = self.data.get("style") or {}
        return [str(n).rstrip(". ") for n in (style.get("global_negative") or [])]

    def rules(self, stage: str) -> list[str]:
        return list(((self.data.get("rules") or {}).get(stage)) or [])

    # -- prompt building -------------------------------------------------------
    def character_lock(self, cid: str, era_id: str | None) -> str:
        c = self.character(cid)
        era = self.era(cid, era_id)
        parts = [f"{c.get('name', cid)}: {str(c['description']).rstrip('. ')}."]
        if c.get("constant_traits"):
            parts.append(f"Always: {str(c['constant_traits']).rstrip('. ')}.")
        if era and era.get("look"):
            parts.append(f"In this era: {str(era['look']).rstrip('. ')}.")
        return " ".join(parts)

    def still_prompt(self, shot: dict) -> str:
        style = self.data.get("style") or {}
        loc = self.location(shot.get("location"))
        chunks = [
            "Vertical 9:16 cinematic film still, single frame.",
            f"Look: {style.get('look', 'cinematic')}.",
            f"Scene: {shot['visual']}",
        ]
        if loc:
            chunks.append(f"Location: {str(loc.get('description', '')).rstrip('. ')}. "
                          f"Lighting: {str(loc.get('lighting', 'as the scene implies')).rstrip('. ')}.")
        for cast in shot.get("characters") or []:
            chunks.append("Character, match the reference image exactly: " +
                          self.character_lock(cast["id"], cast.get("era")))
        rules = self.rules("image")
        if rules:
            chunks.append("Rules: " + " ".join(rules))
        chunks.append("Avoid: " + "; ".join(self.negatives("image")) + ".")
        return " ".join(chunks)

    def motion_prompt(self, shot: dict) -> str:
        loc = self.location(shot.get("location"))
        where = loc.get("description") if loc else "the same place"
        chunks = [
            f"Camera: {shot.get('camera', 'slow push-in')}.",
            f"Action: {shot.get('motion', 'subtle natural movement')}.",
            f"Stays in {where} from start to finish, no location change, no cut.",
        ]
        for cast in shot.get("characters") or []:
            chunks.append(f"Keep {self.character(cast['id']).get('name', cast['id'])} exactly on model: "
                          f"{self.character(cast['id'])['description']}.")
        if not shot.get("speaks"):
            chunks.append("Nobody talks, mouths stay closed, no lip movement.")
        rules = self.rules("video")
        if rules:
            chunks.append("Rules: " + " ".join(rules))
        chunks.append("Avoid: " + "; ".join(self.negatives("video")) + ".")
        return " ".join(chunks)

    def review_checklist(self) -> list[str]:
        checks = [
            "Every character matches its reference image (species, face, markings, outfit).",
            "No text, letters, captions, logos or watermarks anywhere in the frame.",
            "Correct anatomy: no extra or missing limbs, no fused or duplicated bodies.",
            "The image matches the shot's intended visual description.",
            "Vertical 9:16 composition with the subject clearly framed.",
        ]
        checks += [f"Does not show: {n}" for n in self.negatives("image")]
        checks += self.rules("image")
        return checks

    # -- writing ---------------------------------------------------------------
    def add_rule(self, stage: str, rule: str) -> Path:
        """Append a learned rule without rewriting (and losing comments in) the bible."""
        if stage not in STAGES:
            raise BibleError(f"stage must be one of {STAGES}")
        path = self.root / LEARNED_FILE
        data = (yamlish.loads(path.read_text()) if path.is_file() else {}) or {}
        bucket = list(data.get(stage) or [])
        if rule not in bucket:
            bucket.append(rule)
        data[stage] = bucket
        path.write_text("# Rules learned from fixing real output. Every prompt and review reads these.\n"
                        + yamlish.dumps(data) + "\n")
        return path
