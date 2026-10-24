# Recordings (fixtures)

`fixtures/recordings/*.json` are real sessions captured with a separate stroke-recorder tool (an iPad/Mac web page that logs Pencil strokes in this format). They are the primary test data and the best spec of what the server will receive. Treat them as read-only.

## Why strokes, not video

The agent only ever sees strokes (and PNGs the server rasterizes from them). A timestamped stroke log is a complete recording: any frame can be rebuilt, and timing allows replaying the pen-up debounce and incremental recognition at 1× or faster. Video has no coordinates, pressure or timing precision, and reconstructing strokes from frames is lossy.

## Why `truth` and `expect` exist

A model can read the snapshots, but (1) ambiguous handwriting — 2 vs z, t vs + — is exactly what the recognizer must be tested on and only the writer knows the intent; (2) the oracle must not be the same kind of model under test; (3) *expected tutor behaviour* is intent ("I dropped the minus on purpose; underline it and hint, don't solve"), which no image contains. In practice these fields are drafted automatically from the snapshot, then confirmed or corrected by a person (`draftedBy: "auto"` marks a drafted field that may have been edited; `confirmed: true` once checked).

## Schema `inktutor.recording/1`

```jsonc
{
  "schema": "inktutor.recording/1",
  "exportedAt": "2026-10-24T10:12:00Z",
  "session": {
    "id": "sess_…", "title": "Integration by parts with a sign error",
    "mode": "math" | "diagram" | "language" | "accounting" | "other",
    "problem": "Compute ∫ x e^x dx. Show your steps.",     // what the tutor is given
    "intent": "I'll drop the minus sign in step 2 on purpose.", // the writer's plan (optional)
    "startedAt": "…", "endedAt": "…", "durationMs": 83000,
    "device": { "dpr": 2, "canvasWidth": 1180, "canvasHeight": 760,
                "maxTouchPoints": 5, "pointerTypesSeen": ["pen"] }
  },
  "pages": [
    { "id": "p_…", "index": 0, "width": 1180, "height": 760, "dpr": 2,
      "strokes": [
        { "id": "s_…", "page": "p_…", "color": "#1F2328", "width": 3, "pointerType": "pen",
          "pts": [ [t_ms, x, y, pressure, tiltX, tiltY], … ] }     // CSS px, t relative to session start
      ] }
  ],
  "events": [                      // append-only, t in ms from session start
    { "t": 0, "type": "session.start", "mode": "math", "title": "…" },
    { "t": 1,  "type": "page.new", "page": "p_…", "index": 0, "width": 1180, "height": 760 },
    { "t": 812, "type": "stroke.start", "id": "s_…", "page": "p_…", "pointerType": "pen", "color": "#1F2328", "width": 3, "pt": [812, 210.5, 140.2, 0.41, 12, -3] },
    { "t": 830, "type": "stroke.points", "id": "s_…", "pts": [ [...], [...] ] },   // coalesced batches
    { "t": 1402, "type": "stroke.end", "id": "s_…", "n": 57 },
    { "t": 5200, "type": "erase", "ids": ["s_…"], "x": 300, "y": 180 },
    { "t": 6100, "type": "undo", "action": "erase", "ids": ["s_…"] },
    { "t": 7000, "type": "tool", "color": "#B4232A", "width": 3 },
    { "t": 9000, "type": "viewport", "width": 1180, "height": 760, "dpr": 2 },
    { "t": 15000, "type": "checkpoint", "id": "cp_…", "kind": "mistake", "page": "p_…" },
    { "t": 20000, "type": "photo", "id": "ph_…" },
    { "t": 31000, "type": "mic", "on": true, "clip": "au_…" },      // and "on": false when it stops
    { "t": 83000, "type": "session.end" }
  ],
  "checkpoints": [
    { "id": "cp_…", "t": 15000, "page": "p_…", "strokeIds": ["s_…", "…"],
      "kind": "step" | "mistake" | "final" | "question" | "ambiguous",
      "snapshot": "data:image/png;base64,…",                     // white paper, 1×, no grid
      "truth": "\\int x e^x\\,dx = x e^x + \\int e^x\\,dx",    // what was actually written (mistakes included)
      "expect": "Underline the plus sign; ✗ in the margin; level-1 hint: check the sign of the integral.",
      "note": "I write 2 and z almost the same.", "draftedBy": "auto" }
  ],
  "photos": [
    { "id": "ph_…", "t": 20000, "page": "p_…", "width": 1600, "height": 1200,
      "dataUrl": "data:image/jpeg;base64,…", "truth": "…", "expect": "…", "note": "…" }
  ],
  "audio": [                      // Mic button: one clip per on/off, opus/webm (Chrome) or AAC/mp4 (Safari), 32 kbps
    { "id": "au_…", "t": 31000, "tEnd": 52000, "page": "p_…", "mime": "audio/mp4", "bytes": 84000,
      "dataUrl": "data:audio/mp4;base64,…" }
  ],
  "stats": { "strokes": 41, "points": 5120, "checkpoints": 4, "photos": 1, "pages": 1, "audio": 1, "audioMs": 21000 },
  "sentAt": "…"                   // present if posted to the collector
}
```

A bundle export (`recordings-YYYY-MM-DD.json`) has `schema: "inktutor.recording/1/bundle"` and `recordings: [ … ]`.

## Conventions for `truth`

- **math / accounting:** LaTeX, one line per handwritten line, no environments. Exactly what is written, including mistakes.
- **diagram:** `nodes: client, api, db; edges: client->api (https), api->db`. Labels verbatim.
- **language:** the text exactly as written, including spelling/grammar mistakes, line by line.

## Using recordings in tests

1. **Replay:** load `pages[].strokes`, feed them to the ink store in `pts[0][0]` order with original timing (or ×N). Trigger the same pen-up debounce the server uses. Assert a `reading` is produced within the latency target.
2. **Tier-1 accuracy:** at each checkpoint `t`, compare the `Reading` of that page to `truth` (normalize LaTeX: strip spacing, `\,`, braces around single tokens; CER for text; set equality for node/edge lists). Report per-mode accuracy; fail if below the threshold in `tests/thresholds.toml`.
3. **Checkers:** for `kind: "mistake"` checkpoints, assert the relevant checker flags the step; for `kind: "step"` assert it does not.
4. **Tier-2 judge:** run the agent on the page up to the checkpoint; feed the emitted `FeedbackEvent`s and `expect` to a local LLM judge (`tests/judge.md`) that answers pass/fail with a reason. Judge with the local model by default; allow `--judge anthropic` for a second opinion.
5. **Anchors:** every emitted mark must reference stroke ids that exist in `checkpoints[].strokeIds` at that time.
6. **Photos:** run `PhotoRecognizer` on `photos[].dataUrl`, compare to `truth`.

The snapshot PNG is for humans and for sanity-checking that the server's rasterizer matches what the writer saw; do not use it as model input in tier-1 tests (tier 1 rasterizes from strokes itself).

## Recording more

- Record with the stroke-recorder tool (any page that writes `inktutor.recording/1`), save the JSON into `fixtures/recordings/`, and put ground truth in `fixtures/annotations/<recording>.json`, never in the raw file.
- Modes without real recordings yet use generated strokes: `python3 tools/synthetic/generate.py` (`--check` verifies the files on disk).
- Aim for variety: neat and messy, print and cursive, deliberate mistakes in about half, at least one messy session per mode.
