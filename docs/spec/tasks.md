# Tasks: opentutor

- Status: In review <!-- Draft | In review | Approved -->
- Last updated: 2026-10-24 (status after PRs #3–#9)

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked.
Order = implementation order. Each task is a small vertical slice (≤ ~1 day), references
the requirements it serves, and has an objective "Done when".

Conventions for every task:
- Tests are named after the AC they prove (`test_ac_001_4_...`); unit and API tests use the
  fake model backend; model-quality checks go through the evaluation command (T-014).
- Root checks pass: `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run pytest --cov`.
- Changes to `.github/workflows/` need the user's approval first (AGENTS.md).

## Milestone 1: Walking skeleton (riskiest first)
Goal: a student joins from a phone, picks a tutor, types a question and sees a streamed
hint from local Gemma 4 — through CI.

- [ ] T-001: Model spike on the hub laptop
  - Requirements: NFR-005, NFR-006, REQ-004, REQ-006, REQ-007 (feasibility); resolves Q-1, Q-3
  - Depends on: —
  - Scope: `spikes/model_spike.py` (stand-alone script, not shipped), `docs/spikes/2026-10-model.md`
  - Work: verify the Ollama tags for Gemma 4 E2B, E4B and 26B-A4B on the official Ollama
    library; on the hub, measure for each: image input works, JSON-schema `format` output
    validates against a Pydantic model, time to first token (text and photo, 1 and 5
    parallel requests, `OLLAMA_NUM_PARALLEL` 1/2/4), VRAM/RAM use; record GPU model and
    VRAM; check whether an analysis call before the first hint keeps photo latency ≤ 15 s;
    check the hotspot: access-point mode with 15 phones connected, client isolation
    (`802-11-wireless.ap-isolation`) and WPA3 support; measure restart time including
    model warm-up (NFR-004); confirm Ollama keeps request images out of its logs and disk
    with debug logging off.
  - Done when: the report lists measured numbers per model and a recommended default tag,
    digest and parallel setting; ADR-0004 and design.md updated with the outcome; Q-1 and
    Q-3 closed in product.md.

- [x] T-002: Application skeleton and green CI
  - Status: Done (#3, #4): checks pass locally; GitHub CI can't run until the repo has runners.
  - Requirements: NFR-012, REQ-012 (partial: start serving), NFR-001 (bind address)
  - Depends on: —
  - Scope: `pyproject.toml` (dependencies: fastapi, uvicorn, jinja2, python-multipart,
    pydantic-settings, ollama, pillow, sympy, segno — each checked for maintenance and
    license), `src/opentutor/{config.py,app.py,cli.py,model/}` with a fake model backend,
    `src/opentutor/web/{templates,static}`, `opentutor.toml` example; root ruff config
    excludes `ipad-tutor/` (it has its own toolchain); fix the template's lint error;
    coverage gate `--cov-fail-under=80` in `pyproject.toml`; base security from day one:
    security headers and CSP without inline scripts, `Origin` check, JSON log format
    without content, text-only rendering of model output and nicknames in the client.
  - Done when: `uv run opentutor start --dev` serves the join page on 127.0.0.1 with the
    fake backend; root checks pass locally and in CI (currently red on `main`: 5 lint
    errors, 3 files to reformat); tests prove the app never binds `0.0.0.0`, the CSP and
    headers are set, and log lines carry no message text.

- [x] T-003: Join, sessions and Finish
  - Status: Done (#3).
  - Requirements: REQ-001, REQ-011
  - Depends on: T-002
  - Scope: session store (in memory, 15-session cap, 30-min expiry sweep), cookie,
    join page, Finish action, per-address limits
  - Done when: tests for AC-001.1 … AC-001.7 and AC-011.1 … AC-011.4 pass; session IDs
    have ≥ 128 bits of entropy (unit test).

- [x] T-004: Subject tutor folders and subject list
  - Status: Done (#3).
  - Requirements: REQ-008, REQ-002
  - Depends on: T-002
  - Scope: tutor registry with Pydantic validation (`extra="forbid"`), fixed `topics`
    list per tutor, `tutors/maths`,
    `tutors/english-language`, `tutors/science` (first rubrics and examples), subject
    list page; pattern from `ipad-tutor` `modes.py` (design.md, Reuse)
  - Done when: tests for AC-008.1 … AC-008.6 and AC-002.1 … AC-002.3 pass.

- [~] T-005: Typed question with a streamed hint (walking skeleton end to end)
  - Status: Code done (#3); waiting on the manual run with Gemma 4 on a phone over the hotspot.
  - Requirements: REQ-003, REQ-005 (AC-005.1, AC-005.4, AC-005.6, AC-005.7 partial)
  - Depends on: T-001, T-003, T-004
  - Scope: `prompts/core.md` (hint ladder, simple English, untrusted-content rules),
    prompt builder with section merge (pattern from `ipad-tutor` `tutor/base.py`), Ollama
    model client with call limits (60 s first output, 120 s total, max length), `POST
    /api/messages` NDJSON stream, chat page that resumes on reload, quick actions, client
    stream reader
  - Done when: tests for AC-003.1 … AC-003.3 pass with the fake backend; a manual run on
    the hub shows a streamed nudge from Gemma 4 on a phone over the hotspot; CI green.

## Milestone 2: Core tutoring

- [x] T-006: Fair request queue and timeouts
  - Status: Done (#3, #12): AC-009.5 added — Finish or expiry removes the waiting request within one update.
  - Requirements: REQ-009, AC-003.3, NFR-004 (request isolation)
  - Depends on: T-005
  - Scope: FIFO queue with N workers, `queued` events, one in-flight request per
    session, 120-s queue and 60-s model timeouts
  - Done when: tests for AC-009.1 … AC-009.5 pass (simulated slow model).

- [~] T-007: Photo questions
  - Status: Done with the fake model (#3): in-memory uploads, EXIF fix, analysis notices; missing: a real 9 MB no-temp-file test and a real-phone run.
  - Requirements: REQ-004, REQ-015 (analysis field only), NFR-002 (images)
  - Depends on: T-005
  - Scope: camera/gallery input, client-side downscale, in-memory upload parsing with
    `Content-Length` and streaming caps, image pipeline (type/size checks,
    Pillow pixel limit, EXIF orientation fix and metadata removal, downscale), structured
    `ExerciseAnalysis` call, notices for unreadable/several exercises
  - Done when: tests for AC-004.1 … AC-004.6 pass, including tests that a 9 MB upload
    creates no temp file and no image bytes reach disk or logs; photo flow works on a real phone over plain HTTP.

- [x] T-008: Hint ladder, full solution and context budget
  - Status: Done (#3); reading level is measured by `opentutor eval`.
  - Requirements: REQ-005 (all ACs)
  - Depends on: T-005
  - Scope: hint levels 1–4, "Show the answer" with mistakes reminder, context budget
    keeping the exercise
  - Done when: tests for AC-005.1 … AC-005.7 pass (fake backend asserts the level sent to
    the prompt; reading level checked in T-014).

- [x] T-009: Attempt checking with structured verdicts
  - Status: Done (#3).
  - Requirements: REQ-006 (AC-006.1 … AC-006.5)
  - Depends on: T-008
  - Scope: `Verdict` schema, Ollama `format` call, one retry with the validation error,
    rendering, insights hook
  - Done when: tests for AC-006.1 … AC-006.5 pass.

- [x] T-010: Deterministic maths checker
  - Status: Done (#7).
  - Requirements: REQ-006 (AC-006.6 … AC-006.9), ADR-0007
  - Depends on: T-009
  - Scope: adapt `ipad-tutor` `checkers/math.py` (source named in a header comment):
    allow-list tokenizer → `parse_expr` (explicit `local_dict`, empty globals,
    `evaluate=False`), step equivalence, equation solutions, worker process with resource
    limits and a 2-s limit (kill and respawn), merge with the model explanation
  - Done when: tests for AC-006.6 … AC-006.10 pass, plus hostile-input tests (e.g.
    `__import__`, attribute access, huge exponents) that are rejected or time out without
    running code.

- [x] T-011: Practice sets
  - Status: Done (#3, #12): numbered answers ("1. x = 5") are checked one by one, Maths through the checker.
  - Requirements: REQ-007
  - Depends on: T-009, T-010
  - Scope: `PracticeSet` schema (exactly 3), generation with one retry, per-exercise
    checking (uses T-009/T-010)
  - Done when: tests for AC-007.1 … AC-007.5 pass.

- [~] T-012: Safety rules and safety set
  - Status: Rules and 32-prompt safety set done (#8); waiting on an eval run on the hub to meet NFR-009.
  - Requirements: REQ-010, NFR-009
  - Depends on: T-005
  - Scope: safety and injection sections of `prompts/core.md`, safety prompt set (≥ 30
    prompts incl. injection-in-photo cases), privacy notice and mistakes notice on pages
  - Done when: tests for AC-010.1 … AC-010.4 pass with the fake backend (rules present in
    every prompt, notices rendered); the safety set is in the evaluation harness (T-023)
    and meets NFR-009 on the hub.

- [~] T-023: Minimal evaluation harness (eval gate starts here)
  - Status: Harness done (#8); waiting on its first run on the hub.
  - Requirements: REQ-014 (AC-014.2, AC-014.4 partial), NFR-011
  - Depends on: T-008
  - Scope: `opentutor eval` with the hint-first scorer (normalised expected-answer match
    + judge rubric `evals/judge.md`, judge model named in the report, temperature 0),
    5 exercises per tutor, report file, exit code; pattern from `ipad-tutor` `judge.py`
  - Done when: the command runs on the hub and gates hint-first compliance; from this task
    on, prompt, tutor and model changes need a passing report (AGENTS.md).

## Milestone 3: Operations, quality and hackathon release

- [~] T-013: Setup and start commands, operator guide
  - Status: setup/start, digest pin, hotspot+loopback serving, operator README (#3, #4), AC-012.5 fallback, model and checker warm-up, model kept loaded (#12); left: tests for the printed QR/health messages and a run on the hub.
  - Requirements: REQ-012, NFR-001, NFR-007
  - Depends on: T-005
  - Scope: `opentutor setup` (deps check, `ollama pull`, digest record), `opentutor start`
    (runtime/model/digest checks, refuse if Ollama listens beyond loopback, bind to
    hotspot IP and loopback via pre-created sockets, warm-up, address + QR via `segno`),
    README operator guide (NetworkManager hotspot with client isolation and WPA3 where
    possible, firewall rule, swap off or encrypted, Ollama debug logging off, Android
    "no internet" prompt, offline USB copy, supervision checklist, Gemma license — Q-5)
  - Done when: tests for AC-012.1 … AC-012.7 pass (with fakes for Ollama and network
    interfaces).

- [ ] T-014: Evaluation set and `opentutor eval`
  - Requirements: REQ-014 (AC-014.1, AC-014.2, AC-014.4), NFR-009, NFR-011
  - Depends on: T-023, T-009, T-011, T-012
  - Scope: extend T-023 to ≥ 20 exercises per tutor (≥ 5 as images) with answers and
    sample attempts, cases tagged by AC (AC-014.5); scorers for verdict accuracy, reading
    level and the safety set; thresholds file; model tests skipped unless
    `RUN_MODEL_TESTS=1`
  - Done when: `opentutor eval` exits non-zero when an NFR-009 or NFR-011 target is
    missed, and a report on the hub meets them (prompt work continues under this task
    until it does).

- [ ] T-015: Benchmark and soak test
  - Requirements: AC-014.3, NFR-004, NFR-005, NFR-006
  - Depends on: T-006, T-007
  - Scope: `opentutor bench` (p50/p90 time to first reply for text and photo, 1 and 5
    simulated students), 2-hour soak with memory sampling
  - Done when: the report on the hub meets NFR-005 and NFR-006 and the soak meets NFR-004,
    or the gaps are filed as tasks.

- [~] T-016: Security hardening, privacy and offline verification
  - Status: `security-auditor` review done; its Blocker and Should-fix items fixed (#13). Left: offline checklist, outbound capture and port scan on the hub, and the review's remaining nits (cloudflared checksum at start, PIN-failure map pruning, checker lock timeout).
  - Requirements: NFR-001, NFR-002, NFR-003, NFR-008
  - Depends on: T-007, T-013
  - Scope: verification of what T-002…T-013 built: rotating log file, offline checklist
    (upstream disconnected, outbound capture filtered to the OpenTutor and Ollama
    processes), port scan from a second device, hotspot client isolation check
  - Done when: header/log/no-disk-write tests pass; the offline checklist and port scan
    results are recorded in `docs/spikes/` (or the release notes); `security-auditor`
    review has no open Blockers.

- [~] T-017: Accessibility, page weight and device test
  - Status: axe-core (WCAG 2.2 AA) reports no violations on all screens at 360 px, no horizontal scroll, join page about 64–110 KB, after the `ux-reviewer` fixes (#16). Left: the axe and Playwright run as a CI job (needs workflow approval) and the manual Android and iOS test.
  - Requirements: NFR-010, NFR-005 (page weight)
  - Depends on: T-007, T-008
  - Scope: Playwright end-to-end test at 320/360 px with axe checks and an assertion that
    no request leaves the hub origin; page-weight check (≤ 300 KB); CI job (needs
    approval to change `.github/workflows/`)
  - Done when: the end-to-end test passes in CI with no WCAG 2.2 AA violations;
    manual test on one Android and one iOS phone is recorded.

- [~] T-022: Hackathon submission
  - Status: Demo script and README done (#4); live demo with internet off and the video still to do.
  - Requirements: G-5 (demo), product success metric "Hackathon demo"
  - Depends on: T-005, T-007, T-008, T-009, T-013
  - Scope: demo script with pre-tested exercises, demo video, README polish (setup, demo,
    license, Gemma 4 usage), release tag prepared for the human
  - Done when: live demo runs with internet off; repo public with README and video link.

- [ ] T-024: Independent setup walkthrough
  - Requirements: G-5, product success metric "Setup time"
  - Depends on: T-013
  - Done when: a person outside the team sets up a hub from the README in ≤ 30 min and
    the README is fixed where they got stuck.

## Milestone 4: Should / Could

- [x] T-018: Teacher insights view
  - Status: Done (#5); typed exercises get a topic from a background classification call (#12).
  - Requirements: REQ-013
  - Depends on: T-009
  - Done when: tests for AC-013.1 … AC-013.5 pass.

- [ ] T-019: Hub status on the operator console
  - Requirements: REQ-016
  - Depends on: T-006, T-013
  - Done when: tests for AC-016.1 … AC-016.2 pass.

- [ ] T-020: Suggest the right subject tutor
  - Requirements: REQ-015
  - Depends on: T-007
  - Done when: test for AC-015.1 passes.

- [~] T-021: Optional online demo link
  - Status: Done with a fake cloudflared (#6); waiting on a manual run through a real tunnel to a phone on mobile data.
  - Requirements: REQ-017, ADR-0006
  - Depends on: T-013
  - Scope: adapt `ipad-tutor` `pairing.py` (`QuickTunnel`, `TunnelGate`, QR helpers) with
    the Linux `cloudflared` asset and checksum (`setup --online-demo`); `start
    --online-demo` with a dedicated 127.0.0.1:8001 listener, per-start in-memory token,
    session-lifetime cookie, teacher pages refused; on-page notice
  - Done when: tests for AC-017.1 … AC-017.7 pass (fake `cloudflared` process); a manual
    run streams a full reply through a real quick tunnel to a phone on mobile data.

## Hackathon cut [ASSUMPTION A-7]
Proposed for the 16:30 submission, in this order: T-001 (short: one model, text + photo
latency) → T-002 → T-003 → T-004 → T-005 → T-007 → T-008 → T-009 → T-013 (start command
and README only) → T-022. Two people can work in parallel from T-002 (worktree per
person). Everything else continues after the deadline.

- [~] T-025: Read tutor replies aloud
  - Requirements: REQ-018
  - Depends on: T-005
  - Scope: `web/static/app.js` (Read aloud / Stop control, on-device English voice
    selection, maths-to-words), styles; browser test with a stubbed `speechSynthesis`
  - Done when: the browser test for AC-018.1 … AC-018.5 passes; a manual check on one
    Android and one iOS phone confirms an on-device voice reads a reply with Wi-Fi only.

- [~] T-026: Integrate the iPad handwriting tutor
  - Requirements: ADR-0008 (companion service)
  - Depends on: —
  - Scope: root lint exclusion, references renamed to `ipad-tutor/`, README section with run
    steps and port plan, AGENTS.md and spec wording, ADR-0008
  - Done when: root checks pass with `ipad-tutor/` present; both apps documented.
  - Status: Done in the repo (#20). Left: a CI job for `ipad-tutor` (`make test`, `make lint`;
    workflow change needs approval) and a test of the shared Ollama endpoint on a Mac.

- [~] T-027: Write by hand
  - Requirements: REQ-019
  - Depends on: T-007, T-009, T-010
  - Scope: writing pad in the chat (pointer events, undo, clear), handwriting flag on
    `POST /api/messages`, `HandwritingReading` structured call before checking, `reading`
    stream event, tests
  - Done when: tests for AC-019.1 … AC-019.5 pass (API with the fake model; pad in a browser).
  - Status: Built (#21): API tests and a browser test (draw, undo, use, check) pass with the
    fake model. Left: real Gemma 4 handwriting reading on the hub and a real-tablet test.

- [~] T-028: Speak instead of typing; make Listen visible
  - Requirements: REQ-020, AC-018.3 (changed)
  - Depends on: T-005, T-025
  - Scope: Speak control in the composer (Web Speech API with keyboard-dictation fallback),
    first-use notice; Listen shown on every reply when speech synthesis exists
  - Done when: browser tests with stubbed speech APIs pass for AC-020.1 … AC-020.5 and AC-018.3.
  - Follow-up: on-hub speech-to-text so speaking works offline on every phone.
  - Status: Built (#22): browser tests with stubbed speech APIs pass (speech fills the box,
    blocked or missing recognition falls back to the keyboard microphone, Listen shown without an
    offline voice and never uses an online one). Left: real phones.

## Coverage check
| Requirement | Tasks |
|---|---|
| REQ-001 | T-003 |
| REQ-002 | T-004 |
| REQ-003 | T-005, T-006 |
| REQ-004 | T-001, T-007 |
| REQ-005 | T-005, T-008 |
| REQ-006 | T-001, T-009, T-010 |
| REQ-007 | T-001, T-011 |
| REQ-008 | T-004 |
| REQ-009 | T-006 |
| REQ-010 | T-012, T-014 |
| REQ-011 | T-003 |
| REQ-012 | T-002, T-013 |
| REQ-013 | T-018 |
| REQ-014 | T-023, T-014, T-015 |
| REQ-015 | T-007, T-020 |
| REQ-016 | T-019 |
| REQ-017 | T-021 |
| REQ-018 | T-025 |
| REQ-019 | T-027 |
| REQ-020 | T-028 |
| NFR-001 | T-002, T-013, T-016 (pip-audit and gitleaks already run in CI) |
| NFR-002 | T-007, T-016 |
| NFR-003 | T-016 |
| NFR-004 | T-006, T-015 |
| NFR-005 | T-001, T-015, T-017 |
| NFR-006 | T-001, T-015 |
| NFR-007 | T-013 |
| NFR-008 | T-016 |
| NFR-009 | T-012, T-013 (supervision checklist), T-014 |
| NFR-010 | T-017 |
| NFR-011 | T-023, T-014 |
| NFR-012 | T-002 (coverage gate) |
