# Tasks: opentutor

- Status: In review <!-- Draft | In review | Approved -->
- Last updated: 2026-10-24

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
    VRAM; check whether an analysis call before the first hint keeps photo latency ≤ 15 s.
  - Done when: the report lists measured numbers per model and a recommended default tag,
    digest and parallel setting; ADR-0004 and design.md updated with the outcome; Q-1 and
    Q-3 closed in product.md.

- [ ] T-002: Application skeleton and green CI
  - Requirements: NFR-012, REQ-012 (partial: start serving), NFR-001 (bind address)
  - Depends on: —
  - Scope: `pyproject.toml` (dependencies: fastapi, uvicorn, jinja2, python-multipart,
    pydantic-settings, ollama, pillow, sympy, segno — each checked for maintenance and
    license), `src/opentutor/{config.py,app.py,cli.py,model/}` with a fake model backend,
    `src/opentutor/web/{templates,static}`, `opentutor.toml` example; root ruff config
    excludes `tutor-ipad/` (it has its own toolchain); fix the template's lint error.
  - Done when: `uv run opentutor start --dev` serves the join page on 127.0.0.1 with the
    fake backend; root checks pass locally and in CI (currently red on `main`: 5 lint
    errors, 3 files to reformat); a test proves the app never binds `0.0.0.0`.

- [ ] T-003: Join, sessions and Finish
  - Requirements: REQ-001, REQ-011
  - Depends on: T-002
  - Scope: session store (in memory, 15-session cap, 30-min expiry sweep), cookie,
    join page, Finish action
  - Done when: tests for AC-001.1 … AC-001.6 and AC-011.1 … AC-011.4 pass; session IDs
    have ≥ 128 bits of entropy (unit test).

- [ ] T-004: Subject tutor folders and subject list
  - Requirements: REQ-008, REQ-002
  - Depends on: T-002
  - Scope: tutor registry with Pydantic validation (`extra="forbid"`), `tutors/maths`,
    `tutors/english-language`, `tutors/science` (first rubrics and examples), subject
    list page; pattern from `tutor-ipad` `modes.py` (design.md, Reuse)
  - Done when: tests for AC-008.1 … AC-008.6 and AC-002.1 … AC-002.3 pass.

- [ ] T-005: Typed question with a streamed hint (walking skeleton end to end)
  - Requirements: REQ-003, REQ-005 (AC-005.1, AC-005.4, AC-005.6, AC-005.7 partial)
  - Depends on: T-001, T-003, T-004
  - Scope: `prompts/core.md` (hint ladder, simple English, untrusted-content rules),
    prompt builder with section merge (pattern from `tutor-ipad` `tutor/base.py`), Ollama
    model client, `POST /api/messages` NDJSON stream, chat page with quick actions,
    client stream reader
  - Done when: tests for AC-003.1 … AC-003.3 pass with the fake backend; a manual run on
    the hub shows a streamed nudge from Gemma 4 on a phone over the hotspot; CI green.

## Milestone 2: Core tutoring

- [ ] T-006: Fair request queue and timeouts
  - Requirements: REQ-009, AC-003.3, NFR-004 (request isolation)
  - Depends on: T-005
  - Scope: FIFO queue with N workers, `queued` events, one in-flight request per
    session, 120-s queue and 60-s model timeouts
  - Done when: tests for AC-009.1 … AC-009.5 pass (simulated slow model).

- [ ] T-007: Photo questions
  - Requirements: REQ-004, REQ-015 (analysis field only), NFR-002 (images)
  - Depends on: T-005
  - Scope: camera/gallery input, client-side downscale, image pipeline (type/size checks,
    Pillow pixel limit, EXIF orientation fix and metadata removal, downscale), structured
    `ExerciseAnalysis` call, notices for unreadable/several exercises
  - Done when: tests for AC-004.1 … AC-004.6 pass, including a test that no image bytes
    reach disk or logs; photo flow works on a real phone over plain HTTP.

- [ ] T-008: Hint ladder, full solution and context budget
  - Requirements: REQ-005 (all ACs)
  - Depends on: T-005
  - Scope: hint levels 1–4, "Show the answer" with mistakes reminder, context budget
    keeping the exercise
  - Done when: tests for AC-005.1 … AC-005.7 pass (fake backend asserts the level sent to
    the prompt; reading level checked in T-014).

- [ ] T-009: Attempt checking with structured verdicts
  - Requirements: REQ-006 (AC-006.1 … AC-006.5)
  - Depends on: T-008
  - Scope: `Verdict` schema, Ollama `format` call, one retry with the validation error,
    rendering, insights hook
  - Done when: tests for AC-006.1 … AC-006.5 pass.

- [ ] T-010: Deterministic maths checker
  - Requirements: REQ-006 (AC-006.6 … AC-006.9), ADR-0007
  - Depends on: T-009
  - Scope: adapt `tutor-ipad` `checkers/math.py` (source named in a header comment):
    allow-list tokenizer → `parse_expr`, step equivalence, equation solutions, worker
    process with 2-s limit, merge with the model explanation
  - Done when: tests for AC-006.6 … AC-006.9 pass, plus hostile-input tests (e.g.
    `__import__`, attribute access, huge exponents) that are rejected or time out without
    running code.

