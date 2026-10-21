"""Automated visual review of stills, before any video money is spent.

The vision model only reports what it sees, check by check. Code decides pass/fail
from those reports, so a model saying "looks fine overall" cannot wave a bad still
through.
"""
from __future__ import annotations

from .bible import Bible

SYSTEM = ("You are a strict continuity supervisor for an animated series. You inspect ONE still "
          "against a checklist and the reference image(s). Report only what you can see. "
          "Reply with ONE JSON object and nothing else.")


def prompt(bible: Bible, shot: dict) -> str:
    checks = bible.review_checklist(shot)
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(checks, 1))
    return f"""The FIRST image is the still to inspect. Any further images are character reference sheets.

Intended shot: {shot['visual']}

Checklist:
{numbered}

For EVERY checklist item, return a result. Use "fail" only for a problem you can actually see.
JSON shape:
{{"checks": [{{"n": 1, "result": "pass" | "fail", "evidence": "what you see"}}],
  "fix": "one sentence telling an image model what to change, or empty if nothing failed"}}"""


def decide(reply: dict, n_checks: int) -> tuple[bool, list[str]]:
    """Pass only if every checklist item came back and none failed."""
    issues = []
    results = {}
    for item in reply.get("checks") or []:
        try:
            results[int(item.get("n"))] = item
        except (TypeError, ValueError):
            continue
    missing = [n for n in range(1, n_checks + 1) if n not in results]
    if missing:
        issues.append(f"reviewer skipped checks {missing}")
    for n in sorted(results):
        item = results[n]
        if str(item.get("result", "")).lower() != "pass":
            issues.append(f"check {n}: {item.get('evidence', 'failed')}")
    return (not issues), issues


def reroll_instruction(issues: list[str], fix: str) -> str:
    detail = fix.strip() or "; ".join(issues)
    return (" EDIT THE FIRST REFERENCE IMAGE: keep the composition, characters, lighting and everything "
            f"else exactly the same, and change only this: {detail}")
