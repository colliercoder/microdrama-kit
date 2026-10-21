"""Command line: python -m mdk <command> ...  (or python3 -m mdk <command> ...)"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import env, http, openrouter, timing, yamlish
from .bible import BIBLE_FILE, PLACEHOLDER_VOICE, PRODUCTION_DEFAULTS, Bible, BibleError
from .cost import Estimate, video_clip_cost
from .pipeline import Episode, Halt, load_board_for_dry_run

KIT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = KIT_ROOT / "templates"


def _episode(args) -> Episode:
    return Episode(Path(args.series), args.episode, dry_run=args.dry_run, yes=args.yes,
                   auto_approve=getattr(args, "auto_approve", False), force=getattr(args, "force", False),
                   max_spend=getattr(args, "max_spend", None))


def cmd_init(args) -> None:
    dest = Path(args.series)
    if (dest / BIBLE_FILE).exists():
        raise Halt(f"{dest / BIBLE_FILE} already exists; not overwriting")
    (dest / "refs").mkdir(parents=True, exist_ok=True)
    (dest / "episodes" / "E01").mkdir(parents=True, exist_ok=True)
    text = (TEMPLATES / "show-bible.yaml").read_text()
    if args.title:
        text = text.replace('title: "My Series"', f"title: {json.dumps(args.title)}")
    (dest / BIBLE_FILE).write_text(text)
    shutil.copy(TEMPLATES / "episode" / "script.md", dest / "episodes" / "E01" / "script.md")
    print(f"Created {dest}/\n  {BIBLE_FILE}          fill this in first (or: python3 -m mdk bible {dest} --premise \"...\")\n"
          f"  episodes/E01/script.md  your first episode\n  refs/                   character reference images")


BIBLE_SYSTEM = ("You are a showrunner writing the show bible for a vertical animated micro-drama "
                "(9:16, under 60 seconds per episode, one narrator). Reply with ONE JSON object only.")


def cmd_bible(args) -> None:
    series = Path(args.series)
    premise = Path(args.premise_file).read_text() if args.premise_file else args.premise
    if not premise:
        raise Halt("give a premise: --premise \"...\" or --premise-file premise.txt")
    template = yamlish.loads((TEMPLATES / "show-bible.yaml").read_text())
    user = f"""Premise from the creator:
{premise}

Fill this show bible structure for that premise. Keep every key. Write concrete, visual,
unambiguous descriptions an image model can follow. Characters: give each a stable id,
a full physical description, constant_traits (what never changes), and 1 to 3 eras if
the character ages or changes look. Locations: describe look and the REAL light source.
global_negative: things that must never appear in this show, one short phrase each.
Leave production and voice.voice_id exactly as given.

