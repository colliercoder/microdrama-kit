"""One interface for stills and clips, two providers behind it."""
from __future__ import annotations

from pathlib import Path

from . import cost, higgsfield, openrouter, validate


class OpenRouterBackend:
    name = "openrouter"

    def __init__(self, production: dict):
        self.p = production
        self._vmodel = None

    # stills
    def image_estimate(self, n_refs: int):
        try:
            return cost.image_cost(openrouter.image_endpoints(self.p["image_model"]), n_refs)
        except Exception as e:  # pricing is advisory; never block on it
            return None, f"unknown ({e.__class__.__name__} fetching pricing)"

    def check_image(self, n_refs: int) -> None:
        model = validate.find_model(openrouter.image_models(), self.p["image_model"])
        validate.validate_image(model, aspect_ratio="9:16", n_refs=n_refs)

    def generate_image(self, prompt: str, refs: list[Path], out: Path) -> Path:
        return openrouter.generate_image(self.p["image_model"], prompt, refs, out)

    # clips
    def video_model(self) -> dict:
        if self._vmodel is None:
            self._vmodel = validate.find_model(openrouter.video_models(), self.p["video_model"])
        return self._vmodel

    def video_durations(self) -> list[int]:
        return validate.video_durations(self.video_model())

    def check_video(self, duration: int) -> None:
        validate.validate_video(self.video_model(), duration=duration,
                                resolution=self.p["video_resolution"], aspect_ratio="9:16")

    def clip_estimate(self, seconds: int):
        return cost.video_clip_cost(self.video_model().get("pricing_skus"), seconds, self.p["video_resolution"])

    def submit_video(self, prompt: str, still: Path, duration: int) -> dict:
        return openrouter.submit_video(self.p["video_model"], prompt, still, duration, self.p["video_resolution"])

    def finish_video(self, job: dict, out: Path) -> Path:
        done = openrouter.wait_video(job)
        return openrouter.download_video(done, out)


class HiggsfieldBackend:
    """EXPERIMENTAL. See mdk/higgsfield.py."""
    name = "higgsfield"

    def __init__(self, production: dict):
        self.p = production

    def image_estimate(self, n_refs: int):
        return None, "Higgsfield credits: " + higgsfield.credit_estimate(
            self.p["higgsfield_image_model"], "estimate", ["--aspect_ratio", "9:16"])

    def check_image(self, n_refs: int) -> None:
        return None

    def generate_image(self, prompt: str, refs: list[Path], out: Path) -> Path:
        return higgsfield.generate_image(self.p["higgsfield_image_model"], prompt, refs, out)

    def video_durations(self) -> list[int]:
        return higgsfield.video_durations(self.p["higgsfield_video_model"])

    def check_video(self, duration: int) -> None:
        if duration not in self.video_durations():
            raise validate.ParamError(f"{self.p['higgsfield_video_model']}: duration {duration} not supported")

    def clip_estimate(self, seconds: int):
        return None, "Higgsfield credits: " + higgsfield.credit_estimate(
            self.p["higgsfield_video_model"], "estimate", ["--duration", str(seconds), "--aspect_ratio", "9:16"])

    def submit_video(self, prompt: str, still: Path, duration: int) -> dict:
        # The CLI blocks until done, so the real work happens in finish_video.
        # "deferred" tells the pipeline nothing has been paid for yet.
        return {"deferred": True, "prompt": prompt, "still": str(still), "duration": duration}

    def finish_video(self, job: dict, out: Path) -> Path:
        return higgsfield.generate_video(self.p["higgsfield_video_model"], job["prompt"], Path(job["still"]),
                                         int(job["duration"]), out)


def make(production: dict):
    name = production.get("backend", "openrouter")
    if name == "openrouter":
        return OpenRouterBackend(production)
    if name == "higgsfield":
        return HiggsfieldBackend(production)
    raise SystemExit(f"unknown backend {name!r}; use openrouter or higgsfield")
