---
name: microdrama
description: Makes animated vertical micro-drama episodes with this kit (show bible, refs, storyboard, stills, review, clips, one-take narration, final edit). Triggers on make an episode, next episode, new series, micro-drama, storyboard this, animate my character.
---

# microdrama

Drive `python3 -m mdk` from the repo root to make one narrated 9:16 episode under 60
seconds at a time. The full agent rules are in `CLAUDE.md`; this is the short loop.

## Loop

1. Confirm the series folder and episode id with the user (for example `shows/my-show E02`).
2. If `episodes/EP/script.md` is missing, write it WITH the user in the structured
   format (see `templates/episode/script.md`). Keep it under ~140 spoken words.
3. `python3 -m mdk run SERIES EP --dry-run` and show the user the cost table.
4. On their go-ahead for that amount: `python3 -m mdk run SERIES EP --yes`.
   It stops at the storyboard gate.
5. Read every still in `episodes/EP/stills/` yourself, then show the user the contact
   sheet. Apply their decisions: `approve SERIES EP`, or
   `reject SERIES EP <shot> --note "..."` followed by `stills` and `review` again.
6. `python3 -m mdk run SERIES EP --yes` to make clips and the final cut. Extract a frame
   from each clip and look at it before reporting.
7. Every correction the user makes becomes `learn SERIES --stage <stage> "<rule>"`.

## Rules

- Never pass `--yes` for an amount the user has not agreed to.
- Never auto-approve the storyboard.
- One continuous narration take per episode; never split it into per-line TTS calls.
- When a still is mostly right, reject it with a note so it gets edited, not regenerated.

## Related

`CLAUDE.md`, `README.md`, `templates/show-bible.yaml`
