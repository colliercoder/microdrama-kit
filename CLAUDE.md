# Driving microdrama-kit as an agent

This repo makes animated vertical micro-drama episodes. You drive it with
`python3 -m mdk <command>` from the repo root. Read `README.md` once for the method.

## Ground rules

- **Never spend without the user's go-ahead.** Every paid step prints an estimate. Run
  `python3 -m mdk estimate SERIES EP` or `--dry-run` first, show the user the total, and
  only add `--yes` after they agree to that amount in this conversation.
- **Never skip the storyboard gate.** After stills and review, show the user
  `episodes/EP/contact-sheet.html` (or the stills themselves) and wait for their
  approve or reject per shot. Do not use `--auto-approve` or `approve --accept-failed`
  unless the user says so.
- **Keys live in `.env` only.** Never print them, never write them into any other file.
- **Look before you report.** After `stills`, `clips` or `assemble`, open the outputs
  (Read the PNGs; extract a frame from clips with ffmpeg) before telling the user it worked.

## Order of work for a new series

1. `init shows/NAME` then either `bible shows/NAME --premise "..."` or fill
   `show-bible.yaml` with the user. Get `voice.voice_id` from the user (ElevenLabs).
2. `refs shows/NAME`. Show the user every reference sheet. They can replace any with their own art.
3. Write `episodes/E01/script.md` with the user. Structured format (`## Shot N` with
   `visual:` `location:` `characters:` `camera:` `motion:` `narration:`) is free to parse.

## Order of work for an episode

`run` does this and resumes from whatever exists:

1. `storyboard` - fix any problems it lists in the script, then re-run.
2. `voice` - one continuous take. If it reports over the time cap, trim narration
   words in `storyboard.json` and run `voice --force`.
3. `stills`, then `review` (automatic rerolls).
4. Gate: user approves (`approve`) or rejects shots (`reject SERIES EP 03 --note "what to change"`,
   then `stills` again, which edits that one still).
5. `clips`, then watch them; `assemble`.

## When the user corrects something

Turn it into a rule so it never happens again:
`python3 -m mdk learn SERIES --stage image|video|voice|story "the rule, as an instruction"`.

## Narration rules

One continuous take per episode. 1 to 3 ElevenLabs v3 audio tags per line, inside the
line at emotional shifts. Numbers spelled out. Under about 140 spoken words for 60
seconds. Lines describe what is on screen. Last line is a door, not a summary.

## Tests

`python3 -m unittest` must pass before any commit.
