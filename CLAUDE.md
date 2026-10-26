# Working on this fork

- This is the user's fork of Tagcast (github.com/empyreanwolf/tagcast). Talk to the user
  in **English** (AGENTS.md's Dutch rule is for the original author). Keep replies short.
- Read `FORK.md` (versions, how the user runs it) and, for download work,
  `docs/downloads-plan.md` (agreed plan and build order). Architecture and rules:
  `AGENTS.md`.
- Every update: raise `artistCleanupVersion` in `static/artist-cleanup.js` (shown in
  the UI as "fork vN"), add a line to FORK.md's Versions, push to `main`, and give
  the user a zip built with
  `git archive --format=zip --prefix=tagcast-fork/ -o tagcast-fork-vN.zip HEAD -- . ':(exclude)docs/screenshots' ':(exclude)media/screenshots' ':(exclude)tools/gapless_lab'`.
- The user runs it on Windows with `start-tagcast-fork.bat` (port 8913) and updates by
  extracting the zip over the folder.
- Test with unit tests and one quick check; no full-library stress runs unless needed.
