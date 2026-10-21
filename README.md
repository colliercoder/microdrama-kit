# microdrama-kit

Make your own animated character micro-drama: vertical 9:16 shorts, under 60 seconds,
one narrated episode at a time. You bring the character and the story. The kit runs
the pipeline: show bible, character references, storyboard, keyframes, an automated
visual check, image-to-video clips, one continuous narration take, and the final edit.

By Dan Gauerke, [danielgauerke.com](https://danielgauerke.com).

This is the pipeline behind UNDERDAWG, the Big Dog origin series: https://danielgauerke.com/big-dog

## The worked example: THE LAST LIGHT

`examples/the-last-light/` is a made-up series to learn from: Moss, the smallest hedgehog
on a storm-beaten island, inherits the lighthouse the night the lamp goes dark. It has a
complete show bible and a six-shot first episode. No character art and no voice ship
with it: generate the references with `refs` (or bring your own) and pick a voice.

## What you need

- **Python 3.10+** (standard library only, nothing to pip install)
- **ffmpeg** on your PATH ([download](https://ffmpeg.org/download.html); macOS: `brew install ffmpeg`)
- **An OpenRouter key** for text, stills and video ([openrouter.ai/keys](https://openrouter.ai/keys)).
  Or the **Higgsfield CLI** for stills and video (experimental, see below).
- **An ElevenLabs key** for narration ([elevenlabs.io](https://elevenlabs.io)), and a voice you pick or design there.

That is all.

## Quickstart (about 3 minutes to your first dry run)

```bash
git clone <this repo> microdrama-kit && cd microdrama-kit
cp .env.example .env            # paste your keys into .env

# 1. See the whole pipeline on the example, with costs, spending nothing
python3 -m mdk run examples/the-last-light E01 --dry-run

# 2. Start your own series
python3 -m mdk init shows/my-show --title "My Show"
python3 -m mdk bible shows/my-show --premise "A retired lighthouse cat narrates the night the light went out..."
#    then open shows/my-show/show-bible.yaml, read every line, set voice.voice_id

# 3. Character reference sheets (or drop your own art into shows/my-show/refs/)
python3 -m mdk refs shows/my-show

# 4. Write episodes/E01/script.md, then run the episode
python3 -m mdk run shows/my-show E01
#    it pauses at the storyboard gate: open episodes/E01/contact-sheet.html
python3 -m mdk approve shows/my-show E01
python3 -m mdk run shows/my-show E01      # resumes: clips, then the final edit
```

The finished episode lands in `shows/my-show/episodes/E01/out/E01.mp4`, with an `.srt`
caption file next to it.

Every paid step prints a cost estimate first and asks before spending. `--yes` approves
without asking (for scripts and agents). `--dry-run` makes zero paid calls.

## Commands

| Command | What it does | Paid? |
|---|---|---|
| `init SERIES` | Scaffold a series folder from the template | free |
| `bible SERIES --premise "..."` | Draft the show bible from a one-paragraph premise | ~1 cent |
| `refs SERIES` | Character reference sheet per character and era | per image |
| `storyboard SERIES E01` | Script to `storyboard.json`: shots + narration with audio tags | free for structured scripts, ~1 cent for prose |
| `voice SERIES E01` | ONE continuous narration take + per-shot timing | per character |
| `stills SERIES E01` | One keyframe per shot, locked to the character refs | per image |
| `review SERIES E01` | Vision check of every still; failures get edited and rechecked | cents |
| `approve SERIES E01` | The human storyboard gate | free |
| `reject SERIES E01 03 --note "..."` | Reject one still; the next `stills` edits it with your note | free |
| `clips SERIES E01` | Image-to-video for each approved still | per second of video |
| `assemble SERIES E01` | ffmpeg: clips timed to the narration, 9:16, loudness-normalized, captions | free |
| `run SERIES E01` | All of the above in order, skipping steps whose outputs exist | sum |
| `estimate SERIES E01` | Remaining spend for the episode | free |
| `learn SERIES --stage image "rule"` | Add a rule every future prompt and review follows | free |
| `models [video\|image]` | Current OpenRouter models that fit, with prices | free |

## The method (and why it is built this way)

These are the lessons learned the hard way, each one paid for in rerolls.

**1. Show bible first.** One file holds the characters, their looks per era, the
locations and their real light, the voice, and the things that must never appear.
Every step reads it. Consistency across a season comes from this file, never from
re-describing the character in each prompt by hand. When a character drifts, you fix
the bible once and every future shot inherits the fix.

**2. Per-era character references.** A character that ages or changes outfits gets one
reference image per era. Every still is generated with the right reference attached,
which holds the face and markings far better than words alone.

**3. Narration is ONE continuous take.** The whole episode goes to ElevenLabs `eleven_v3`
in a single request, with inline audio tags like `[weary]` or `[quietly]` placed at the
emotional shifts inside each line. Stitching separate per-line clips sounds like a
robot reading a list: the pacing, breath and tone reset at every seam. One take sounds
like a person telling a story. The picture is then cut to the voice, not the other way
round.

**4. Measure the voice before you make video.** The narration length is the 60-second
gate and it decides how long each clip must be. The kit records the take first, splits
it into per-shot slots using ElevenLabs character timestamps (or real pauses in the
audio when timestamps are not usable), and only then orders clips of the right length.
Clips that run long are trimmed; clips that run short are slowed a little. A frozen last
frame never appears.

**5. Storyboard before generating.** Every shot is written down (picture, camera move,
motion, narration) before anything is rendered. One idea per shot, the picture changes
every shot, and every episode ends on a door, not a summary.

**6. Review stills before video.** A still costs cents; a clip costs dimes to dollars.
So every still goes through an automated vision check against the bible: on model,
no text artifacts, no extra limbs, matches the shot, none of the never-list. The model
only reports what it sees per check; code decides pass or fail. A failed still is
*edited* (the bad still goes back in as the first reference with "keep everything,
change only this"), not regenerated from scratch, because from-scratch throws away
everything that was right. Then you look at the contact sheet and approve. The
automated check catches most problems; your eye catches the rest.

**7. One global negative list, repeated everywhere.** The never-list goes into every
still prompt AND every motion prompt. Video models honor negatives less than image
models, so saying it twice is on purpose.

**8. Every fix becomes a rule.** When you correct something, run
`python3 -m mdk learn SERIES --stage image "every figure in frame is an animal, including the ones in the background"`.
It goes into `learned-rules.yaml`, and every future prompt and review includes it. The
pipeline gets better each episode instead of repeating mistakes.

## Cost per episode

With the default models (`google/gemini-2.5-flash` for text and review,
`google/gemini-2.5-flash-image` for stills, `google/veo-3.1-lite` at 720p with no
generated audio for clips) the dry run of the 7-shot example prints:

| Step | Estimate |
|---|---|
| Character reference sheets (one-time per series) | about $0.12 |
| Narration, ~560 characters | about $0.17 (depends on your ElevenLabs plan) |
| 7 stills + review + reroll allowance | about $0.38 |
| 7 clips, 4 to 6 seconds each | about $0.96 |
| **Total for a ~35 second episode** | **about $1.63** |

A full 55 to 59 second episode with 8 or 9 shots lands around **$2 to $3**. Rerolls and
rejected clips add to that, which is why stills get checked before any video is made.
Prices come live from OpenRouter's model lists, so run `python3 -m mdk estimate SERIES E01`
for today's numbers. Pricier video models (Kling, Veo 3.1 full) can run 3 to 10 times more.

## Writing an episode

`episodes/E01/script.md` can be structured or prose.

**Structured** (free, no model call): one section per shot.

```markdown
## Shot 2
visual: Young Moss in his oversized yellow raincoat stands at the bottom of a spiral stone staircase, a brass key in both paws.
location: lamp_room
characters: moss@young
camera: slow tilt up from Moss to the endless stairs above him
motion: Moss looks up and tightens his grip on the key
narration: [quietly] The old keeper left me one brass key... [wistful] and two hundred stairs.
```

**Prose:** write the episode as a short story and `storyboard` asks the text model to
cut it into shots with the same fields. Either way, code checks the result before
anything else runs (known characters and eras, numbers spelled out, under the time cap).

Narration tips: about 140 spoken words fits under 60 seconds. One to three audio tags
per line, plain words in brackets. Ellipses for pauses. Lines describe what is on screen
or the narrator's reflection, never dialogue the viewer cannot see. Listen to the take
once: if a tag word got spoken aloud, simplify that tag and run `voice` again.

## Captions

`assemble` writes an `.srt` and adds it to the MP4 as a selectable caption track
(`--captions soft`, the default). `--captions burn` burns them into the picture if your
ffmpeg was built with libass; otherwise it falls back to the soft track and says so.
Most people style captions in their editor or posting tool anyway; the `.srt` is ready
for that.

## If the video API wants a public image URL

Image-to-video needs the keyframe as the clip's first frame. The kit first sends it
inline as a base64 data URL. OpenRouter's image-to-video guide asks for a public HTTPS
URL, and whether every provider accepts inline images is **not yet confirmed**. If the
provider rejects the inline image, the kit retries with a URL from an upload command
you control. Set it in `.env`:

```bash
# {path} is the local still, {name} its file name. The LAST line printed must be the https URL.
MDK_UPLOAD_CMD=aws s3 cp {path} s3://my-bucket/mdk/{name} && echo https://my-bucket.s3.amazonaws.com/mdk/{name}
# Set MDK_UPLOAD_ALWAYS=1 to skip the inline attempt.
```

Any storage you own works (S3, Cloudflare R2, Google Cloud Storage, your own server).
Use a bucket or folder you are happy to make public, and clean it up after.

## Higgsfield backend (experimental)

Set `production.backend: higgsfield` in the bible to make stills and clips through the
[Higgsfield CLI](https://github.com/higgsfield-ai/cli) instead of OpenRouter
(`higgsfield auth login` first). It uses `nano_banana_pro` for stills and
`veo3_1_lite` for clips by default. This adapter has **not been through a paid
end-to-end run yet**. Cost estimates show Higgsfield credits from `higgsfield generate
cost` rather than dollars.

Without an OpenRouter key, the text steps change: write structured scripts (free to
parse), write the bible by hand, and review stills by eye with the contact sheet
(`approve --accept-failed` once you are happy).

## Using it with Claude Code

`CLAUDE.md` tells an agent how to drive the kit, and `.claude/skills/microdrama/` is a
skill for it. Open the folder in Claude Code and say "make episode 2 of my show".

## Layout

```
mdk/                  the pipeline (standard library Python)
templates/            show bible and episode script templates
examples/the-last-light/  the worked example (text only)
tests/                python3 -m unittest
```

Inside a series folder:

```
show-bible.yaml       creative truth
learned-rules.yaml    rules added with `learn`
refs/                 character reference images
episodes/E01/
  script.md           you write this
  storyboard.json     shots + narration
  vo/                 narration.mp3 + timing.json
  stills/             keyframes (+ earlier versions)
  review.json         automated check results
  contact-sheet.html  the storyboard gate
  clips/              clips + jobs.json (resume without paying twice)
  out/                E01.mp4 + E01.srt
```

## Tests

```bash
python3 -m unittest
```

They cover the pure parts: bible parsing, shot timing, ffmpeg commands, model parameter
checks against a captured model list, cost estimates, and a full dry run that must not
touch the network or write a file.

## License

MIT, copyright Dan Gauerke. See `LICENSE`.
