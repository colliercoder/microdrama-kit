# E01 - "Episode Title"

Two ways to write this file:

1. Structured (free, no model call): keep the "## Shot N" sections below with
   `visual:` and `narration:` lines. `storyboard` parses them directly.
2. Prose: delete the shot sections and write the episode as a short story.
   `storyboard` asks the text model to cut it into shots (costs about a cent).

Narration rules: the whole episode is ONE continuous take, under 60 seconds
(about 140 spoken words). Put 1 to 3 audio tags like [weary] or [quietly] inside
each line at the emotional shifts. Spell out numbers. End on a door, not a summary.

characters: comma separated `id@era` from the show bible.

## Shot 1
visual: Wide shot. The hero stands alone at the edge of the place where the story starts, looking toward the camera.
location: home
characters: hero@present
camera: slow push-in from wide to medium
motion: wind moves the hero's coat, the hero breathes, nothing else moves
narration: [low] Everybody thinks they know how this started. [quietly] They don't.

## Shot 2
visual: The hero as a youngster, small in a big world, gripping a tool too large for them.
location: home
characters: hero@young
camera: locked-off medium shot
motion: the young hero tightens their grip and takes one step forward
narration: [warm] I was the smallest one there... [proud] and I grabbed the biggest tool on the wall.

## Shot 3
visual: The young hero at the far end of the frame, something ominous on the left edge of frame.
location: home
characters: hero@young
camera: slow pan left to reveal the threat
motion: dust drifts, the hero turns their head toward the threat
narration: [tense] Then, one night, everything went quiet. [whispering] Too quiet.
