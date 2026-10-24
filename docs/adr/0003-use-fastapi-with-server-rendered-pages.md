# 0003. Use FastAPI with server-rendered pages and vanilla JavaScript

- Status: Proposed
- Date: 2026-10-24
- Deciders: OpenTutor team

## Context
Students use cheap phone browsers over hub Wi-Fi. Pages must be light (≤ 300 KB,
NFR-005), fully local (NFR-003), accessible (NFR-010), stream replies (AC-003.1), show
queue positions (AC-009.2) and accept camera photos over plain HTTP (AC-004.1). The team
writes Python; the repository is already a Python/uv project.

## Options considered
1. **FastAPI + Jinja2 templates + one small vanilla JS/CSS bundle, replies streamed as
   NDJSON over `fetch`** — Pros: full control of page weight, accessibility, sessions and
   the queue; async fits streaming from the model; no front-end build step. Cons: we write
   the chat UI ourselves.
2. **Streamlit** — Pros: fastest to a first screen. Cons: multi-megabyte front end,
   per-user state and a fair shared queue are awkward, limited control of markup for
   accessibility, websocket reruns on weak Wi-Fi.
3. **Gradio** — Pros: built-in queue and image input. Cons: heavy front end, limited
   layout control, and its queue/session model would shape our product rules.
4. **Open WebUI on top of Ollama** — Pros: complete chat UI today. Cons: accounts, heavy,
   not hint-first or structured; little left for our open-source contribution.

## Decision
We will build the web app with FastAPI, server-rendered Jinja2 pages and a single
vanilla JavaScript file, streaming each reply as newline-delimited JSON events over a
`fetch` POST, because it is the only option that meets the page-weight, accessibility,
queue and privacy requirements while staying in Python.

## Consequences
- Positive: small pages; one event stream carries queue position, tokens, structured
  results and errors; easy to test with FastAPI's test client and Playwright.
- Negative / risks: more UI code to write → keep the UI to the screens in design.md;
  Safari/Chrome versions in NFR-010 must support streamed `fetch` bodies → covered by the
  device test.
- Follow-ups: the web app runs as a single process (ADR-0005).
