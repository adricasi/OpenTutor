# UX principles and interaction design

These are the binding UX rules; changes are decided with the team and logged in `DECISIONS.md`. When in doubt, the test is: *would a good teacher do this with a red pen on the student's paper?*

## The one rule: the agent never touches your ink

The user's handwriting stays the only content on the page. Nothing is replaced, snapped, typeset or redrawn. Three layers:

1. **Ink** — the user's strokes. Only the user edits them.
2. **Tutor** — the agent's output. Marks and ghost ink anchored to the user's strokes. The user can erase, accept or dismiss, nothing else.
3. **Popovers / side panel** — explanations, hints, the voice transcript. Appear on tap or when the tutor speaks, then get out of the way.

The machine's internal reading (LaTeX, transcript, graph) is **not** shown by default. It surfaces in exactly two cases: a low-confidence chip ("I read this as ∂ — correct?") next to the stroke, and a debug pane the user opens on purpose ("show what you read").

## How the tutor marks

- **Marks:** ✓ / ✗ / ? in the margin beside a handwritten line; an underline under the exact strokes that are wrong; a circle around a box in a diagram; a numbered pin where something is missing. Small, colour-coded by severity, never text-heavy.
- **Ghost ink:** when the tutor writes (a corrected step, a missing component, a better phrasing) it writes *on the page, in its own hand*: a handwriting-style font, dashed or lighter, in a contrasting colour, placed in the margin or the empty space beneath the user's line. Visibly not the user's handwriting. Accept → it becomes solid; dismiss → gone; or the user rewrites it by hand and the ghost retires once the recognizer sees the fix.
- **Hint ladder:** tapping a mark opens level 1 (nudge: "check the sign"). "More" → level 2 (hint: "the integral of eˣ is…"), level 3 (the corrected step as ghost ink), level 4 (full solution, only on explicit request).
- **Questions before grades:** if the reading is uncertain, the tutor asks with a one-tap chip instead of marking.
- **Latency masking:** marks arrive a few seconds after pen-up with a subtle per-line "checking" shimmer; the page never freezes. New ink cancels pending feedback.

## Per subject

- **Maths:** marks per line; underline on the offending strokes (the math recognizer knows which strokes made which symbol); ghost corrected step beneath the user's line. Never typeset the user's work.
- **Diagrams (system design, consulting):** the tutor only *adds*: a dashed ghost box ("cache?") in the gap between API and DB, a dashed arrow, a pin on the single point of failure. Numbered pins map to a checklist in the panel. "Show reference design" opens a clean reference in a separate pane with a diff — never on the user's page.
- **Language:** squiggle under the misspelled word on the ink; corrected phrasing as ghost ink below; for CJK, stroke-order playback of the correct character beside the user's.
- **Accounting (T-accounts):** ✓ when debits = credits; ✗ on the entry on the wrong side; ghost note in the margin.
- **Sudoku / puzzles:** the tutor explains the *next deducible move*, never fills cells.

## Voice

- The tutor speaks short things unprompted ("careful with the sign there") and says what it is marking as it marks it. Detail stays in the popover.
- The user can interrupt (barge-in). "Why is this wrong?" resolves to the stroke under the pen.
- Speech is also the hint-ladder control: "just a hint", "show me the step".
- Voice is optional and off by default; a visible mic state tells the user when it listens.

## Modes and sessions

A session = one problem/task in one mode, with the problem statement the tutor is given and an optional rubric or reference answer. The provider badge (`local · 3.4 s`, `Claude · 1.9 s`) is always visible so it is obvious which brain answered.

## Screen tutor

On the Mac, over any app (`SCREEN-TUTOR.md`). One step at a time: a highlight on the real control, an arrow only when it is far from the mouse, one short spoken line (and the same line as a caption). The user does the clicking; when the step is detected as done, the tutor moves on by itself. If the user goes another way, it replans quietly and says so in one line. It never clicks, types or scrolls for them. Its only presence is a draggable phantom icon whose menu offers text input, listen on/off, the transcript, pause and exit; a visible indicator shows when the screen is being captured.

## Things we decided *not* to do

- No live typeset "twin" pane next to the ink (converting handwriting into machine output is explicitly not the experience).
- No tutor text written inline between the user's lines.
- No wholesale redraw or beautify of the user's diagram.
- No autosolving; the full solution is the last rung of the ladder and only on request.