Structure (JSON):
{json.dumps(template, indent=2)}"""
    est = Estimate()
    model = PRODUCTION_DEFAULTS["text_model"]
    from .cost import text_cost
    usd, basis = text_cost(openrouter.text_pricing(model), 1500, 3000)
    est.add("bible", "premise to show bible", usd, f"{model}: {basis}")
    print(f"Cost estimate:\n{est.render()}")
    if args.dry_run:
        print("dry run: would ask the text model to draft the bible; nothing written")
        return
    if not args.yes and sys.stdin.isatty() and input("Go? [y/N] ").strip().lower() not in ("y", "yes"):
        raise Halt("stopped")
    if not args.yes and not sys.stdin.isatty():
        raise Halt("needs --yes when not run interactively")
    data = openrouter.extract_json(openrouter.chat(model, BIBLE_SYSTEM, user, max_tokens=6000))
    data["production"] = template["production"]
    data.setdefault("voice", {})["voice_id"] = PLACEHOLDER_VOICE
    target = series / BIBLE_FILE
    series.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = series / "show-bible.prev.yaml"
        shutil.copy(target, backup)
        print(f"previous bible kept at {backup}")
    target.write_text("# Drafted from your premise. Read it all and edit: this file drives every shot.\n"
                      + yamlish.dumps(data) + "\n")
    Bible.load(series)  # validates
    print(f"wrote {target}. Next: set voice.voice_id, then python3 -m mdk refs {series}")


def cmd_refs(args) -> None:
    Episode(Path(args.series), "E01", dry_run=args.dry_run, yes=args.yes, force=args.force).step_refs()


def cmd_step(name):
    def run(args):
        ep = _episode(args)
        if args.dry_run and name != "storyboard":
            load_board_for_dry_run(ep)
        if name == "review":
            ep.step_review(args.max_rerolls)
        elif name == "assemble":
            ep.step_assemble(args.captions)
        else:
            getattr(ep, f"step_{name}")()
    return run


def cmd_approve(args) -> None:
    _episode(args).step_approve(accept_failed=args.accept_failed)


def cmd_reject(args) -> None:
    _episode(args).step_reject(args.shot, args.note)


def cmd_estimate(args) -> None:
    ep = _episode(args)
    load_board_for_dry_run(ep)
    print(f"Estimated remaining spend for {ep.bible.title} {ep.ep}:\n{ep.estimate_all().render()}")


def cmd_run(args) -> None:
    ep = _episode(args)
    if args.dry_run:
        load_board_for_dry_run(ep)
    elif not ep.board_path.is_file():
        ep.step_storyboard()
    est = ep.estimate_all()
    print(f"\n{ep.bible.title} {ep.ep}: estimated spend for the remaining steps\n{est.render()}")
    if not args.dry_run:
        ep.guard(est.known_total, "this episode")
    if args.dry_run:
        print("\nDRY RUN: zero paid calls. What each step would do:")
    elif est.lines and not args.yes:
        if not sys.stdin.isatty():
            raise Halt("needs --yes to approve the spend when not run interactively")
        if input(f"Run the episode for about ${est.known_total:,.2f}? [y/N] ").strip().lower() not in ("y", "yes"):
            raise Halt("stopped: spend not approved")
    ep.yes = True if not args.dry_run else ep.yes
    ep.quiet_estimates = True
    ep.step_storyboard()
    ep.step_voice()
    ep.step_refs(used_only=True)
    ep.step_stills()
    if ep.prod["backend"] == "openrouter" or env.get("OPENROUTER_API_KEY"):
        ep.step_review()
    if not args.dry_run:
        ep.write_contact_sheet()
        if not ep.is_approved() and not args.auto_approve:
            print(f"\nPAUSED at the storyboard gate. Look at {ep.dir / 'contact-sheet.html'}\n"
                  f"Then: python3 -m mdk approve {args.series} {ep.ep}   and re-run: python3 -m mdk run {args.series} {ep.ep}")
            return
    ep.step_clips()
    ep.step_assemble(args.captions)
    if args.dry_run:
        print("\nDry run complete. Nothing was generated and nothing was spent.")
    else:
        from . import spend
        print(f"\nSpend recorded for {ep.ep} (episodes/{ep.ep}/spend.jsonl): "
              + ", ".join(f"{k} ${v:.2f}" for k, v in spend.summary().items()))


def cmd_learn(args) -> None:
    bible = Bible.load(Path(args.series))
    path = bible.add_rule(args.stage, args.rule)
    print(f"added to {path}: [{args.stage}] {args.rule}\nEvery future prompt and review now includes it.")


def cmd_models(args) -> None:
    kind = args.kind
    if kind in ("video", "all"):
        print("Video models (image-to-video, 9:16, first frame). Price shown for a 6s 720p clip:")
        rows = []
        for m in openrouter.video_models():
            if "9:16" not in (m.get("supported_aspect_ratios") or []) or \
                    "first_frame" not in (m.get("supported_frame_images") or []):
                continue
            durs = m.get("supported_durations") or []
            d = min((x for x in durs if x >= 6), default=max(durs) if durs else 6)
            usd, _ = video_clip_cost(m.get("pricing_skus"), d, "720p")
            rows.append((usd if usd is not None else 1e9, m["id"], d, durs))
        for usd, mid, d, durs in sorted(rows):
            price = f"${usd:.2f} ({d}s)" if usd < 1e9 else "unknown"
            print(f"  {mid:<40} {price:<16} durations {durs}")
    if kind in ("image", "all"):
        print("\nImage models that accept 9:16 and reference images:")
        for m in openrouter.image_models():
            p = m.get("supported_parameters") or {}
            ar = (p.get("aspect_ratio") or {}).get("values") or []
            if "9:16" in ar and "input_references" in p:
                print(f"  {m['id']}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mdk", description="Micro-drama kit: animated vertical shorts, one episode at a time.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="show the plan and costs; make zero paid calls")
    common.add_argument("--yes", action="store_true", help="approve the printed spend without asking")
    common.add_argument("--max-spend", type=float, metavar="USD",
                        help="hard cap for this episode; stops before any paid call that would cross it")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold a new series folder from the template", parents=[common])
    p.add_argument("series")
    p.add_argument("--title")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("bible", help="draft the show bible from a one-paragraph premise (LLM)", parents=[common])
    p.add_argument("series")
    p.add_argument("--premise")
    p.add_argument("--premise-file")
    p.set_defaults(func=cmd_bible)

    p = sub.add_parser("refs", help="generate character reference sheets for every character and era", parents=[common])
    p.add_argument("series")
    p.add_argument("--force", action="store_true", help="regenerate even if a reference exists")
    p.set_defaults(func=cmd_refs)

    for name, helptext in [("storyboard", "episode script -> storyboard.json with narration and audio tags"),
                           ("voice", "ONE continuous narration take + per-shot timing"),
                           ("stills", "one keyframe per shot, locked to the character refs"),
                           ("review", "vision check of each still; rerolls failures"),
                           ("clips", "image-to-video for each approved still"),
                           ("assemble", "ffmpeg: clips timed to the narration, 9:16, loudness, captions")]:
        p = sub.add_parser(name, help=helptext, parents=[common])
        p.add_argument("series")
        p.add_argument("episode")
        p.add_argument("--force", action="store_true", help="redo even if outputs exist")
        if name == "review":
            p.add_argument("--max-rerolls", type=int)
        if name == "assemble":
            p.add_argument("--captions", choices=["soft", "burn", "none"], default="soft")
        if name == "clips":
            p.add_argument("--auto-approve", action="store_true")
        p.set_defaults(func=cmd_step(name))

    p = sub.add_parser("approve", help="the human storyboard gate: approve all current stills", parents=[common])
    p.add_argument("series")
    p.add_argument("episode")
    p.add_argument("--accept-failed", action="store_true", help="approve even shots the review failed")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="reject one still with a note; the next stills run edits it", parents=[common])
    p.add_argument("series")
    p.add_argument("episode")
    p.add_argument("shot")
    p.add_argument("--note", required=True)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("run", help="the whole episode end to end, resuming from what exists", parents=[common])
    p.add_argument("series")
    p.add_argument("episode")
    p.add_argument("--auto-approve", action="store_true", help="skip the human storyboard gate (not recommended)")
    p.add_argument("--captions", choices=["soft", "burn", "none"], default="soft")
    p.set_defaults(func=cmd_run, force=False)

    p = sub.add_parser("estimate", help="print the remaining spend for an episode (free)", parents=[common])
    p.add_argument("series")
    p.add_argument("episode")
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("learn", help="add a rule every future prompt and review will follow", parents=[common])
    p.add_argument("series")
    p.add_argument("--stage", required=True, choices=["image", "video", "voice", "story"])
    p.add_argument("rule")
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("models", help="list current OpenRouter video and image models with prices (free)", parents=[common])
    p.add_argument("kind", nargs="?", default="all", choices=["all", "video", "image"])
    p.set_defaults(func=cmd_models)
    return ap


def main(argv: list[str] | None = None) -> int:
    env.load_dotenv(Path.cwd() / ".env", KIT_ROOT / ".env")
    args = build_parser().parse_args(argv)
    if getattr(args, "dry_run", False):
        http.block_paid_calls()
    try:
        args.func(args)
        return 0
    except (Halt, BibleError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 2
    except http.PaidCallBlocked as e:
        print(f"\nBUG: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\ninterrupted; re-run the same command to resume", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
