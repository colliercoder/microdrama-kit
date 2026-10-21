"""Cost estimates. Pure functions over the free model-list responses.

OpenRouter video `pricing_skus` mix units across providers: some keys are cents per
second, some dollars per second, some dollars per video token. Every estimate here
returns (usd_or_None, basis) so the user can see which price line was used. When no
line matches we say "unknown" rather than guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 9:16 frame sizes per resolution tier (width x height).
VERTICAL_DIMS = {"480p": (480, 854), "720p": (720, 1280), "768p": (768, 1366),
                 "1080p": (1080, 1920), "2K": (1440, 2560), "4K": (2160, 3840)}
TOKENS_PER_IMAGE_OUTPUT = 1290   # Gemini image models bill ~1290 output tokens per 1K image
TOKENS_PER_IMAGE_INPUT = 258     # and ~258 input tokens per reference image
VIDEO_FPS_FOR_TOKENS = 24


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def video_clip_cost(skus: dict | None, seconds: float, resolution: str,
                    with_audio: bool = False, frame_images: int = 1) -> tuple[float | None, str]:
    """Estimated USD for one image-to-video clip."""
    skus = skus or {}
    audio = "with_audio" if with_audio else "without_audio"
    per_second_usd = [
        f"image_to_video_duration_seconds_{resolution}",
        f"duration_seconds_{audio}_{resolution}",
        f"duration_seconds_{resolution}",
        f"duration_seconds_{audio}",
        "duration_seconds",
    ]
    per_second_cents = [
        f"cents_per_video_output_second_{resolution}",
        f"cents_per_second_output_{resolution}",
        "cents_per_second_output",
    ]
    total, basis = None, ""
    for key in per_second_usd:
        if _f(skus.get(key)) is not None:
            total, basis = _f(skus[key]) * seconds, f"{key}={skus[key]} USD/s"
            break
    if total is None:
        for key in per_second_cents:
            if _f(skus.get(key)) is not None:
                total, basis = _f(skus[key]) / 100 * seconds, f"{key}={skus[key]} cents/s"
                break
    if total is None:
        for key in (f"video_tokens_{audio}", "video_tokens"):
            if _f(skus.get(key)) is not None:
                w, h = VERTICAL_DIMS.get(resolution, (720, 1280))
                tokens = w * h * VIDEO_FPS_FOR_TOKENS * seconds / 1024
                total = _f(skus[key]) * tokens
                basis = f"{key}={skus[key]} USD/token x ~{tokens:,.0f} tokens (approx)"
                break
    if total is None:
        return None, "unknown (no matching pricing line)"
    if _f(skus.get("cents_per_image_input")) is not None:
        total += _f(skus["cents_per_image_input"]) / 100 * frame_images
        basis += f" + {skus['cents_per_image_input']} cents per input image"
    minimum = _f(skus.get("minimum_cents_per_generation"))
    if minimum is not None and total < minimum / 100:
        total, basis = minimum / 100, basis + " (raised to provider minimum)"
    return round(total, 4), basis


def image_cost(endpoints: list | None, n_refs: int = 0) -> tuple[float | None, str]:
    """Estimated USD for one generated image from /images/models/{id}/endpoints records.

    Uses the most expensive endpoint so the estimate errs high.
    """
    best: tuple[float | None, str] = (None, "unknown (no pricing lines)")
    for ep in endpoints or []:
        total, parts = 0.0, []
        found_output = False
        for line in ep.get("pricing", []):
            cost = _f(line.get("cost_usd"))
            if cost is None or line.get("variant") not in (None, "", "1k", "1K"):
                continue
            billable, unit = line.get("billable"), line.get("unit")
            if billable == "output_image":
                found_output = True
                qty = {"image": 1, "token": TOKENS_PER_IMAGE_OUTPUT, "megapixel": 1.0}.get(unit, 1)
                total += cost * qty
                parts.append(f"output {cost}/{unit}")
            elif billable in ("input_image", "input_reference") and n_refs:
                qty = {"image": 1, "token": TOKENS_PER_IMAGE_INPUT, "megapixel": 1.0}.get(unit, 1)
                total += cost * qty * n_refs
                parts.append(f"{n_refs} refs at {cost}/{unit}")
        if found_output and (best[0] is None or total > best[0]):
            best = (round(total, 4), f"{ep.get('provider_slug', '?')}: " + ", ".join(parts))
    return best


def text_cost(pricing: dict | None, prompt_tokens: int, completion_tokens: int) -> tuple[float | None, str]:
    p = _f((pricing or {}).get("prompt"))
    c = _f((pricing or {}).get("completion"))
    if p is None or c is None or p < 0 or c < 0:
        return None, "unknown text pricing"
    return round(p * prompt_tokens + c * completion_tokens, 4), \
        f"~{prompt_tokens} in / {completion_tokens} out tokens"


def voice_cost(characters: int, usd_per_1k_chars: float) -> tuple[float, str]:
    return round(characters / 1000 * usd_per_1k_chars, 4), \
        f"{characters} characters at ~${usd_per_1k_chars}/1k (depends on your ElevenLabs plan)"


@dataclass
class Estimate:
    lines: list = field(default_factory=list)  # (step, description, usd or None, basis)

    def add(self, step: str, description: str, usd: float | None, basis: str) -> None:
        self.lines.append((step, description, usd, basis))

    @property
    def known_total(self) -> float:
        return round(sum(x[2] for x in self.lines if x[2] is not None), 2)

    @property
    def has_unknown(self) -> bool:
        return any(x[2] is None for x in self.lines)

    def render(self) -> str:
        if not self.lines:
            return "  (nothing to pay for: every output already exists)"
        out = []
        for step, desc, usd, basis in self.lines:
            price = f"${usd:,.2f}" if usd is not None else "unknown"
            out.append(f"  {step:<10} {desc:<44} {price:>9}   {basis}")
        total = f"${self.known_total:,.2f}"
        if self.has_unknown:
            total += " + unknown items"
        out.append(f"  {'TOTAL':<10} {'':<44} {total:>9}")
        return "\n".join(out)
