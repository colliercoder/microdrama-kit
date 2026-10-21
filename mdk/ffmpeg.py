"""ffmpeg and ffprobe. Command construction is pure (and tested); running is separate."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11"   # common target for vertical social video


def loudness_measure_cmd(audio: Path) -> list[str]:
    """Pass 1 of 2: measure. A single loudnorm pass undershoots on real speech
    (the paid test episode came out at -15.6 LUFS), so the mux uses measured values."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af",
            LOUDNESS + ":print_format=json", "-f", "null", "-"]


def parse_loudness(stderr: str) -> dict | None:
    start, end = stderr.rfind("{"), stderr.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(stderr[start:end + 1])
        return {k: data[k] for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        return None


def loudness_filter(measured: dict | None) -> str:
    """Pass 2: linear normalization from the measurement (falls back to one pass)."""
    if not measured:
        return LOUDNESS
    return (f"{LOUDNESS}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true")


def canvas_filter(width: int, height: int, fps: int = 24) -> str:
    """Fill the frame: scale to cover, then center-crop. Never pad. A clip that comes
    back at another aspect ratio would otherwise show black bars (seen in the paid test)."""
    return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}")


def segment_cmd(clip: Path, out: Path, target: float, factor: float,
                width: int, height: int, fps: int = 24) -> list[str]:
    """One shot: slow by `factor` if needed, trim to exactly `target` seconds, fit 9:16."""
    vf = (f"setpts={factor:.5f}*PTS,trim=duration={target:.3f},setpts=PTS-STARTPTS,"
          f"{canvas_filter(width, height, fps)}")
    return ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-filter:v", vf, "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]


def concat_list_text(segments: list[Path]) -> str:
    def esc(p: Path) -> str:
        return str(p.absolute()).replace("'", "'\\''")
    return "".join(f"file '{esc(s)}'\n" for s in segments)


def concat_cmd(list_file: Path, out: Path) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(out)]


def mux_cmd(video: Path, narration: Path, out: Path, duration: float,
            srt: Path | None = None, captions: str = "soft", measured: dict | None = None) -> list[str]:
    """Final mux: one video track + the one continuous narration, loudness-normalized.

    captions: "burn" (needs ffmpeg built with libass), "soft" (selectable subtitle
    track, works everywhere), or "none".
    """
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(narration)]
    if srt and captions == "soft":
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    if srt and captions == "soft":
        cmd += ["-map", "2:s:0", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    if srt and captions == "burn":
        style = "FontName=Arial,FontSize=14,Bold=1,Outline=2,Shadow=0,Alignment=2,MarginV=60"
        path = str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        cmd += ["-vf", f"subtitles='{path}':force_style='{style}'", "-c:v", "libx264",
                "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-af", loudness_filter(measured), "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)]
    return cmd


def silencedetect_cmd(audio: Path, noise_db: int = -35, min_silence: float = 0.25) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"]


def frame_grab_cmd(video: Path, out: Path, at: float) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
            "-frames:v", "1", str(out)]


# ---- runners ---------------------------------------------------------------

def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not on PATH. Install ffmpeg (https://ffmpeg.org/download.html).")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{r.stderr[-800:]}")
    return r


def duration(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip())


def has_filter(name: str) -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == [name] for line in r.stdout.splitlines() if line.strip())


def measure_loudness(audio: Path) -> dict | None:
    r = subprocess.run(loudness_measure_cmd(audio), capture_output=True, text=True)
    return parse_loudness(r.stderr)


EDGES = {"top": "iw:ih*0.06:0:0", "bottom": "iw:ih*0.06:0:ih*0.94",
         "left": "iw*0.08:ih:0:0", "right": "iw*0.08:ih:iw*0.92:0"}
BAR_MAX_LUMA = 30   # a painted bar's brightest pixel stays near black; a dark scene's edge has detail


def edge_luma_cmd(image: Path, edge: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", str(image), "-vf",
            f"crop={EDGES[edge]},signalstats,metadata=print:key=lavfi.signalstats.YMAX",
            "-f", "null", "-"]


def parse_ymax(stderr: str) -> float | None:
    import re
    found = re.findall(r"YMAX=([0-9.]+)", stderr)
    return float(found[-1]) if found else None


def has_black_bands(ymax: dict) -> bool:
    """True for letterbox (top AND bottom) or pillarbox (left AND right) bars painted into a
    still. Image models sometimes do this; a crop cannot remove them. A dark night scene keeps
    detail at its edges, so its brightest edge pixel stays well above BAR_MAX_LUMA."""
    def bar(edge: str) -> bool:
        v = ymax.get(edge)
        return v is not None and v <= BAR_MAX_LUMA
    return (bar("top") and bar("bottom")) or (bar("left") and bar("right"))


def image_size(image: Path) -> tuple[int, int] | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(image)], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except ValueError:
        return None


def black_bands(image: Path) -> bool:
    ymax = {}
    for edge in EDGES:
        r = subprocess.run(edge_luma_cmd(image, edge), capture_output=True, text=True)
        ymax[edge] = parse_ymax(r.stderr)
    return has_black_bands(ymax)


def silences(audio: Path) -> str:
    r = subprocess.run(silencedetect_cmd(audio), capture_output=True, text=True)
    return r.stderr
