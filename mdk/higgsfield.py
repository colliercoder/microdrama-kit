"""EXPERIMENTAL Higgsfield backend for stills and clips.

This drives the official `higgsfield` CLI (https://github.com/higgsfield-ai/cli),
which handles login, uploads and polling. It has not been through a paid end-to-end
run inside this kit yet. If a flag or output field changes, this is the file to fix.

Install:  curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
Login:    higgsfield auth login
Path:     set HIGGSFIELD_BIN in .env if the CLI is not on your PATH
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from . import env, http


def _cli() -> str:
    path = env.get("HIGGSFIELD_BIN") or shutil.which("higgsfield")
    if not path:
        raise SystemExit("The higgsfield CLI is not installed. See the header of mdk/higgsfield.py.")
    return path


def _run_json(args: list[str], paid: bool, timeout: int = 1800):
    if paid and not http.paid_calls_allowed():
        raise http.PaidCallBlocked("dry run: refusing paid Higgsfield job")
    r = subprocess.run([_cli(), *args, "--json"], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"higgsfield {' '.join(args[:3])} failed:\n{(r.stderr or r.stdout)[-600:]}")
    return json.loads(r.stdout)


def _result_url(payload) -> str:
    jobs = payload if isinstance(payload, list) else [payload]
    for job in jobs:
        if isinstance(job, dict) and job.get("status") == "completed" and job.get("result_url"):
            return job["result_url"]
    raise RuntimeError(f"higgsfield job did not complete: {json.dumps(payload)[:400]}")


def _download(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as resp:
        out.write_bytes(resp.read())
    return out


def video_durations(model: str) -> list[int]:
    schema = _run_json(["model", "get", model], paid=False, timeout=60)
    for p in schema.get("params", []):
        if p.get("name") == "duration" and p.get("enum"):
            return sorted(int(x) for x in p["enum"])
    return [5]


def generate_image(model: str, prompt: str, refs: list[Path], out: Path, aspect_ratio: str = "9:16") -> Path:
    args = ["generate", "create", model, "--prompt", prompt, "--aspect_ratio", aspect_ratio]
    for ref in refs:
        args += ["--image-references", str(ref)]
    args += ["--wait", "--wait-timeout", "10m"]
    return _download(_result_url(_run_json(args, paid=True)), out)


def generate_video(model: str, prompt: str, first_frame: Path, duration: int, out: Path,
                   aspect_ratio: str = "9:16") -> Path:
    args = ["generate", "create", model, "--prompt", prompt, "--start-image", str(first_frame),
            "--duration", str(duration), "--aspect_ratio", aspect_ratio,
            "--wait", "--wait-timeout", "30m"]
    return _download(_result_url(_run_json(args, paid=True, timeout=2400)), out)


def credit_estimate(model: str, prompt: str, extra: list[str] | None = None) -> str:
    """`higgsfield generate cost` estimates credits without creating a job (free)."""
    try:
        r = subprocess.run([_cli(), "generate", "cost", model, "--prompt", prompt, *(extra or [])],
                           capture_output=True, text=True, timeout=60)
        return (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "unknown"
    except (SystemExit, OSError, subprocess.TimeoutExpired):
        return "unknown (higgsfield CLI not available)"
