"""Check generation params against the free model lists BEFORE submitting a paid job."""
from __future__ import annotations


class ParamError(ValueError):
    pass


def find_model(models: list, model_id: str) -> dict:
    for m in models:
        if m.get("id") == model_id:
            return m
    raise ParamError(f"model {model_id!r} is not in the list. Run `python3 -m mdk models` to see current ids.")


def validate_video(model: dict, *, duration: int, resolution: str, aspect_ratio: str,
                   first_frame: bool = True) -> None:
    problems = []
    durs = model.get("supported_durations")
    if durs is not None and duration not in durs:
        problems.append(f"duration {duration}s not in {sorted(durs)}")
    res = model.get("supported_resolutions")
    if res is not None and resolution not in res:
        problems.append(f"resolution {resolution} not in {res}")
    ars = model.get("supported_aspect_ratios")
    if ars is not None and aspect_ratio not in ars:
        problems.append(f"aspect ratio {aspect_ratio} not in {ars}")
    frames = model.get("supported_frame_images") or []
    if first_frame and "first_frame" not in frames:
        problems.append("model does not accept a first_frame image (image-to-video)")
    if problems:
        raise ParamError(f"{model.get('id')}: " + "; ".join(problems))


def validate_image(model: dict, *, aspect_ratio: str, n_refs: int) -> None:
    params = model.get("supported_parameters") or {}
    problems = []
    ar = params.get("aspect_ratio")
    if ar is None:
        problems.append("model does not accept aspect_ratio")
    elif ar.get("type") == "enum" and aspect_ratio not in ar.get("values", []):
        problems.append(f"aspect ratio {aspect_ratio} not in {ar.get('values')}")
    if n_refs and "input_references" not in params:
        problems.append("model does not accept reference images (input_references)")
    if problems:
        raise ParamError(f"{model.get('id')}: " + "; ".join(problems))


def video_durations(model: dict) -> list[int]:
    durs = model.get("supported_durations")
    if not durs:
        raise ParamError(f"{model.get('id')} lists no supported_durations; pick another model")
    return sorted(int(d) for d in durs)
