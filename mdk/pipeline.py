"""The episode pipeline. Each step reads and writes files in the episode folder, so any
step can be re-run alone and `run` can resume by skipping steps whose outputs exist.

Order inside `run` (and why):
  storyboard -> voice -> stills -> review -> approve -> clips -> assemble
  Voice comes before pictures because the narration length is the 60-second gate
  and it decides how long every clip must be. Review and approval come before clips
  because a still costs cents and a clip costs dimes to dollars.
"""
from __future__ import annotations

import hashlib
import html
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import backends, cost, elevenlabs, ffmpeg, http, openrouter, review, storyboard, timing
from .bible import Bible

# Rough token budgets for the cost estimate of text steps.
TOKENS = {"storyboard": (3000, 3000), "review": (3500, 700), "bible": (1500, 3000)}


class Halt(Exception):
    """Stop the pipeline cleanly with a message for the user (not a crash)."""


class Episode:
    def __init__(self, series_dir: Path, episode: str, *, dry_run: bool = False, yes: bool = False,
                 auto_approve: bool = False, force: bool = False):
        self.series = series_dir.resolve()
        self.ep = episode.upper() if episode.lower().startswith("e") else f"E{int(episode):02d}"
        self.dir = self.series / "episodes" / self.ep
        self.bible = Bible.load(self.series)
        self.prod = self.bible.production
        self.backend = backends.make(self.prod)
        self.dry_run = dry_run
        self.yes = yes
        self.auto_approve = auto_approve
        self.force = force
        self.quiet_estimates = False   # `run` prints one episode estimate up front instead
        self._board: dict | None = None
        if dry_run:
            http.block_paid_calls()

    # ---- paths ------------------------------------------------------------
    @property
    def script(self) -> Path: return self.dir / "script.md"
    @property
    def board_path(self) -> Path: return self.dir / "storyboard.json"
    @property
    def vo(self) -> Path: return self.dir / "vo" / "narration.mp3"
    @property
    def timing_path(self) -> Path: return self.dir / "vo" / "timing.json"
    @property
    def review_path(self) -> Path: return self.dir / "review.json"
    @property
    def approved_path(self) -> Path: return self.dir / "approved.json"
    @property
    def jobs_path(self) -> Path: return self.dir / "clips" / "jobs.json"
    @property
    def final(self) -> Path: return self.dir / "out" / f"{self.ep}.mp4"

    def still(self, sid: str) -> Path: return self.dir / "stills" / f"{sid}.png"
    def clip(self, sid: str) -> Path: return self.dir / "clips" / f"{sid}.mp4"

    # ---- helpers ------------------------------------------------------------
    def say(self, msg: str) -> None:
        print(msg, flush=True)

    def board(self) -> dict:
        if self._board is None:
            if not self.board_path.is_file():
                raise Halt(f"no storyboard yet. Run: python3 -m mdk storyboard {self.series} {self.ep}")
            self._board = json.loads(self.board_path.read_text())
        return self._board

    def shots(self) -> list[dict]:
        return self.board()["shots"]

    def spoken_lines(self) -> list[str]:
        pron = (self.bible.voice or {}).get("pronunciations")
        return [timing.apply_pronunciations(s["narration"], pron) for s in self.shots()]

    def narration_hash(self) -> str:
        payload = json.dumps([self.spoken_lines(), self.bible.voice.get("voice_id")])
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def timings(self) -> dict | None:
        if not self.timing_path.is_file():
            return None
        t = json.loads(self.timing_path.read_text())
        return t if t.get("hash") == self.narration_hash() else None

    def confirm(self, est: cost.Estimate, what: str) -> bool:
        if not (self.quiet_estimates and (self.dry_run or self.yes)):
            self.say(f"\nCost estimate for {what}:\n{est.render()}")
        if not est.lines:
            return True
        if self.dry_run:
            if not self.quiet_estimates:
                self.say("  (dry run: nothing will be spent)")
            return False
        if self.yes:
            return True
        if not sys.stdin.isatty():
            raise Halt("needs confirmation: re-run with --yes to approve the spend, or --dry-run to preview")
        answer = input(f"Spend about ${est.known_total:,.2f}{' plus unknown items' if est.has_unknown else ''}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise Halt("stopped: spend not approved")
        return True

    def _text_estimate(self, est: cost.Estimate, step: str, desc: str, n: int = 1) -> None:
        tin, tout = TOKENS[step]
        try:
            usd, basis = cost.text_cost(openrouter.text_pricing(self.prod["text_model"]), tin * n, tout * n)
        except Exception as e:
            usd, basis = None, f"unknown ({e.__class__.__name__} fetching pricing)"
        est.add(step, desc, usd, f"{self.prod['text_model']}: {basis}")

    def _need_text_model(self) -> None:
        if self.prod["backend"] == "higgsfield" and not __import__("os").environ.get("OPENROUTER_API_KEY"):
            raise Halt("this step needs a text/vision model via OPENROUTER_API_KEY. On the Higgsfield-only "
                       "path, write a structured script (see templates) and review stills by eye.")

    # ---- storyboard ------------------------------------------------------------
    def step_storyboard(self) -> None:
        if self.board_path.is_file() and not self.force:
            self.say(f"[storyboard] exists, skipping ({self.board_path.relative_to(self.series)})")
            return
        if not self.script.is_file():
            raise Halt(f"write the episode script first: {self.script}")
        text = self.script.read_text()
        board = storyboard.parse_structured(text, self.ep)
        if board is not None:
            self.say(f"[storyboard] structured script, parsed {len(board['shots'])} shots (free, no model call)")
        else:
            self._need_text_model()
            est = cost.Estimate()
            self._text_estimate(est, "storyboard", "script to shot list")
            if not self.confirm(est, "storyboard"):
                self.say("[storyboard] dry run: would ask the text model to build the shot list")
                return
            system, user = storyboard.llm_prompts(self.bible, text, self.ep)
            board = openrouter.extract_json(openrouter.chat(self.prod["text_model"], system, user))
        board = storyboard.normalise(board)
        problems = storyboard.validate(board, self.bible)
        if problems:
            raise Halt("storyboard problems (fix the script or storyboard.json, then re-run):\n  - "
                       + "\n  - ".join(problems))
        self._board = board
        if self.dry_run:
            self.say("[storyboard] dry run: not writing storyboard.json")
            return
        self.board_path.write_text(storyboard.dumps(board))
        self.say(f"[storyboard] wrote {self.board_path.relative_to(self.series)}")

    # ---- voice -------------------------------------------------------------------
    def step_voice(self) -> None:
        lines = self.spoken_lines()
        text, _ = timing.join_narration(lines)
        guess = timing.estimate_seconds(lines)
        cap = float(self.bible.format["max_seconds"])
        if self.timings() and self.vo.is_file() and not self.force:
            self.say(f"[voice] exists for this exact narration, skipping ({self.timings()['total']:.1f}s)")
            return
        self.say(f"[voice] one continuous take, {len(text)} characters, about {guess:.0f}s by word count (cap {cap:.0f}s)")
        if guess > cap * 1.1:
            raise Halt(f"narration is about {guess:.0f}s, well over the {cap:.0f}s cap. Trim words before paying for voice.")
        est = cost.Estimate()
        usd, basis = cost.voice_cost(len(text), float(self.prod["elevenlabs_usd_per_1k_chars"]))
        est.add("voice", f"ElevenLabs {self.bible.voice['model_id']} single take", usd, basis)
        if not self.confirm(est, "narration"):
            self.say("[voice] dry run: would send the full episode narration as ONE request")
            return
        alignment = elevenlabs.synthesize(text, self.bible.voice, self.vo)
        total = ffmpeg.duration(self.vo)
        durations = timing.blocks_from_alignment(alignment or {}, lines, total) if alignment else None
        method = "elevenlabs-timestamps"
        if durations is None:
            durations = timing.blocks_from_silences(timing.parse_silencedetect(ffmpeg.silences(self.vo)), lines, total)
            method = "silencedetect"
        self.timing_path.write_text(json.dumps({
            "hash": self.narration_hash(), "total": round(total, 3), "method": method,
            "durations": dict(zip([s["id"] for s in self.shots()], durations)), "text": text}, indent=2))
        self.say(f"[voice] {total:.1f}s, shots timed by {method}: " +
                 ", ".join(f"{s['id']}={d:.1f}s" for s, d in zip(self.shots(), durations)))
        if total > cap:
            raise Halt(f"narration runs {total:.1f}s, over the {cap:.0f}s cap. Trim the storyboard narration "
                       f"and run voice again (the old take is replaced).")
        self.say("[voice] listen once now: if any [tag] word was spoken aloud, simplify that tag and re-run.")

    # ---- character refs -------------------------------------------------------------
    def refs_plan(self, used_only: bool = False) -> list[tuple[dict, dict | None, Path]]:
        used = None
        if used_only:
            used = {self.bible.ref_path(c["id"], c.get("era")) for s in self.shots() for c in s.get("characters") or []}
        plan = []
        for c in self.bible.characters:
            for era in (c.get("eras") or [None]):
                path = self.bible.ref_path(c["id"], era.get("id") if era else None)
                if used is None or path in used:
                    plan.append((c, era, path))
        return plan

    def step_refs(self, used_only: bool = False) -> None:
        todo = [(c, e, p) for c, e, p in self.refs_plan(used_only) if not p.is_file() or self.force]
        if not todo:
            self.say("[refs] every character has a reference image, skipping")
            return
        est = cost.Estimate()
        for c, e, p in todo:
            usd, basis = self.backend.image_estimate(n_refs=1)
            est.add("refs", f"{c['id']}{'/' + e['id'] if e else ''} reference sheet", usd, basis)
        if not self.confirm(est, "character reference sheets"):
            for c, e, p in todo:
                self.say(f"[refs] dry run: would create {p.relative_to(self.series)}")
            return
        style = self.bible.data.get("style") or {}
        anchors: dict[str, Path] = {}
        for c, e, p in todo:
            first = anchors.get(c["id"])
            prompt = (f"Character reference sheet, one full-body figure standing, front three-quarter view, "
                      f"plain neutral grey studio backdrop, even soft light, vertical 9:16. Look: {style.get('look', '')}. "
                      f"{self.bible.character_lock(c['id'], e.get('id') if e else None)} "
                      f"Avoid: {'; '.join(self.bible.negatives('image'))}.")
            if first:
                prompt += " Same individual as the reference image, same face and markings, only the era changes."
            self.backend.generate_image(prompt, [first] if first else [], p)
            anchors.setdefault(c["id"], p)
            self.say(f"[refs] wrote {p.relative_to(self.series)}  (look at it; replace it with your own art any time)")

    # ---- stills -------------------------------------------------------------------
    def refs_for(self, shot: dict) -> list[Path]:
        refs = []
        for cast in shot.get("characters") or []:
            p = self.bible.ref_path(cast["id"], cast.get("era"))
            if not p.is_file() and not self.dry_run:
                raise Halt(f"missing reference image {p}. Run python3 -m mdk refs {self.series} or add your own.")
            refs.append(p)
        return refs

    def step_stills(self) -> None:
        todo = [s for s in self.shots() if not self.still(s["id"]).is_file() or self.force]
        if not todo:
            self.say("[stills] all stills exist, skipping")
            return
        if not self.dry_run:
            self.backend.check_image(n_refs=max(len(self.refs_for(s)) for s in todo))
        est = cost.Estimate()
        for s in todo:
            usd, basis = self.backend.image_estimate(n_refs=len(s.get("characters") or []))
            est.add("stills", f"shot {s['id']} keyframe", usd, basis)
        if not self.confirm(est, f"{len(todo)} stills (rerolls in review cost the same per image)"):
            for s in todo:
                prompt = self.bible.still_prompt(s)
                self.say(f"[stills] dry run: shot {s['id']} ({len(prompt)} char prompt, "
                         f"{len(self.refs_for(s))} refs): {s['visual'][:90]}...")
            return
        for s in todo:
            out = self.still(s["id"])
            refs = self.refs_for(s)
            prompt = self.bible.still_prompt(s)
            if s.get("fix_note") and s.get("rejected_still") and Path(s["rejected_still"]).is_file():
                refs = [Path(s["rejected_still"])] + refs
                prompt += review.reroll_instruction([], s["fix_note"])
            self.backend.generate_image(prompt, refs, out)
            self.say(f"[stills] shot {s['id']} -> {out.relative_to(self.series)}")
            if s.pop("fix_note", None) is not None:
                s.pop("rejected_still", None)
                self.board_path.write_text(storyboard.dumps(self.board()))
        self.write_contact_sheet()

    # ---- review --------------------------------------------------------------------
    def step_review(self, max_rerolls: int | None = None) -> None:
        max_rerolls = int(self.prod["max_rerolls"] if max_rerolls is None else max_rerolls)
        report = json.loads(self.review_path.read_text()) if self.review_path.is_file() else {}
        todo = [s for s in self.shots()
                if self.force or s["id"] not in report
                or report[s["id"]].get("still_mtime") != self._mtime(self.still(s["id"]))]
        if not todo:
            self.say("[review] every current still already reviewed, skipping")
            return
        self._need_text_model()
        n_checks = len(self.bible.review_checklist())
        est = cost.Estimate()
        self._text_estimate(est, "review", f"vision check of {len(todo)} stills", n=len(todo))
        usd, basis = self.backend.image_estimate(n_refs=2)
        if usd is not None:
            est.add("rerolls", f"worst case {len(todo) * max_rerolls} rerolls (only if stills fail)",
                    round(usd * len(todo) * max_rerolls, 4), basis)
        if not self.confirm(est, "automated still review"):
            self.say(f"[review] dry run: would check each still against {n_checks} checklist items, "
                     f"rerolling failures up to {max_rerolls} times")
            return
        for s in todo:
            path = self.still(s["id"])
            if not path.is_file():
                raise Halt(f"missing still for shot {s['id']}; run stills first")
            attempt, passed, issues = 0, False, []
            while True:
                reply = openrouter.extract_json(openrouter.chat(
                    self.prod["text_model"], review.SYSTEM, review.prompt(self.bible, s),
                    images=[path] + self.refs_for(s), max_tokens=2000))
                passed, issues = review.decide(reply, n_checks)
                self.say(f"[review] shot {s['id']} try {attempt + 1}: {'PASS' if passed else 'FAIL'}"
                         + ("" if passed else "  " + " | ".join(issues)[:300]))
                if passed or attempt >= max_rerolls:
                    break
                attempt += 1
                keep = path.with_name(f"{s['id']}.v{attempt}.png")
                shutil.copy2(path, keep)
                prompt = self.bible.still_prompt(s) + review.reroll_instruction(issues, reply.get("fix", ""))
                self.backend.generate_image(prompt, [keep] + self.refs_for(s), path)
            report[s["id"]] = {"pass": passed, "issues": issues, "tries": attempt + 1,
                               "still_mtime": self._mtime(path)}
            self.review_path.write_text(json.dumps(report, indent=2))
        self.write_contact_sheet()
        failed = [sid for sid, r in report.items() if not r["pass"]]
        if failed:
            self.say(f"[review] still failing after rerolls: {', '.join(failed)}. Look at contact-sheet.html; "
                     f"fix with `python3 -m mdk reject {self.series} {self.ep} <shot> --note \"...\"` or approve anyway.")

    @staticmethod
    def _mtime(p: Path) -> float | None:
        return round(p.stat().st_mtime, 3) if p.is_file() else None

    # ---- human gate -----------------------------------------------------------------
    def step_approve(self, accept_failed: bool = False) -> None:
        missing = [s["id"] for s in self.shots() if not self.still(s["id"]).is_file()]
        if missing:
            raise Halt(f"cannot approve: stills missing for {missing}")
        report = json.loads(self.review_path.read_text()) if self.review_path.is_file() else {}
        failed = [s["id"] for s in self.shots() if not report.get(s["id"], {}).get("pass")]
        if failed and not accept_failed:
            raise Halt(f"shots {failed} did not pass review (or were not reviewed). Fix them, or approve "
                       f"knowingly with --accept-failed.")
        stamp = {s["id"]: self._mtime(self.still(s["id"])) for s in self.shots()}
        self.approved_path.write_text(json.dumps({"approved_at": datetime.now(timezone.utc).isoformat(),
                                                  "stills": stamp, "accepted_failed": failed}, indent=2))
        self.say(f"[approve] storyboard approved ({len(stamp)} stills). Clips can be rendered now.")

    def is_approved(self) -> bool:
        if not self.approved_path.is_file():
            return False
        stamp = json.loads(self.approved_path.read_text()).get("stills", {})
        return all(stamp.get(s["id"]) == self._mtime(self.still(s["id"])) for s in self.shots())

    def step_reject(self, sid: str, note: str) -> None:
        board = self.board()
        shot = next((s for s in board["shots"] if s["id"] == f"{int(sid):02d}" or s["id"] == sid), None)
        if not shot:
            raise Halt(f"no shot {sid}")
        src = self.still(shot["id"])
        if not src.is_file():
            raise Halt(f"shot {shot['id']} has no still to reject")
        dest = self.dir / "stills" / "_rejected" / f"{shot['id']}.{int(time.time())}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)
        shot["fix_note"], shot["rejected_still"] = note, str(dest)
        self.board_path.write_text(storyboard.dumps(board))
        self.say(f"[reject] shot {shot['id']} moved to {dest.relative_to(self.series)}; the next `stills` run "
                 f"edits it with your note instead of starting from scratch.")

    # ---- clips ----------------------------------------------------------------------
    def clip_plan(self) -> list[tuple[dict, float, int]]:
        t = self.timings()
        durations = self.backend.video_durations()
        if t:
            slot = t["durations"]
        else:
            # No narration take yet: estimate each slot from its word count.
            slot = {s["id"]: timing.estimate_seconds([s["narration"]]) for s in self.shots()}
        return [(s, float(slot[s["id"]]), timing.choose_clip_duration(float(slot[s["id"]]), durations,
                                                                       float(self.prod["max_slowdown"])))
                for s in self.shots()]

    def step_clips(self) -> None:
        if not self.dry_run and not self.timings():
            raise Halt("run voice first: clip lengths come from the narration timing")
        if not self.dry_run and not self.is_approved():
            if self.auto_approve:
                self.step_approve(accept_failed=False)
            else:
                raise Halt(f"storyboard not approved. Open {self.dir / 'contact-sheet.html'}, then run "
                           f"python3 -m mdk approve {self.series} {self.ep}")
        jobs = json.loads(self.jobs_path.read_text()) if self.jobs_path.is_file() else {}
        plan = [(s, slot, d) for s, slot, d in self.clip_plan() if not self.clip(s["id"]).is_file() or self.force]
        if not plan:
            self.say("[clips] all clips exist, skipping")
            return
        def submitted(sid: str) -> bool:
            return sid in jobs and not jobs[sid].get("deferred") and not self.force

        est = cost.Estimate()
        for s, slot, d in plan:
            self.backend.check_video(d)
            if submitted(s["id"]):
                est.add("clips", f"shot {s['id']} (already submitted, just downloading)", 0.0, "paid earlier")
                continue
            usd, basis = self.backend.clip_estimate(d)
            est.add("clips", f"shot {s['id']}: {d}s clip for a {slot:.1f}s slot", usd, basis)
        if not self.confirm(est, f"{len(plan)} clips on {self.prod.get('video_model') if self.backend.name == 'openrouter' else self.prod['higgsfield_video_model']}"):
            for s, slot, d in plan:
                self.say(f"[clips] dry run: shot {s['id']} {d}s, motion prompt starts: {self.bible.motion_prompt(s)[:100]}...")
            return
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        for s, slot, d in plan:
            if submitted(s["id"]):
                continue
            jobs[s["id"]] = self.backend.submit_video(self.bible.motion_prompt(s), self.still(s["id"]), d)
            self.jobs_path.write_text(json.dumps(jobs, indent=2))  # saved at once: a crash must not re-pay
            self.say(f"[clips] shot {s['id']} submitted ({d}s)")
            if not jobs[s["id"]].get("deferred"):
                time.sleep(4)  # stagger submits; providers rate-limit bursts
        failed = []
        for s, slot, d in plan:
            try:
                self.backend.finish_video(jobs[s["id"]], self.clip(s["id"]))
                self.say(f"[clips] shot {s['id']} -> {self.clip(s['id']).relative_to(self.series)}")
            except (RuntimeError, TimeoutError) as e:
                if isinstance(e, TimeoutError):
                    self.say(f"[clips] shot {s['id']} still rendering; re-run clips later to download it")
                    continue
                failed.append(s["id"])
                jobs.pop(s["id"], None)  # forget the dead job so the next run resubmits
                self.jobs_path.write_text(json.dumps(jobs, indent=2))
                self.say(f"[clips] shot {s['id']} FAILED: {str(e)[:300]}")
        if failed:
            raise Halt(f"clips failed for shots {failed}. Re-run clips to resubmit. If one shot keeps failing "
                       f"while others pass, the provider's safety filter is probably refusing that still: make it "
                       f"calmer (no weapon-like props held up, no threat), reject it with a note, and try again.")
        self.say("[clips] watch them once before assembling: drift, extra limbs, text, morphing.")

    # ---- assemble -------------------------------------------------------------------
    def step_assemble(self, captions: str = "soft") -> None:
        if self.final.is_file() and not self.force:
            self.say(f"[assemble] {self.final.relative_to(self.series)} exists, skipping (use --force to rebuild)")
            return
        if self.dry_run:
            self.say(f"[assemble] dry run: would time {len(self.shots())} clips to the narration, 9:16 "
                     f"{self.prod['output_width']}x{self.prod['output_height']}, loudness -14 LUFS, captions={captions}")
            return
        ffmpeg.require_ffmpeg()
        t = self.timings()
        if not t:
            raise Halt("no narration timing for the current storyboard; run voice")
        missing = [s["id"] for s in self.shots() if not self.clip(s["id"]).is_file()]
        if missing:
            raise Halt(f"clips missing for shots {missing}; run clips")
        build = self.dir / "build"
        build.mkdir(exist_ok=True)
        w, h = int(self.prod["output_width"]), int(self.prod["output_height"])
        segs = []
        for s in self.shots():
            target = float(t["durations"][s["id"]])
            actual = ffmpeg.duration(self.clip(s["id"]))
            factor = timing.fit_factor(target, actual)
            seg = build / f"seg_{s['id']}.mp4"
            ffmpeg.run(ffmpeg.segment_cmd(self.clip(s["id"]), seg, target, factor, w, h))
            segs.append(seg)
            self.say(f"[assemble] shot {s['id']}: clip {actual:.2f}s -> slot {target:.2f}s"
                     + (f" (slowed x{factor:.2f})" if factor > 1 else " (trimmed)"))
        lst = build / "concat.txt"
        lst.write_text(ffmpeg.concat_list_text(segs))
        silent = build / "video.mp4"
        ffmpeg.run(ffmpeg.concat_cmd(lst, silent))
        self.final.parent.mkdir(exist_ok=True)
        srt = None
        if captions != "none":
            lines = [s["narration"] for s in self.shots()]  # real spelling, not the respelled TTS text
            durs = [float(t["durations"][s["id"]]) for s in self.shots()]
            srt = self.final.with_suffix(".srt")
            srt.write_text(timing.to_srt(timing.caption_chunks(lines, durs)))
            if captions == "burn" and not ffmpeg.has_filter("subtitles"):
                self.say("[assemble] this ffmpeg has no 'subtitles' filter (needs libass); adding a soft "
                         "caption track instead. The .srt sits next to the video for your editor.")
                captions = "soft"
        ffmpeg.run(ffmpeg.mux_cmd(silent, self.vo, self.final, float(t["total"]), srt, captions))
        final_len = ffmpeg.duration(self.final)
        self.say(f"[assemble] {self.final.relative_to(self.series)}  {final_len:.1f}s"
                 + (f"  captions: {srt.name}" if srt else ""))
        if final_len > float(self.bible.format["max_seconds"]) + 0.5:
            raise Halt(f"final cut is {final_len:.1f}s, over the cap")

    # ---- contact sheet (the human storyboard gate) ----------------------------------
    def write_contact_sheet(self) -> Path:
        report = json.loads(self.review_path.read_text()) if self.review_path.is_file() else {}
        cards = []
        for s in self.shots():
            r = report.get(s["id"])
            verdict = "not reviewed" if r is None else ("PASS" if r["pass"] else "FAIL: " + "; ".join(r["issues"]))
            img = f"stills/{s['id']}.png"
            cards.append(
                f"<figure><img src='{img}' alt='shot {s['id']}'><figcaption><b>{s['id']}</b> "
                f"{html.escape(timing.strip_tags(s['narration']))}<br><small>{html.escape(s['visual'])}</small>"
                f"<br><i>{html.escape(verdict)}</i></figcaption></figure>")
        page = ("<!doctype html><meta charset='utf-8'><title>" + html.escape(f"{self.bible.title} {self.ep}") +
                "</title><style>body{font-family:system-ui;background:#111;color:#eee;margin:24px}"
                "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}"
                "img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:6px;background:#222}"
                "figure{margin:0}small{color:#aaa}i{color:#f6c}</style>"
                f"<h1>{html.escape(self.bible.title)} {self.ep}: approve or reject each shot</h1>"
                "<p>Approve: <code>python3 -m mdk approve SERIES EP</code>. Reject one: "
                "<code>python3 -m mdk reject SERIES EP 03 --note \"what to change\"</code></p><main>"
                + "".join(cards) + "</main>")
        out = self.dir / "contact-sheet.html"
        out.write_text(page)
        return out

    # ---- whole-episode estimate -----------------------------------------------------
    def estimate_all(self) -> cost.Estimate:
        est = cost.Estimate()
        if not self.board_path.is_file() and storyboard.parse_structured(self.script.read_text(), self.ep) is None:
            self._text_estimate(est, "storyboard", "script to shot list")
        if not (self.timings() and self.vo.is_file()):
            text, _ = timing.join_narration(self.spoken_lines())
            usd, basis = cost.voice_cost(len(text), float(self.prod["elevenlabs_usd_per_1k_chars"]))
            est.add("voice", "one continuous take", usd, basis)
        missing_refs = [(c, e) for c, e, p in self.refs_plan(used_only=True) if not p.is_file()]
        if missing_refs:
            usd, basis = self.backend.image_estimate(n_refs=1)
            est.add("refs", f"{len(missing_refs)} character reference sheets",
                    None if usd is None else round(usd * len(missing_refs), 4), basis)
        stills = [s for s in self.shots() if not self.still(s["id"]).is_file()]
        if stills:
            usd, basis = self.backend.image_estimate(n_refs=1)
            est.add("stills", f"{len(stills)} keyframes", None if usd is None else round(usd * len(stills), 4), basis)
            if self.prod["backend"] == "openrouter" or __import__("os").environ.get("OPENROUTER_API_KEY"):
                self._text_estimate(est, "review", f"vision check of {len(stills)} stills", n=len(stills))
            if usd is not None:
                est.add("rerolls", f"allowance: 1 in 3 stills rerolled once", round(usd * len(stills) / 3, 4), basis)
        for s, slot, d in self.clip_plan():
            if not self.clip(s["id"]).is_file():
                usd, basis = self.backend.clip_estimate(d)
                est.add("clips", f"shot {s['id']}: {d}s clip for ~{slot:.1f}s", usd, basis)
        return est


def load_board_for_dry_run(ep: Episode) -> None:
    """Dry run with no storyboard.json: parse a structured script in memory (free)."""
    if ep.board_path.is_file() or ep._board is not None:
        return
    if not ep.script.is_file():
        raise Halt(f"no script at {ep.script}")
    board = storyboard.parse_structured(ep.script.read_text(), ep.ep)
    if board is None:
        raise Halt("dry run needs storyboard.json or a structured script (## Shot N with visual:/narration:). "
                   "Prose scripts need one cheap text-model call to become a storyboard.")
    ep._board = storyboard.normalise(board)
