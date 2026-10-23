# Learning library

Rohan's background and a running glossary of market concepts, so Claude knows what to teach
and what not to re-explain. **Check `INDEX.md` before explaining any market term.**

- `profile.md`: what Rohan already knows (and what they don't)
- `INDEX.md`: every concept, with status and one-line gist
- `concepts/<slug>.md`: one short note per concept

## How Claude maintains this
1. Before using market jargon, look it up in `INDEX.md`.
   - `taught`: use it freely; link the note if useful.
   - `new` or missing: explain it in plain terms on first use, anchored to probability or
     web-dev analogies, then add or update the note and the index row in the same commit.
2. If Rohan shows they already know something (or doesn't), update `profile.md` and the status.
3. Notes stay short: what it is, why it matters for us, one analogy, where it shows up in
   this repo. Math is welcome; Rohan reads it fluently.
4. **`taught` has a bar.** A concept is `taught` only after it was explained in detail in the
   final message of a turn (Rohan is not watching the screen mid-turn; passing explanations
   get missed). Write the note, mark it `new`, explain it at the end of the turn, then flip it.
