# Product: opentutor

- Status: Draft <!-- Draft | In review | Approved -->
- Last updated: 2026-10-24
- Input: [idea brief](../discovery/idea-brief.md) (verdict Go) · [research notes](../discovery/research-notes.md)

## Problem
Students from low-income families often have no home internet or prepaid data, share an
old phone or have no computer, can't pay for tutoring, and may have parents who can't
help with homework. Cloud AI tutors need data, accounts and often subscriptions, so they
widen the gap: students who can pay get a tutor, those who can't get nothing. Offline
content libraries (Kolibri, RACHEL) give videos and exercises but no conversational help
with *this* exercise.

OpenTutor turns one laptop running Gemma 4 locally into a free, private homework tutor
for a whole group of students. They reach it from any phone browser over the hub's local
Wi-Fi, with zero mobile data and nothing to install.

## Users and personas
| Persona | Needs | Current pain |
|---|---|---|
| **Student** (secondary school, low-income family, cheap phone, no data) | Understand a homework exercise step by step, at no cost, without being handed the answer | Textbook, classmates or nothing; cloud tutors need data and accounts |
| **Hub operator** (teacher, librarian, NGO volunteer) | Give a whole group a tutor with one donated laptop; set it up without being a developer; choose which subject tutors are offered | Only static offline content; own time is the bottleneck |
| **Teacher** (may be the same person as the operator) | See which topics students asked about and where they struggled, without collecting personal data | No visibility into homework difficulties until the next class |
| **Contributor** (open-source developer, educator) | Add or improve a subject tutor or translate prompts without changing core code | — |

## Goals
- G-1: A student on the hub Wi-Fi gets tutoring on a homework exercise, from a photo or typed question, on a cheap phone browser with no install and zero mobile data.
- G-2: The tutor helps students learn rather than copy: hints first, checks the student's own attempt, gives the final answer only when asked, offers practice.
- G-3: Everything runs offline on one laptop with Gemma 4; no student data leaves the hub.
- G-4: Several students can use one hub at the same time with an acceptable wait.
- G-5: Operators can set up a hub, and contributors can add a subject tutor, from the documentation alone.

## Success metrics
| Metric | Target | How it is measured |
|---|---|---|
| Time to first visible hint, text question | ≤ 5 s (p90) on the hub laptop | Timed benchmark script against the running hub |
| Time to first visible hint, photo question | ≤ 15 s (p90) on the hub laptop | Same benchmark, image set |
| Concurrent students served | 5 active students without errors; up to 15 connected | Load test with simulated phone clients |
| Hint-first compliance | ≥ 95% of tutoring replies on the evaluation set don't reveal the final answer before the student asks | Evaluation set, ≥ 20 exercises per subject tutor |
| Attempt-check accuracy | ≥ 90% correct "right / wrong" verdicts on the evaluation set (maths) | Evaluation set with known answers |
| Offline operation | 0 outbound connections from the hub during a full session | Session run with upstream network disabled and outbound traffic monitored |
| Setup time | ≤ 30 min from a laptop with Ollama and the model already downloaded to students connected | Timed walkthrough of the README by someone outside the team |
| Hackathon demo | Live end-to-end demo with internet off, public repo, README and demo video by the deadline | Submission checklist |

## Scope
### In scope (this version)
- Hub web app served by the laptop on the local network; mobile-first pages that work in any modern phone browser.
- Student joins with a nickname only; no accounts.
- Ask by photo of a worksheet/textbook page or by typed text; the model reads the image.
- Subject tutors: **Maths**, **English language** and **Science** at launch, each with its own instructions, examples and checking behaviour on the same model; new subject tutors are added as configuration, not code [ASSUMPTION A-5].
- Hint-first tutoring: step-by-step hints, student attempts, feedback, final answer only on request.
- Attempt checking with a structured verdict.
- Practice: "give me 3 similar exercises" and checking the answers.
- Fairness under load: request queue with the student's position shown.
- Teacher view: aggregated topics and struggle points per subject, nickname-level only (Should).
- Operator setup: README, setup script, model download, starting the hub, connecting phones.
- Evaluation set and benchmark scripts that measure the metrics above.
- Open-source repository (MIT), demo video.

### Non-goals (explicitly out of scope)
- Accounts, passwords, student profiles or history kept beyond the hub's running session.
- Any cloud backend, cloud model API, telemetry or analytics.
- Native mobile apps.
- Voice input/output (ElevenLabs out of scope), payments (Plata out of scope).
- Languages other than English for the interface and tutoring in this version.
- Fine-tuning the model (roadmap: LoRA on teacher-approved hint dialogues).
- Hosting or integrating a content library (Kolibri, RACHEL) in this version.
- Grading or assessment for school records.
- Internet access for students through the hub.

## Constraints
- Deployment target: **Local / on-device** — one hub laptop runs the model and the web app; students' phones connect over the hub's local Wi-Fi (laptop hotspot or a local router); no cloud backend.
- Model: Gemma 4, run locally through Ollama (E4B default, E2B fallback); local Gemma 4 confirmed as eligible for the hackathon category.
- Hub hardware (reference machine): Linux laptop, 64 GB RAM, dedicated GPU (model unconfirmed, Q-1). Other operating systems are not supported in this version.
- Team: 3 people, Python. Stack from the repository: Python 3.12+, uv.
- Deadline: hackathon submission 2026-10-24 16:30 CEST (demo in English) [ASSUMPTION A-2]; the spec also covers work after the hackathon.
- Users are minors: no personal data beyond a self-chosen nickname; images held in memory only.
- License: MIT (already in the repository); public repository.
- Cost: zero running cost; no paid APIs.

## Glossary
| Term | Meaning |
|---|---|
| Hub | The laptop running the model and the web app, plus the local Wi-Fi it serves |
| Hub operator | The adult who sets up and runs a hub |
| Subject tutor | A named tutoring profile (instructions, examples, checking behaviour) for one subject, running on the shared model |
| Session | One student's conversation on the hub, identified by a nickname; ends when the student leaves or the hub restarts |
| Hint ladder | Progressively more specific hints before the final answer |
| Attempt check | The tutor's structured verdict on a student's own answer or step |
| Practice set | Exercises similar to the current one, generated by the tutor, with checking |
| Evaluation set | Curated exercises with expected answers used to measure tutor quality |

## Open questions
- [ ] Q-1: Exact GPU model and VRAM of the hub laptop? Decides the default model tag and how many requests run in parallel; to be settled by benchmarking on the real machine (see A-1).
- [x] Q-2: Hub laptop operating system → Linux.
- [ ] Q-3: Exact Ollama tags and sizes for Gemma 4 E2B/E4B (research sources disagree); verify on the official Ollama library.
- [ ] Q-4: Which subjects beyond Maths, English language and Science, if any, for this version?
- [ ] Q-5: Gemma 4 license terms and any terms operators must accept — check the official model card and record them in the README.

## Assumptions
<!-- Anything decided without confirmation. Mark in text as [ASSUMPTION A-n]. -->
- A-1: The hub GPU has at least 8 GB VRAM, enough to run E4B on the GPU; if benchmarks disagree, E2B becomes the default.
- A-2: The hackathon deadline in the brief (16:30 CEST today) still applies to the submission; "more time" means the spec is complete, not compressed.
- ~~A-3~~: Latency targets 5 s / 15 s — confirmed by the user, now in Success metrics.
- ~~A-4~~: Group size — decided by the user: 5 active, 15 connected.
- A-5: Subject tutors are defined as configuration files so operators and contributors can add one without code changes.
