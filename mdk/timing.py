"""Shot timing math. Pure functions, all unit-tested.

The narration is ONE continuous take. Each shot's on-screen time is the slice of that
take its narration occupies. We get the slices from ElevenLabs character timestamps
when available, otherwise from real silences in the audio (never from a guess based on
character counts alone).
"""
from __future__ import annotations

import re

TAG_RE = re.compile(r"\[[^\]]*\]")


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", text)).strip()


def spoken_words(text: str) -> int:
    return len(strip_tags(text).split())


def estimate_seconds(lines: list[str], words_per_second: float = 2.6) -> float:
    return round(sum(spoken_words(x) for x in lines) / words_per_second, 2)


def join_narration(lines: list[str]) -> tuple[str, list[int]]:
    """Join shot lines with single spaces. Returns the text and each line's start offset."""
    offsets, parts, pos = [], [], 0
    for i, line in enumerate(lines):
        line = line.strip()
        if i:
            pos += 1
        offsets.append(pos)
        parts.append(line)
        pos += len(line)
    return " ".join(parts), offsets


def apply_pronunciations(text: str, mapping: dict | None) -> str:
    """Respell words for the voice model only (captions keep the real spelling)."""
    for written, spoken in (mapping or {}).items():
        text = re.sub(rf"\b{re.escape(written)}\b", spoken, text)
    return text


def blocks_from_alignment(alignment: dict, lines: list[str], total: float) -> list[float] | None:
    """Per-shot durations from ElevenLabs character timestamps.

    Returns None if the alignment does not line up with the text we sent, so the
    caller falls back to silence detection.
    """
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    text, offsets = join_narration(lines)
    if not chars or len(chars) != len(text) or len(starts) != len(chars) or len(ends) != len(chars):
        return None
    if "".join(chars) != text:
        return None
    cuts = [0.0]
    for i in range(1, len(lines)):
        prev_end_idx = offsets[i] - 2  # last char of the previous line (before the joining space)
        this_start_idx = offsets[i]
        cut = (ends[prev_end_idx] + starts[this_start_idx]) / 2
        cuts.append(max(cut, cuts[-1]))
    cuts.append(total)
    return [round(b - a, 3) for a, b in zip(cuts, cuts[1:])]


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")


def parse_silencedetect(stderr: str) -> list[tuple[float, float]]:
    out, start = [], None
    for line in stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    return out


def blocks_from_silences(silences: list[tuple[float, float]], lines: list[str], total: float,
                         tolerance: float = 2.5) -> list[float]:
    """Split the take at real pauses.

    Word counts give a rough expected position for each boundary; the cut snaps to
    the midpoint of the nearest silence within `tolerance` seconds. Cuts stay in order.
    """
    weights = [max(1, spoken_words(x)) for x in lines]
    total_w = sum(weights)
    mids = sorted((a + b) / 2 for a, b in silences if 0 < (a + b) / 2 < total)
    cuts, acc, used = [0.0], 0, set()
    for w in weights[:-1]:
        acc += w
        expected = acc / total_w * total
        best = None
        for idx, mid in enumerate(mids):
            if idx in used or mid <= cuts[-1]:
                continue
            if abs(mid - expected) <= tolerance and (best is None or abs(mid - expected) < abs(mids[best] - expected)):
                best = idx
        if best is not None:
            used.add(best)
            cuts.append(mids[best])
        else:
            cuts.append(max(expected, cuts[-1]))
    cuts.append(total)
    return [round(b - a, 3) for a, b in zip(cuts, cuts[1:])]


def choose_clip_duration(target: float, supported: list[int], max_slowdown: float = 1.25) -> int:
    """Cheapest supported clip length that covers `target` seconds.

    A clip may be slowed by up to `max_slowdown` to fill its slot (slowing reads as
    deliberate; freezing the last frame reads as broken). Longer clips get trimmed.
    """
    options = sorted(supported)
    for d in options:
        if d * max_slowdown >= target:
            return d
    return options[-1]


def fit_factor(target: float, clip_seconds: float) -> float:
    """setpts multiplier: >1 slows the clip to fill the slot, 1.0 means trim only."""
    if clip_seconds <= 0:
        raise ValueError("clip has no duration")
    return round(max(1.0, target / clip_seconds), 5)


def caption_chunks(lines: list[str], durations: list[float], max_words: int = 4) -> list[tuple[float, float, str]]:
    """Short caption cues, timed by word share inside each shot's slot."""
    cues, t0 = [], 0.0
    for line, dur in zip(lines, durations):
        words = strip_tags(line).split()
        if not words:
            t0 += dur
            continue
        groups = [words[i:i + max_words] for i in range(0, len(words), max_words)]
        start = t0
        for g in groups:
            span = dur * len(g) / len(words)
            cues.append((round(start, 3), round(start + span, 3), " ".join(g)))
            start += span
        t0 += dur
    return cues


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[tuple[float, float, str]]) -> str:
    return "".join(f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{text}\n\n"
                   for i, (a, b, text) in enumerate(cues, 1))
