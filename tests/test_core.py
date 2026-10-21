"""Unit tests for the pure parts of the kit. Run: python3 -m unittest"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mdk import cost, ffmpeg, http, review, storyboard, timing, validate, yamlish
from mdk.bible import Bible, BibleError

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
EXAMPLE = ROOT / "examples" / "the-last-light"


def fixture(name):
    return json.loads((FIX / name).read_text())


VIDEO_MODELS = fixture("video_models.json")["data"]
IMAGE_MODELS = fixture("image_models.json")["data"]


class YamlishTests(unittest.TestCase):
    def test_nested_lists_blocks_and_comments(self):
        text = """
# comment
series:
  title: "A # not a comment"   # trailing comment
  format:
    aspect_ratio: "9:16"
    max_seconds: 59
desc: >
  folded line one
  folded line two
lit: |
  keep
  lines
items: [a, "b, c", 3]
empty: {}
cast:
  - id: hero
    eras:
      - id: young
        ref: refs/hero-young.png
  - id: crew
plain:
  - one
  - 2.5
  - true
"""
        d = yamlish.loads(text)
        self.assertEqual(d["series"]["title"], "A # not a comment")
        self.assertEqual(d["series"]["format"], {"aspect_ratio": "9:16", "max_seconds": 59})
        self.assertEqual(d["desc"], "folded line one folded line two")
        self.assertEqual(d["lit"], "keep\nlines")
        self.assertEqual(d["items"], ["a", "b, c", 3])
        self.assertEqual(d["empty"], {})
        self.assertEqual(d["cast"][0]["eras"][0]["ref"], "refs/hero-young.png")
        self.assertEqual(d["cast"][1], {"id": "crew"})
        self.assertEqual(d["plain"], ["one", 2.5, True])

    def test_round_trip(self):
        data = {"a": "9:16", "b": ["x: y", "plain words"], "c": [{"id": "h", "n": 1}], "d": {}, "e": "true"}
        self.assertEqual(yamlish.loads(yamlish.dumps(data)), data)

    def test_tabs_rejected(self):
        with self.assertRaises(yamlish.YamlError):
            yamlish.loads("a:\n\tb: 1\n")


class BibleTests(unittest.TestCase):
    def test_template_and_example_load(self):
        for folder, text in [(ROOT, (ROOT / "templates" / "show-bible.yaml").read_text()),
                             (EXAMPLE, (EXAMPLE / "show-bible.yaml").read_text())]:
            b = Bible.from_text(text, folder)
            self.assertTrue(b.title)
            self.assertEqual(b.format["aspect_ratio"], "9:16")
            self.assertEqual(b.production["video_model"], "google/veo-3.1-lite")

    def test_validation_errors(self):
        bad = 'series:\n  title: ""\ncharacters:\n  - id: a\n  - id: a\n    description: x\n'
        with self.assertRaises(BibleError) as ctx:
            Bible.from_text(bad, ROOT)
        msg = str(ctx.exception)
        self.assertIn("series.title", msg)
        self.assertIn("duplicate character id", msg)
        self.assertIn("needs a description", msg)

    def test_refs_negatives_and_prompts(self):
        b = Bible.load(EXAMPLE)
        self.assertEqual(b.ref_path("moss", "old"), EXAMPLE / "refs/moss-old.png")
        self.assertEqual(b.ref_path("moss", None), EXAMPLE / "refs/moss-young.png")  # first era by default
        with self.assertRaises(BibleError):
            b.era("moss", "astronaut")
        negs = b.negatives("image")
        self.assertTrue(any("no humans" in n for n in negs))
        self.assertFalse(any(r in negs for r in b.rules("image")), "rules must not be duplicated as negatives")
        shot = {"visual": "a hedgehog with a key", "location": "shore", "characters": [{"id": "moss", "era": "young"}]}
        still = b.still_prompt(shot)
        self.assertIn("yellow oilskin raincoat", still)
        self.assertIn("night storm", still)
        motion = b.motion_prompt(shot)
        self.assertIn("mouths stay closed", motion)
        self.assertIn("no humans", motion)  # negatives repeated in the video prompt on purpose

    def test_character_rules_stay_out_of_empty_shots(self):
        # Regression (paid E2E 2026-10-20): "Moss is tiny next to everything: the brass lens..."
        # leaked into the empty storm-wave shot and the image model invented an animal there.
        b = Bible.load(EXAMPLE)
        empty = {"visual": "a wave breaks on rocks", "location": "shore", "characters": []}
        with_moss = {"visual": "Moss at the lens", "location": "lamp_room", "characters": [{"id": "moss", "era": "young"}]}
        moss_rule = next(r for r in b.rules("image") if "Moss" in r)
        self.assertNotIn(moss_rule, b.rules("image", empty))
        self.assertIn(moss_rule, b.rules("image", with_moss))
        self.assertIn("Every prop a character touches is in the still; the video model will not add it later.",
                      b.rules("image", empty))  # rules naming nobody still apply
        self.assertNotIn(moss_rule, b.still_prompt(empty))
        self.assertIn("No characters in this shot", b.still_prompt(empty))
        self.assertIn("No characters in this shot", b.motion_prompt(empty))
        self.assertNotIn("Moss stays upright", b.motion_prompt(empty))
        self.assertIn("The only characters in this shot: Moss.", b.still_prompt(with_moss))

    def test_review_checklist_checks_the_cast(self):
        # Regression (paid E2E): the reviewer passed an empty shot with an invented animal in it.
        b = Bible.load(EXAMPLE)
        empty = b.review_checklist({"visual": "waves", "characters": []})
        self.assertTrue(empty[0].startswith("There are no characters at all"))
        self.assertFalse(any("Moss" in c for c in empty))
        cast = b.review_checklist({"visual": "x", "characters": [{"id": "moss", "era": "young"}]})
        self.assertIn("The only characters visible are: Moss. Any extra animal, creature or person is a fail.", cast)
        # Calibrated intent check: details like gaze or expression must not fail a still.
        self.assertTrue(any("small differences in pose, gaze, expression" in c for c in cast))
        self.assertIn("Checklist:\n1. There are no characters at all",
                      review.prompt(b, {"visual": "waves", "characters": []}))

    def test_learned_rules_file_is_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "show-bible.yaml").write_text((EXAMPLE / "show-bible.yaml").read_text())
            b = Bible.load(tmp)
            b.add_rule("image", "Hard hats stay on.")
            b.add_rule("image", "Hard hats stay on.")  # no duplicates
            again = Bible.load(tmp)
            self.assertEqual(again.rules("image").count("Hard hats stay on."), 1)
            self.assertIn("Does not show: no humans, no people, no human faces, no human hands; every character is an animal",
                          again.review_checklist())
            self.assertIn("Hard hats stay on.", again.review_checklist())
            with self.assertRaises(BibleError):
                b.add_rule("music", "x")


class TimingTests(unittest.TestCase):
    LINES = ["[low] One two.", "[warm] Three four five."]

    def test_tags_and_words(self):
        self.assertEqual(timing.strip_tags("[low] Hello... [quietly] there."), "Hello... there.")
        self.assertEqual(timing.spoken_words("[a] one [b] two three"), 3)
        self.assertEqual(timing.estimate_seconds(["one two three four five six"], words_per_second=2), 3.0)

    def test_join_offsets(self):
        text, offsets = timing.join_narration(self.LINES)
        self.assertEqual(text, "[low] One two. [warm] Three four five.")
        self.assertEqual(offsets, [0, 15])
        self.assertEqual(text[offsets[1]:offsets[1] + 6], "[warm]")

    def test_alignment_split(self):
        text, offsets = timing.join_narration(self.LINES)
        n = len(text)
        starts = [i * 0.1 for i in range(n)]
        ends = [s + 0.1 for s in starts]
        # Put a pause between the lines: everything in line 2 starts 1s later.
        starts = [s + (1.0 if i >= offsets[1] else 0) for i, s in enumerate(starts)]
        ends = [e + (1.0 if i >= offsets[1] else 0) for i, e in enumerate(ends)]
        total = ends[-1] + 0.2
        durs = timing.blocks_from_alignment(
            {"characters": list(text), "character_start_times_seconds": starts,
             "character_end_times_seconds": ends}, self.LINES, total)
        # cut = midpoint of end of '.' (index 13) and start of '[' (index 15)
        cut = (ends[13] + starts[15]) / 2
        self.assertAlmostEqual(durs[0], round(cut, 3), places=3)
        self.assertAlmostEqual(sum(durs), total, places=2)

    def test_alignment_mismatch_returns_none(self):
        self.assertIsNone(timing.blocks_from_alignment({"characters": ["x"]}, self.LINES, 5.0))

    def test_silencedetect_parse_and_snap(self):
        stderr = ("[silencedetect @ 0x1] silence_start: 2.9\n"
                  "[silencedetect @ 0x1] silence_end: 3.3 | silence_duration: 0.4\n"
                  "[silencedetect @ 0x1] silence_start: 9.95\n"
                  "[silencedetect @ 0x1] silence_end: 10.2 | silence_duration: 0.25\n")
        sil = timing.parse_silencedetect(stderr)
        self.assertEqual(sil, [(2.9, 3.3), (9.95, 10.2)])
        lines = ["one two three four", "five six seven eight", "nine ten eleven twelve"]
        durs = timing.blocks_from_silences(sil, lines, 12.0)
        # expected cuts at 4.0 and 8.0; first snaps to 3.1, second has no silence within
        # tolerance of 8.0 other than 10.075 (2.075 away), which is inside 2.5s
        self.assertEqual(durs, [3.1, 6.975, 1.925])
        self.assertAlmostEqual(sum(durs), 12.0, places=3)

    def test_choose_clip_duration(self):
        veo = [4, 6, 8]
        self.assertEqual(timing.choose_clip_duration(3.0, veo), 4)
        self.assertEqual(timing.choose_clip_duration(4.9, veo), 4)    # 4 x 1.25 = 5.0 covers it
        self.assertEqual(timing.choose_clip_duration(5.1, veo), 6)
        self.assertEqual(timing.choose_clip_duration(12.0, veo), 8)   # longest, slowed in assembly
        self.assertEqual(timing.choose_clip_duration(5.1, veo, max_slowdown=1.0), 6)

    def test_fit_factor(self):
        self.assertEqual(timing.fit_factor(4.0, 6.0), 1.0)       # trim, never speed up
        self.assertEqual(timing.fit_factor(5.0, 4.0), 1.25)
        with self.assertRaises(ValueError):
            timing.fit_factor(5.0, 0)

    def test_captions_and_srt(self):
        cues = timing.caption_chunks(["[low] one two three four five six", "[up] seven"], [6.0, 2.0], max_words=4)
        self.assertEqual(cues, [(0.0, 4.0, "one two three four"), (4.0, 6.0, "five six"), (6.0, 8.0, "seven")])
        srt = timing.to_srt(cues[:1])
        self.assertEqual(srt, "1\n00:00:00,000 --> 00:00:04,000\none two three four\n\n")

    def test_pronunciations(self):
        self.assertEqual(timing.apply_pronunciations("Nguyen and Nguyens", {"Nguyen": "Win"}),
                         "Win and Nguyens")


class FfmpegTests(unittest.TestCase):
    def test_segment_command(self):
        cmd = ffmpeg.segment_cmd(Path("in.mp4"), Path("out.mp4"), 5.0, 1.25, 1080, 1920)
        vf = cmd[cmd.index("-filter:v") + 1]
        self.assertTrue(vf.startswith("setpts=1.25000*PTS,trim=duration=5.000,setpts=PTS-STARTPTS,"))
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease", vf)
        self.assertIn("pad=1080:1920", vf)
        self.assertIn("-an", cmd)

    def test_mux_soft_burn_none(self):
        soft = ffmpeg.mux_cmd(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"), 30.0, Path("o.srt"), "soft")
        self.assertIn("mov_text", soft)
        self.assertEqual(soft[soft.index("-c:v") + 1], "copy")
        self.assertIn(ffmpeg.LOUDNESS, soft)
        self.assertEqual(soft[soft.index("-t") + 1], "30.000")
        burn = ffmpeg.mux_cmd(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"), 30.0, Path("o.srt"), "burn")
        self.assertTrue(burn[burn.index("-vf") + 1].startswith("subtitles='o.srt'"))
        self.assertEqual(burn[burn.index("-c:v") + 1], "libx264")
        none = ffmpeg.mux_cmd(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"), 30.0, None, "none")
        self.assertNotIn("mov_text", none)
        self.assertEqual(none.count("-i"), 2)

    def test_concat_list_escapes_quotes(self):
        text = ffmpeg.concat_list_text([Path("/tmp/it's.mp4")])
        self.assertEqual(text, "file '/tmp/it'\\''s.mp4'\n")


class ValidateTests(unittest.TestCase):
    def test_video_params(self):
        veo = validate.find_model(VIDEO_MODELS, "google/veo-3.1-lite")
        validate.validate_video(veo, duration=6, resolution="720p", aspect_ratio="9:16")
        with self.assertRaises(validate.ParamError) as ctx:
            validate.validate_video(veo, duration=5, resolution="480p", aspect_ratio="1:1")
        msg = str(ctx.exception)
        self.assertIn("duration 5s", msg)
        self.assertIn("resolution 480p", msg)
        self.assertIn("aspect ratio 1:1", msg)
        hailuo = validate.find_model(VIDEO_MODELS, "minimax/hailuo-2.3")
        with self.assertRaises(validate.ParamError):
            validate.validate_video(hailuo, duration=6, resolution="1080p", aspect_ratio="9:16")
        aleph = validate.find_model(VIDEO_MODELS, "runway/aleph-2")
        with self.assertRaises(validate.ParamError):
            validate.video_durations(aleph)
        with self.assertRaises(validate.ParamError):
            validate.find_model(VIDEO_MODELS, "nope/not-a-model")

    def test_image_params(self):
        gem = validate.find_model(IMAGE_MODELS, "google/gemini-2.5-flash-image")
        validate.validate_image(gem, aspect_ratio="9:16", n_refs=2)
        mini = validate.find_model(IMAGE_MODELS, "openai/gpt-5-image-mini")
        with self.assertRaises(validate.ParamError):
            validate.validate_image(mini, aspect_ratio="9:16", n_refs=0)
        recraft = validate.find_model(IMAGE_MODELS, "recraft/recraft-v4.1-flash")
        with self.assertRaises(validate.ParamError):
            validate.validate_image(recraft, aspect_ratio="9:16", n_refs=1)


class CostTests(unittest.TestCase):
    def skus(self, mid):
        return validate.find_model(VIDEO_MODELS, mid)["pricing_skus"]

    def test_usd_per_second(self):
        usd, basis = cost.video_clip_cost(self.skus("google/veo-3.1-lite"), 6, "720p")
        self.assertAlmostEqual(usd, 0.18)
        self.assertIn("without_audio_720p", basis)

    def test_cents_per_second_plus_image_input(self):
        usd, basis = cost.video_clip_cost(self.skus("x-ai/grok-imagine-video-1.5-lite"), 6, "720p")
        self.assertAlmostEqual(usd, 0.18 + 0.01)
        self.assertIn("cents", basis)

    def test_token_priced_and_image_to_video_keys(self):
        usd, basis = cost.video_clip_cost(self.skus("bytedance/seedance-2.0-mini"), 5, "720p")
        self.assertAlmostEqual(usd, round(0.0000035 * 720 * 1280 * 24 * 5 / 1024, 4))
        self.assertIn("approx", basis)
        usd, basis = cost.video_clip_cost(self.skus("kwaivgi/kling-v3.0-std"), 5, "720p")
        self.assertTrue(basis.startswith("image_to_video_duration_seconds_720p"))

    def test_minimum_and_unknown(self):
        usd, _ = cost.video_clip_cost({"cents_per_second_output": "28", "minimum_cents_per_generation": "56"}, 1, "720p")
        self.assertAlmostEqual(usd, 0.56)
        usd, basis = cost.video_clip_cost({"something_new": "1"}, 6, "720p")
        self.assertIsNone(usd)
        self.assertIn("unknown", basis)

    def test_image_cost(self):
        eps = fixture("image_endpoints_gemini25.json")["endpoints"]
        usd, _ = cost.image_cost(eps, n_refs=2)
        self.assertAlmostEqual(usd, round(3e-05 * 1290 + 3e-07 * 258 * 2, 4))
        usd, _ = cost.image_cost([{"provider_slug": "x", "pricing": [
            {"billable": "output_image", "unit": "image", "cost_usd": 0.05}]}])
        self.assertEqual(usd, 0.05)
        self.assertEqual(cost.image_cost([])[0], None)

    def test_text_voice_and_total(self):
        self.assertEqual(cost.text_cost({"prompt": "0.000001", "completion": "0.000002"}, 1000, 500)[0], 0.002)
        self.assertIsNone(cost.text_cost({"prompt": "-1", "completion": "-1"}, 1, 1)[0])
        self.assertEqual(cost.voice_cost(1000, 0.3)[0], 0.3)
        est = cost.Estimate()
        est.add("a", "x", 1.0, "")
        est.add("b", "y", None, "")
        self.assertEqual(est.known_total, 1.0)
        self.assertTrue(est.has_unknown)
        self.assertIn("unknown items", est.render())


class StoryboardTests(unittest.TestCase):
    def setUp(self):
        self.bible = Bible.load(EXAMPLE)

    def test_structured_example(self):
        board = storyboard.normalise(storyboard.parse_structured(
            (EXAMPLE / "episodes/E01/script.md").read_text(), "E01"))
        self.assertEqual(len(board["shots"]), 6)
        self.assertEqual(board["title"], 'E01 - "The Night the Light Went Out"')
        self.assertEqual(board["shots"][0]["characters"], [{"id": "moss", "era": "old"}])
        self.assertEqual(board["shots"][3]["characters"], [])
        self.assertEqual(storyboard.validate(board, self.bible), [])
        self.assertLess(timing.estimate_seconds([s["narration"] for s in board["shots"]]), 59)

    def test_prose_is_not_structured(self):
        self.assertIsNone(storyboard.parse_structured("# E01\n\nOnce upon a time a hedgehog climbed a lighthouse.", "E01"))

    def test_validation_catches_problems(self):
        board = {"shots": [
            {"id": "01", "visual": "x", "narration": "[low] He had 3 dogs.", "characters": [{"id": "ghost"}]},
            {"id": "01", "visual": "", "narration": "[this tag is far too long to be a real audio tag] ok",
             "characters": [{"id": "moss", "era": "astronaut"}], "location": "moon"},
        ]}
        problems = "\n".join(storyboard.validate(board, self.bible))
        for needle in ("not unique", "spell out numbers", "unknown character 'ghost'", "no visual",
                       "odd audio tag", "no era 'astronaut'", "unknown location 'moon'"):
            self.assertIn(needle, problems)

    def test_too_long_is_caught(self):
        board = {"shots": [{"id": "01", "visual": "x", "narration": " ".join(["word"] * 200), "characters": []}]}
        self.assertTrue(any("over the 59s cap" in p for p in storyboard.validate(board, self.bible)))


class ReviewTests(unittest.TestCase):
    def test_code_decides(self):
        ok, issues = review.decide({"checks": [{"n": 1, "result": "pass"}, {"n": 2, "result": "PASS"}]}, 2)
        self.assertTrue(ok)
        self.assertEqual(issues, [])
        ok, issues = review.decide({"checks": [{"n": 1, "result": "pass"},
                                               {"n": 2, "result": "fail", "evidence": "human hand"}]}, 2)
        self.assertFalse(ok)
        self.assertEqual(issues, ["check 2: human hand"])
        ok, issues = review.decide({"checks": [{"n": 1, "result": "pass"}], "overall": "looks great"}, 3)
        self.assertFalse(ok, "skipped checks must fail, whatever the model says overall")


class SpendTests(unittest.TestCase):
    def tearDown(self):
        from mdk import spend
        spend.LEDGER = None

    def test_ledger_and_guard(self):
        from mdk import spend
        from mdk.pipeline import Episode, Halt
        with tempfile.TemporaryDirectory() as tmp:
            series = Path(tmp) / "show"
            series.mkdir()
            (series / "show-bible.yaml").write_text((EXAMPLE / "show-bible.yaml").read_text())
            ep = Episode(series, "E01", max_spend=1.0)
            self.assertEqual(spend.LEDGER, ep.dir / "spend.jsonl")
            self.assertIsNone(spend.usage_cost({"usage": {"prompt_tokens": 3}}))
            spend.record("openrouter", "image", "m", spend.usage_cost({"usage": {"cost": 0.4}}), estimated=False)
            spend.record("elevenlabs", "voice", "v", 0.3, estimated=True)
            spend.record("openrouter", "text", "m", None, estimated=False)
            self.assertAlmostEqual(spend.total(), 0.7)
            self.assertEqual(spend.summary(), {"openrouter": 0.4, "elevenlabs (estimated)": 0.3})
            ep.guard(0.3, "fits exactly")
            with self.assertRaises(Halt):
                ep.guard(0.31, "one cent over")
            Episode(series, "E01").guard(100.0, "no cap set")


class DryRunTests(unittest.TestCase):
    def tearDown(self):
        http._PAID_ALLOWED = True

    def test_paid_request_blocked(self):
        http.block_paid_calls()
        with self.assertRaises(http.PaidCallBlocked):
            http.request("POST", "https://example.invalid/x", body={}, paid=True)

    def test_full_dry_run_writes_nothing_and_pays_nothing(self):
        from mdk import cli, openrouter
        with tempfile.TemporaryDirectory() as tmp:
            series = Path(tmp) / "show"
            series.mkdir()
            (series / "show-bible.yaml").write_text((EXAMPLE / "show-bible.yaml").read_text())
            (series / "episodes/E01").mkdir(parents=True)
            (series / "episodes/E01/script.md").write_text((EXAMPLE / "episodes/E01/script.md").read_text())
            before = sorted(p.relative_to(series) for p in series.rglob("*"))
            pricing = {"prompt": "0.0000003", "completion": "0.0000025"}
            with mock.patch.object(openrouter, "video_models", return_value=VIDEO_MODELS), \
                    mock.patch.object(openrouter, "image_models", return_value=IMAGE_MODELS), \
                    mock.patch.object(openrouter, "image_endpoints",
                                      return_value=fixture("image_endpoints_gemini25.json")["endpoints"]), \
                    mock.patch.object(openrouter, "text_pricing", return_value=pricing), \
                    mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = cli.main(["run", str(series), "E01", "--dry-run"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("zero paid calls", out.getvalue())
            self.assertIn("TOTAL", out.getvalue())
            after = sorted(p.relative_to(series) for p in series.rglob("*"))
            self.assertEqual(before, after, "dry run must not create files")


if __name__ == "__main__":
    unittest.main()
