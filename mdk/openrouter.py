"""OpenRouter client: text + vision (chat completions), images, video. Stdlib only.

Endpoints (checked against https://openrouter.ai/docs on 2026-10-20):
  POST /api/v1/chat/completions          text and vision
  POST /api/v1/images                    image generation, returns data[].b64_json
  GET  /api/v1/images/models             free; image models + supported_parameters
  GET  /api/v1/images/models/{id}/endpoints   free; per-endpoint pricing lines
  POST /api/v1/videos                    submit a video job, returns id + polling_url
  GET  /api/v1/videos/{id}               poll; completed | failed | cancelled | expired
  GET  /api/v1/videos/{id}/content?index=0   download (bearer auth)
  GET  /api/v1/videos/models             free; durations, aspect ratios, pricing_skus
  GET  /api/v1/models                    free; text model pricing
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import shlex
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from . import env, http

BASE = "https://openrouter.ai/api/v1"
TERMINAL_FAIL = {"failed", "cancelled", "expired"}


def _cache_dir() -> Path:
    d = Path.home() / ".cache" / "microdrama-kit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_get(name: str, url: str, max_age_s: int = 6 * 3600):
    """Free GET with a short disk cache so repeated estimates stay fast."""
    path = _cache_dir() / name
    if path.is_file() and time.time() - path.stat().st_mtime < max_age_s:
        return json.loads(path.read_text())
    data = http.request("GET", url, paid=False, timeout=60)
    path.write_text(json.dumps(data))
    return data


def video_models() -> list:
    data = _cached_get("video-models.json", f"{BASE}/videos/models")
    return data.get("data", data) if isinstance(data, dict) else data


def image_models() -> list:
    return _cached_get("image-models.json", f"{BASE}/images/models")["data"]


def image_endpoints(model_id: str) -> list:
    safe = model_id.replace("/", "__")
    return _cached_get(f"image-endpoints-{safe}.json",
                       f"{BASE}/images/models/{model_id}/endpoints").get("endpoints", [])


def text_models() -> list:
    return _cached_get("models.json", f"{BASE}/models")["data"]


def text_pricing(model_id: str) -> dict | None:
    for m in text_models():
        if m.get("id") == model_id:
            return m.get("pricing")
    return None


def _auth() -> dict:
    key = env.require("OPENROUTER_API_KEY", "Get one at https://openrouter.ai/keys")
    return {"Authorization": f"Bearer {key}", "X-Title": "microdrama-kit"}


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# ---- text and vision --------------------------------------------------------

def chat(model: str, system: str, user: str, images: list[Path] | None = None,
         json_mode: bool = True, max_tokens: int = 4000) -> str:
    content: list | str = user
    if images:
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": data_url(p)}} for p in images]
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = http.request("POST", f"{BASE}/chat/completions", headers=_auth(), body=body, paid=True)
    except http.HttpError as e:
        # Some providers refuse response_format; the prompt already asks for JSON, so retry without it.
        if not (json_mode and e.status == 400 and "response_format" in e.body):
            raise
        body.pop("response_format")
        resp = http.request("POST", f"{BASE}/chat/completions", headers=_auth(), body=body, paid=True)
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected chat response: {json.dumps(resp)[:500]}")


def extract_json(text: str):
    """Models sometimes wrap JSON in prose or code fences. Take the outermost object."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model reply: {text[:300]}")
    return json.loads(text[start:end + 1])


# ---- images -----------------------------------------------------------------

def generate_image(model: str, prompt: str, refs: list[Path], out: Path, aspect_ratio: str = "9:16") -> Path:
    body = {"model": model, "prompt": prompt, "aspect_ratio": aspect_ratio, "n": 1}
    if refs:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in refs]
    resp = http.request("POST", f"{BASE}/images", headers=_auth(), body=body, paid=True)
    items = (resp or {}).get("data") or []
    if not items or not items[0].get("b64_json"):
        raise RuntimeError(f"image API returned no image: {json.dumps(resp)[:400]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(items[0]["b64_json"]))
    return out


# ---- video ------------------------------------------------------------------

def _looks_like_url_rejection(err: http.HttpError) -> bool:
    body = err.body.lower()
    return err.status in (400, 415, 422) and any(
        w in body for w in ("url", "data:", "base64", "frame_image", "image_url", "https"))


def upload_frame(path: Path) -> str:
    """Turn a local still into a public https URL using the user's own upload command.

    MDK_UPLOAD_CMD is a shell template with {path} and {name}; it must print the
    public URL as its last line. Example for an S3 or R2 bucket you control:
      aws s3 cp {path} s3://my-bucket/mdk/{name} && echo https://my-bucket.example.com/mdk/{name}
    """
    template = env.get("MDK_UPLOAD_CMD")
    if not template:
        raise SystemExit(
            "The video provider rejected an inline (data URL) first frame and MDK_UPLOAD_CMD is not set.\n"
            "Set MDK_UPLOAD_CMD in .env to a command that uploads {path} to storage you control and "
            "prints the public https URL. See README, 'If the video API wants a public image URL'.")
    cmd = template.format(path=shlex.quote(str(path)), name=shlex.quote(path.name))
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    url = (r.stdout.strip().splitlines() or [""])[-1].strip()
    if r.returncode != 0 or not url.startswith("https://"):
        raise SystemExit(f"MDK_UPLOAD_CMD failed or printed no https URL:\n{r.stderr[-400:]}")
    return url


def submit_video(model: str, prompt: str, first_frame: Path, duration: int, resolution: str,
                 aspect_ratio: str = "9:16") -> dict:
    def body_for(url: str) -> dict:
        return {"model": model, "prompt": prompt, "duration": duration, "resolution": resolution,
                "aspect_ratio": aspect_ratio, "generate_audio": False,
                "frame_images": [{"type": "image_url", "image_url": {"url": url},
                                  "frame_type": "first_frame"}]}
    if env.get("MDK_UPLOAD_ALWAYS") == "1":
        return http.request("POST", f"{BASE}/videos", headers=_auth(), body=body_for(upload_frame(first_frame)), paid=True)
    try:
        return http.request("POST", f"{BASE}/videos", headers=_auth(), body=body_for(data_url(first_frame)), paid=True)
    except http.HttpError as e:
        if not _looks_like_url_rejection(e):
            raise
        print(f"    provider rejected the inline image ({e.status}); retrying with MDK_UPLOAD_CMD")
        return http.request("POST", f"{BASE}/videos", headers=_auth(), body=body_for(upload_frame(first_frame)), paid=True)


def wait_video(job: dict, timeout_s: int = 1800, interval_s: int = 15) -> dict:
    deadline = time.time() + timeout_s
    current = job
    while True:
        status = current.get("status")
        if status == "completed":
            return current
        if status in TERMINAL_FAIL:
            raise RuntimeError(f"video job {current.get('id')} {status}: {current.get('error')}")
        if time.time() > deadline:
            raise TimeoutError(f"video job {current.get('id')} still {status} after {timeout_s}s")
        time.sleep(interval_s)
        url = current.get("polling_url") or f"{BASE}/videos/{quote(current['id'])}"
        if url.startswith("/"):
            url = "https://openrouter.ai" + url
        current = http.request("GET", url, headers=_auth(), paid=False, timeout=60)


def download_video(job: dict, out: Path) -> Path:
    url = f"{BASE}/videos/{quote(job['id'])}/content?index=0"
    data = http.request("GET", url, headers=_auth(), paid=False, raw=True, timeout=600)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
