"""Keys come from the environment or a .env file. Never from code, never from the bible."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(*candidates: Path) -> None:
    """Read KEY=VALUE lines into os.environ without overriding real env vars."""
    for path in candidates:
        if not path or not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


def get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value else default


def require(name: str, why: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set. {why}\nAdd it to your .env (see .env.example).")
    return value
