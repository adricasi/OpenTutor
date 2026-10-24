# Idea brief: OpenTutor

- Status: Go <!-- Exploring | Validating | Go | Pivot | Parked | Killed -->
- Last updated: 2026-10-24
- One-liner: For students whose families can't afford internet or tutoring, OpenTutor turns one old laptop running Gemma 4 into a free, private homework tutor for a whole classroom, reachable from any cheap phone over local Wi-Fi with zero mobile data. Unlike cloud AI tutors (need data, subscriptions) or offline content libraries (no conversation), it tutors step by step, from a photo of the worksheet, fully offline.
- Context: hackathon, team of 3, deadline today 16:30 CEST. Target categories: Best Use of Gemma 4 and Best Open-Source AI Project. Plata and ElevenLabs out of scope. Demo in English.

## 1. Problem
Low-income students can't afford home internet or enough prepaid data, often share an old phone or have no computer, can't pay for tutoring, and their parents may not be able to help with homework. Cloud AI tutors widen the gap: those who can pay get a tutor, those who can't get nothing.
- Who: secondary-school students in low-income families, and the schools, libraries and NGOs that serve them.
- Frequency: every homework session.
- Workaround today: textbook, classmates, waiting for the next class; offline content libraries (Kolibri, RACHEL) with videos and exercises but no conversational help.

## 2. Users and customers
| Segment | Job to be done | Current alternative | Willingness to pay |
|---|---|---|---|
| Low-income student with a cheap phone, no data | "Help me understand this exercise without giving me the answer, at no cost" | Textbook, classmates, nothing | None: must be free |
| Teacher / NGO / library running the hub | Give a whole group a tutor with one old laptop; see who struggles with what | Kolibri / RACHEL content, own time | None (donated hardware, grants) |
| Hackathon judges | Real problem, Gemma 4 central, open source, runs with internet off | — | — |

## 3. Solution concept
- **Community hub**: one laptop runs Gemma 4 locally (Ollama) and a small web app; it opens a local Wi-Fi hotspot. Students join with any phone browser: no app install, no internet, no data.
- **Photo of the exercise**: student photographs a worksheet or textbook page; Gemma 4 (multimodal) reads it.
- **Subject tutors**: maths, science and English language profiles (own instructions, few-shot examples and checking behaviour) on the same Gemma 4 model.
- **Tutor, not answer machine**: explains step by step with hints first, checks the student's attempt, gives the answer only on request; simple language.
- **Practice**: "give me 3 similar exercises", checks the answers.
- **Teacher view** *(stretch)*: topics students asked about and where they struggled, no personal data beyond a nickname.

Core loop: join hub Wi-Fi → photo or question → hint → student tries → feedback → practice exercises.

## 4. Market and competition
Light research only (hackathon). See `research-notes.md`.
| Competitor / alternative | Strengths | Weaknesses | Pricing |
|---|---|---|---|
| Kolibri (Learning Equality) | Mature offline content platform, NGO reach | No generative tutor yet; prototyping local models, nothing broadly testable (forum) | Free, OSS |
| RACHEL | Offline content libraries | Static content, no conversation | Free / hardware kits |
| DIY Pi + Ollama tutors ("AI Learning Box", gemma2:2b) | Proves feasibility | Hobby build, text only, single user | Free |
| Cloud tutors (Khanmigo, ChatGPT, etc.) | Strong models | Need data/internet, accounts, often paid | Free–paid |

## 5. Business model
- Open source (license to choose: Apache-2.0 or MIT). Value: tutoring hours for students who have none, at the cost of one old laptop. Later distribution through NGOs, schools, libraries, Kolibri community.

## 6. Technical feasibility
- Architecture (local, no cloud): laptop → Ollama serving Gemma 4 (E4B, fallback E2B) → Python web app (FastAPI + mobile-first HTML, or Streamlit) bound to the LAN → laptop hotspot → phone browsers. Session state in memory/SQLite.
- Structured outputs: Pydantic schemas passed to Ollama's `format` (JSON schema) for answer checking, exercise generation and subject detection; Instructor optional for retries.
- Hard parts / unknowns:
  1. Latency of Gemma 4 E2B/E4B with images on a CPU laptop, with several students at once → benchmark first; queue requests; stream tokens.
  2. Model download (~9–10 GB reported for E4B) over hackathon Wi-Fi → pull now on two laptops; copy by USB.
  3. Small-model correctness on maths → hint-first prompt, pre-tested demo exercises, "check with your teacher" note.
- Data and privacy: minors → nothing leaves the hub; nickname only; no accounts, no telemetry; camera images kept in memory only.
- Effort: 3 people × ~3.5 h.

## 7. Personal fit
Python team, local open models: strong fit for Gemma 4 + Open-Source categories.

## 8. Risks and pre-mortem
| Risk | Likelihood | Impact | Mitigation / test |
|---|---|---|---|
| Too slow on demo laptop / with several phones | M | H | Benchmark E2B vs E4B first; streaming; request queue; demo with 2 phones |
| Model download slow | M | H | Start now on 2 machines; USB copy |
| Wrong explanation live | M | M | Pre-test demo exercises; hint-first design |
| Hotspot/LAN issues at venue | M | M | Fallback: phone joins laptop hotspot, or demo in laptop browser + video |
| Scope creep | H | H | MVP done by 14:45; stretch only after |

## 9. Scorecard (hackathon lens)
| Criterion | Weight | Score (1–5) | Why |
|---|---|---|---|
| Problem severity & frequency | 3 | 5 | Daily, real, equity gap growing with paid AI |
| Reachable users / market | 2 | 4 | Schools, libraries, NGOs, Kolibri community |
| Differentiation | 2 | 4 | Offline multimodal tutor shared by many phones; gap noted by Kolibri forum and research |
| Technical feasibility (for us) | 2 | 4 | Ollama + Python; latency is the unknown |
| Time & cost to MVP | 2 | 4 | ~3 h with 3 people, zero cost |
| Business viability | 2 | 2 | Non-profit OSS; fits the goal |
| Personal fit & learning | 2 | 5 | Local Gemma 4, multimodal |
| Risk (5 = low) | 1 | 3 | Hardware speed, venue network |
| **Weighted total (max 80)** | | **65** | |

## 10. Verdict and next steps
- Verdict: **Go** — real problem for people in need, Gemma 4 is central (local + multimodal), open source is essential to the solution.
- MVP (in): hub web app on LAN; subject tutors (maths, science, English); structured outputs for checking and exercises; photo or text question → hint-first explanation with local Gemma 4; check student's attempt; "3 similar exercises"; works with internet off; public repo, license, README with setup, demo video.
- Stretch: teacher view; simple languages switch; request queue with "you're #2"; cache of common answers.
- Out: accounts, cloud, native app, voice, Plata, fine-tuning (roadmap: LoRA on teacher-approved hint dialogues per subject).
- Kill criteria (demo): if E2B can't answer an image question in < ~30 s on any team laptop by 13:15, demo text-first and show image flow on the fastest laptop only.

## Open questions
- [ ] Q-1: Which laptop is the hub (RAM/GPU)?
- [ ] Q-2: Demo subject (maths suggested) — "English" = demo language or subject?
- [ ] Q-3: Gemma category rules: is local Gemma 4 (not via Gemini API) accepted? Check.
- [x] Q-4: Name: OpenTutor.

## Sources
See `research-notes.md`.