- [ ] T-011: Practice sets
  - Requirements: REQ-007
  - Depends on: T-009
  - Scope: `PracticeSet` schema (exactly 3), generation with one retry, per-exercise
    checking (uses T-009/T-010)
  - Done when: tests for AC-007.1 … AC-007.5 pass.

- [ ] T-012: Safety rules and safety set
  - Requirements: REQ-010, NFR-009
  - Depends on: T-005
  - Scope: safety and injection sections of `prompts/core.md`, safety prompt set (≥ 30
    prompts incl. injection-in-photo cases), privacy notice and mistakes notice on pages
  - Done when: tests for AC-010.1 … AC-010.4 pass with the fake backend (rules present in
    every prompt, notices rendered); the safety set runs in T-014.

## Milestone 3: Operations, quality and hackathon release

- [ ] T-013: Setup and start commands, operator guide
  - Requirements: REQ-012, NFR-001, NFR-007
  - Depends on: T-005
  - Scope: `opentutor setup` (deps check, `ollama pull`, digest record), `opentutor start`
    (runtime/model/digest checks, refuse if Ollama listens beyond loopback, bind to
    hotspot IP, warm-up, address + QR via `segno`), README operator guide (NetworkManager
    hotspot, firewall rule, offline USB copy, Gemma license — Q-5)
  - Done when: tests for AC-012.1 … AC-012.7 pass (with fakes for Ollama and network
    interfaces); a person outside the team sets up a hub from the README in ≤ 30 min.

- [ ] T-014: Evaluation set and `opentutor eval`
  - Requirements: REQ-014 (AC-014.1, AC-014.2, AC-014.4), NFR-009, NFR-011
  - Depends on: T-009, T-011, T-012
  - Scope: ≥ 20 exercises per tutor (≥ 5 as images) with answers and sample attempts;
    scorers for hint-first compliance, verdict accuracy, reading level and safety
    refusals; thresholds file; report files; model tests skipped unless
    `RUN_MODEL_TESTS=1` (pattern from `tutor-ipad` `judge.py`)
  - Done when: `opentutor eval` runs on the hub, writes a report and exits non-zero when
    a target is missed; the first report meets NFR-011 or the gaps are filed as tasks.

- [ ] T-015: Benchmark and soak test
  - Requirements: AC-014.3, NFR-004, NFR-005, NFR-006
  - Depends on: T-006, T-007
  - Scope: `opentutor bench` (p50/p90 time to first reply for text and photo, 1 and 5
    simulated students), 2-hour soak with memory sampling
  - Done when: the report on the hub meets NFR-005 and NFR-006 and the soak meets NFR-004,
    or the gaps are filed as tasks.

- [ ] T-016: Security hardening, privacy and offline verification
  - Requirements: NFR-001, NFR-002, NFR-003, NFR-008
  - Depends on: T-007, T-013
  - Scope: security headers and CSP, `Origin` check, JSON log format without content,
    rotating log file, offline checklist (upstream disconnected, outbound capture), port
    scan from a second device
  - Done when: header/log/no-disk-write tests pass; the offline checklist and port scan
    results are recorded in `docs/spikes/` (or the release notes); `security-auditor`
    review has no open Blockers.

- [ ] T-017: Accessibility, page weight and device test
  - Requirements: NFR-010, NFR-005 (page weight)
  - Depends on: T-007, T-008
  - Scope: Playwright end-to-end test at 320/360 px with axe checks and an assertion that
    no request leaves the hub origin; page-weight check (≤ 300 KB); CI job (needs
    approval to change `.github/workflows/`)
  - Done when: the end-to-end test passes in CI with no WCAG 2.2 AA violations;
    manual test on one Android and one iOS phone is recorded.

- [ ] T-022: Hackathon submission
  - Requirements: G-5 (demo), product success metric "Hackathon demo"
  - Depends on: T-005, T-007, T-008, T-009, T-013
  - Scope: demo script with pre-tested exercises, demo video, README polish (setup, demo,
    license, Gemma 4 usage), release tag prepared for the human
  - Done when: live demo runs with internet off; repo public with README and video link.

## Milestone 4: Should / Could

- [ ] T-018: Teacher insights view
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

- [ ] T-021: Optional online demo link
  - Requirements: REQ-017, ADR-0006
  - Depends on: T-013
  - Scope: adapt `tutor-ipad` `pairing.py` (`QuickTunnel`, `TunnelGate`, QR helpers) with
    the Linux `cloudflared` asset and checksum; `start --online-demo`, `--new-token`;
    tunnel requests treated as remote; on-page notice
  - Done when: tests for AC-017.1 … AC-017.7 pass (fake `cloudflared` process); a manual
    run streams a full reply through a real quick tunnel to a phone on mobile data.

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
| REQ-014 | T-014, T-015 |
| REQ-015 | T-007, T-020 |
| REQ-016 | T-019 |
| REQ-017 | T-021 |
| NFR-001 | T-002, T-013, T-016 |
| NFR-002 | T-007, T-016 |
| NFR-003 | T-016 |
| NFR-004 | T-006, T-015 |
| NFR-005 | T-001, T-015, T-017 |
| NFR-006 | T-001, T-015 |
| NFR-007 | T-013 |
| NFR-008 | T-016 |
| NFR-009 | T-012, T-014 |
| NFR-010 | T-017 |
| NFR-011 | T-014 |
| NFR-012 | T-002 |
