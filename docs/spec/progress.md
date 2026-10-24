# Progress log: opentutor

Session handoff notes, newest last. Appended by `implement-task` and `/handoff`.
The latest entry is shown to Claude at the start of every session.

<!-- Entry format:
## YYYY-MM-DD — T-nnn: <title> (done | partial | blocked)
- Done: what was completed (files, tests)
- Decisions: choices made and why (link ADRs)
- Issues: problems found, spec questions raised
- Next: the exact next step
-->

## 2026-10-24 — Hackathon cut: T-002…T-013, T-018, T-021, T-023 (partial)
- Done: PRs #3–#9 merged. The hub runs end to end with the fake model: join, subjects,
  typed and photo questions, hint ladder, checks (sympy decides typed Maths), practice,
  queue, teacher view, setup/start commands, online demo link, `opentutor eval` with a
  32-prompt safety set. 165 tests pass, about 89 % coverage; Playwright run of the student
  flow at 360 px is clean after the fix in #9.
- Decisions: PRs merged by the agent at the user's request (deadline), arnauthedev
  requested as reviewer on each; code/security review deferred to the end.
- Issues: nothing has run against real Gemma 4, a phone or the hotspot yet; GitHub CI
  can't run (private repo without runners); cloudflared checksum is trust-on-first-use.
- Next: on the hub laptop, T-001 (`opentutor setup`, check the Gemma 4 tag, time a photo
  question) and the manual runs that close T-005, T-007, T-012, T-021, T-023; then the demo
  video (T-022); then the deferred code-reviewer and security-auditor pass.

## 2026-10-24 — Repository made public (done)
- Done: public-release review (no secrets, personal data or internal hosts in tracked files
  or history). With the repository public, GitHub Actions runners are available, so
  `ci-python` and `security` run on every push to `main` and every pull request. Every
  `ci-python` step passes locally: ruff format and lint, mypy, 207 tests, and `pip-audit`
  (no known vulnerabilities). README and hackathon checklist updated with that result.
- Issues: the earlier "GitHub CI can't run (private repo without runners)" note no longer
  applies. Commit author emails in history are now public.
- Next: unchanged from the entry above.
