"""A per-episode spend ledger (episodes/EP/spend.jsonl).

Every paid call appends one line. OpenRouter responses carry the real charge in
`usage.cost`, so those lines are actual dollars. Where a provider does not report a
cost in the response (ElevenLabs, some video jobs) the line holds the estimate and says
so. `--max-spend` reads this ledger before every paid call.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LEDGER: Path | None = None


def record(provider: str, kind: str, model: str, usd: float | None, *, estimated: bool,
           detail: str = "") -> None:
    if LEDGER is None:
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"ts": round(time.time(), 1), "provider": provider, "kind": kind, "model": model,
                            "usd": None if usd is None else round(float(usd), 6), "estimated": estimated,
                            "detail": detail}) + "\n")


def usage_cost(resp) -> float | None:
    """OpenRouter puts the charged amount in usage.cost on chat, image and video responses."""
    if isinstance(resp, dict):
        usage = resp.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("cost"), (int, float)):
            return float(usage["cost"])
    return None


def entries(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    if not path or not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def total(path: Path | None = None) -> float:
    return round(sum(e["usd"] for e in entries(path) if e.get("usd") is not None), 4)


def summary(path: Path | None = None) -> dict:
    out: dict = {}
    for e in entries(path):
        key = f"{e['provider']}{' (estimated)' if e.get('estimated') else ''}"
        out[key] = round(out.get(key, 0.0) + (e.get("usd") or 0.0), 4)
    return out
