"""Episode script -> storyboard.json (shot list + narration with audio tags).

Two routes:
  * Structured script (every shot has `visual:` and `narration:` lines): parsed
    directly, no model call, free. This is also how `--dry-run` works.
  * Loose prose script: an LLM turns it into the same structure.
Either way the result is validated by code before anything downstream runs.
"""
from __future__ import annotations

import json
import re

from . import timing
from .bible import Bible

FIELDS = ("visual", "location", "characters", "camera", "motion", "narration", "speaks")
TAG_OK = re.compile(r"^\[[a-zA-Z][a-zA-Z ,'-]{0,30}\]$")


class StoryboardError(ValueError):
    pass


def parse_cast(value: str) -> list[dict]:
    cast = []
    for part in [p.strip() for p in value.split(",") if p.strip()]:
        cid, _, era = part.partition("@")
        cast.append({"id": cid.strip(), "era": era.strip() or None})
    return cast


def parse_structured(script: str, episode: str) -> dict | None:
    """Parse '## Shot N' sections with 'key: value' lines. None if the script is prose."""
    title_m = re.search(r"^#\s+(.+)$", script, re.M)
    sections = re.split(r"^##\s+Shot\s+(\d+)[^\n]*$", script, flags=re.M | re.I)
    if len(sections) < 3:
        return None
    shots = []
    for num, body in zip(sections[1::2], sections[2::2]):
        shot: dict = {"id": f"{int(num):02d}"}
        current = None
        for line in body.splitlines():
            m = re.match(r"^\s*(visual|location|characters|camera|motion|narration|speaks)\s*:\s*(.*)$", line, re.I)
            if m:
                current = m.group(1).lower()
                shot[current] = m.group(2).strip()
            elif current and line.strip() and not line.lstrip().startswith("#"):
                shot[current] = (shot[current] + " " + line.strip()).strip()
        if "visual" not in shot or "narration" not in shot:
            return None
        shot["characters"] = parse_cast(shot.get("characters", ""))
        shot["speaks"] = str(shot.get("speaks", "")).lower() in ("yes", "true", "1")
        shots.append(shot)
    return {"episode": episode, "title": title_m.group(1).strip() if title_m else episode, "shots": shots}


def llm_prompts(bible: Bible, script: str, episode: str) -> tuple[str, str]:
    fmt = bible.format
    max_words = int(float(fmt["max_seconds"]) * 2.4)
    cast = "\n".join(f"- id={c['id']} ({c.get('name', c['id'])}): {c['description']}; eras: "
                     f"{[e.get('id') for e in (c.get('eras') or [])]}" for c in bible.characters)
    locs = "\n".join(f"- id={l.get('id')}: {l.get('description', '')}" for l in bible.data.get("locations") or [])
    system = (
        "You are a storyboard artist for a vertical animated micro-drama. You turn an episode script "
        "into a shot list. Reply with ONE JSON object and nothing else.")
    user = f"""Series: {bible.title}
Logline: {(bible.data.get('series') or {}).get('logline', '')}
Narrator: {(bible.data.get('series') or {}).get('narrator', '')}
Tone: {(bible.data.get('series') or {}).get('tone', '')}

Characters (use these ids exactly):
{cast}

Locations (use these ids or null):
{locs or '- none defined'}

Rules for the shot list:
- {fmt['shots_per_episode']} shots (5 to 9 is fine if the story needs it). One idea per shot; the picture changes every shot.
- The whole episode is ONE continuous narration. Total spoken words at most {max_words} so it lands under {fmt['max_seconds']} seconds.
- Each shot's narration is self-contained: it describes what is on screen or the narrator's reflection, never off-screen dialogue.
- Put 1 to 3 ElevenLabs v3 audio tags in square brackets INSIDE each line at the emotional shifts, for example "[weary] Twenty years... [quietly] gone." Tags are one or two plain words. Use ellipses for pauses. Spell out numbers.
- The last line is a door, not a summary: end on an unresolved image and a question the next episode answers.
- visual: one concrete frame. Put every prop the character touches IN the frame. Give directional action a clear left-right layout. Never ask for on-screen text.
- camera: exactly ONE named move (slow push-in, locked-off, slow pan left, crane up, handheld follow) with start and end framing.
- motion: what moves during the clip, calm and specific.
- speaks: false unless a character visibly talks on camera (rare).
{chr(10).join('- ' + r for r in bible.rules('story'))}

JSON shape:
{{"episode": "{episode}", "title": "...", "shots": [{{"id": "01", "visual": "...", "location": "<location id or null>",
"characters": [{{"id": "<character id>", "era": "<era id or null>"}}], "camera": "...", "motion": "...",
"narration": "...", "speaks": false}}]}}

Episode script:
{script}
"""
    return system, user


def validate(board: dict, bible: Bible) -> list[str]:
    """Return a list of problems. Empty list means the storyboard is usable."""
    problems = []
    shots = board.get("shots") or []
    if not shots:
        return ["storyboard has no shots"]
    ids = [s.get("id") for s in shots]
    if len(set(ids)) != len(ids):
        problems.append("shot ids are not unique")
    known_chars = {c["id"] for c in bible.characters}
    known_locs = {l.get("id") for l in bible.data.get("locations") or []}
    for s in shots:
        sid = s.get("id")
        if not s.get("visual"):
            problems.append(f"shot {sid}: no visual")
        if not timing.strip_tags(s.get("narration", "")):
            problems.append(f"shot {sid}: no narration")
        for tag in re.findall(r"\[[^\]]*\]", s.get("narration", "")):
            if not TAG_OK.match(tag):
                problems.append(f"shot {sid}: odd audio tag {tag}")
        if re.search(r"\d", timing.strip_tags(s.get("narration", ""))):
            problems.append(f"shot {sid}: spell out numbers in narration")
        for cast in s.get("characters") or []:
            if cast.get("id") not in known_chars:
                problems.append(f"shot {sid}: unknown character {cast.get('id')!r}")
            else:
                try:
                    bible.era(cast["id"], cast.get("era"))
                except ValueError as e:
                    problems.append(f"shot {sid}: {e}")
        if s.get("location") and known_locs and s["location"] not in known_locs:
            problems.append(f"shot {sid}: unknown location {s['location']!r}")
    est = timing.estimate_seconds([s.get("narration", "") for s in shots])
    if est > float(bible.format["max_seconds"]):
        problems.append(f"narration estimates {est:.0f}s, over the {bible.format['max_seconds']}s cap; trim words")
    return problems


def normalise(board: dict) -> dict:
    for i, s in enumerate(board.get("shots") or [], 1):
        s["id"] = f"{int(s.get('id') or i):02d}" if str(s.get("id", i)).isdigit() else str(s["id"])
        if s.get("location") in ("null", ""):
            s["location"] = None
        cast = s.get("characters") or []
        s["characters"] = [c if isinstance(c, dict) else {"id": str(c), "era": None} for c in cast]
        for c in s["characters"]:
            if c.get("era") in ("null", ""):
                c["era"] = None
        s["speaks"] = bool(s.get("speaks"))
    return board


def dumps(board: dict) -> str:
    return json.dumps(board, indent=2, ensure_ascii=False) + "\n"
