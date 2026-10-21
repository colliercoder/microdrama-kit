"""ffmpeg and ffprobe. Command construction is pure (and tested); running is separate."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11"   # common target for vertical social video


def canvas_filter(width: int, height: int, fps: int = 24) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")


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
            srt: Path | None = None, captions: str = "soft") -> list[str]:
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
    cmd += ["-af", LOUDNESS, "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
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


def silences(audio: Path) -> str:
    r = subprocess.run(silencedetect_cmd(audio), capture_output=True, text=True)
    return r.stderr
