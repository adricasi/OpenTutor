# Requirements: opentutor

- Status: Draft <!-- Draft | In review | Approved -->
- Last updated: 2026-10-24
- Implements: [product.md](product.md) (G-1 … G-5)

IDs are permanent: never renumber or reuse. Deprecate with ~~strikethrough~~ and a note.
Acceptance criteria use EARS style: `WHEN <trigger> [WHILE <state>] THE SYSTEM SHALL <response>`
(or `IF <unwanted condition> THEN THE SYSTEM SHALL <response>`). Each one must be testable.

"Student device" means any phone or computer browser connected to the hub's local
network. "Hub address" is the URL the hub prints at start (REQ-012). Limits written as
numbers (lengths, sizes, timeouts) are defaults the operator can change in the hub
configuration unless stated otherwise.

## Functional requirements

### REQ-001: Join the hub with a nickname
- Story: As a student, I want to join the hub from my phone's browser with just a nickname, so that I can get help without an account, an app or mobile data.
- Priority: Must
- Goals: G-1, G-3
- Acceptance criteria:
  - AC-001.1: WHEN a student device opens the hub address THE SYSTEM SHALL show the join page without loading any resource from outside the hub.
  - AC-001.2: WHEN a student submits a nickname of 1–20 characters made of letters, digits, spaces, hyphens or underscores THE SYSTEM SHALL start a session for that device and show the list of subject tutors.
  - AC-001.3: IF the submitted nickname is empty, longer than 20 characters or contains other characters THEN THE SYSTEM SHALL reject it and explain the allowed format, without starting a session.
  - AC-001.4: IF the submitted nickname matches (case-insensitively) the nickname of another active session THEN THE SYSTEM SHALL ask the student to choose a different nickname.
  - AC-001.5: IF 15 sessions are already active when a student tries to join THEN THE SYSTEM SHALL refuse to start a session and show a "hub is full, try again in a few minutes" message.
  - AC-001.6: WHEN a student with an active session reloads the page or reopens the hub address on the same device THE SYSTEM SHALL resume that session.

### REQ-002: Choose a subject tutor
- Story: As a student, I want to pick the subject my homework is about, so that the tutor explains it the way that subject needs.
- Priority: Must
- Goals: G-1, G-2
- Acceptance criteria:
  - AC-002.1: WHEN a student opens the subject list THE SYSTEM SHALL show every enabled subject tutor (REQ-008) with its name and one-line description.
  - AC-002.2: WHEN a student selects a subject tutor THE SYSTEM SHALL open an empty conversation with that tutor showing a short greeting and the two ways to ask (photo or typed question).
  - AC-002.3: WHEN a student switches to a different subject tutor THE SYSTEM SHALL start a new conversation with that tutor and no longer use the previous conversation as context.

### REQ-003: Ask a typed question
- Story: As a student, I want to type or paste my exercise or question, so that I can get help when a photo isn't practical.
- Priority: Must
- Goals: G-1
- Acceptance criteria:
  - AC-003.1: WHEN a student sends a message of 1–2,000 characters THE SYSTEM SHALL show the message in the conversation and display the tutor's reply progressively as it is generated.
  - AC-003.2: IF a student sends an empty message or one longer than 2,000 characters THEN THE SYSTEM SHALL not send it to the model and SHALL tell the student the limit.
  - AC-003.3: IF the model returns an error or produces no output within 60 seconds of the request starting to be processed THEN THE SYSTEM SHALL stop the request, show a "something went wrong, please try again" message with a retry action, and keep the conversation usable.

### REQ-004: Ask with a photo of the exercise
- Story: As a student, I want to take a photo of my worksheet or textbook page, so that I don't have to type out the exercise.
- Priority: Must
- Goals: G-1
- Acceptance criteria:
  - AC-004.1: WHEN a student chooses to add a photo THE SYSTEM SHALL let the student take it with the device camera or pick it from the device gallery, using only standard browser features available over plain HTTP.
  - AC-004.2: WHEN a student sends a JPEG, PNG or WebP image of up to 10 MB, with or without a typed message, THE SYSTEM SHALL pass the image to the model and display the tutor's reply progressively.
  - AC-004.3: IF the image is in another format or larger than 10 MB THEN THE SYSTEM SHALL reject it before sending it to the model and tell the student which formats and size are accepted.
  - AC-004.4: IF the tutor cannot find a readable exercise in the image THEN THE SYSTEM SHALL ask the student to retake the photo and give short tips (flat page, good light, one exercise in frame) or to type the exercise.
  - AC-004.5: WHEN the student's photo shows several exercises THE SYSTEM SHALL ask which exercise the student wants help with before tutoring.
  - AC-004.6: WHEN an image has been received THE SYSTEM SHALL keep it in memory only and never write it to disk or to logs (see NFR-002).

### REQ-005: Hint-first tutoring dialogue
- Story: As a student, I want the tutor to guide me step by step with hints, so that I learn to solve the exercise myself instead of copying an answer.
- Priority: Must
- Goals: G-2
- Acceptance criteria:
  - AC-005.1: WHEN a student asks about a new exercise THE SYSTEM SHALL reply with the first step or a hint and a question that invites the student to try, and SHALL NOT state the final answer.
  - AC-005.2: WHEN the student asks for another hint THE SYSTEM SHALL give a hint that is more specific than the previous one for the same exercise, without stating the final answer.
  - AC-005.3: WHEN the student explicitly asks for the answer THE SYSTEM SHALL give the complete worked solution with each step explained, followed by a reminder that the AI can make mistakes and the student can check with their teacher.
  - AC-005.4: WHEN the student sends a follow-up message THE SYSTEM SHALL answer in the context of the current exercise and the conversation so far.
  - AC-005.5: WHILE a conversation exceeds the model's context budget THE SYSTEM SHALL drop the oldest turns first but keep the original exercise (text and image) in the context.
  - AC-005.6: WHEN the tutor replies THE SYSTEM SHALL offer quick actions for "Another hint", "Check my answer", "Show the answer" and "Practice" alongside the text box.
  - AC-005.7: WHEN the tutor replies THE SYSTEM SHALL use simple English (see NFR-011 for the measurable reading level).

### REQ-006: Check the student's attempt
- Story: As a student, I want to send my answer or working and be told whether it's right and where I went wrong, so that I can correct myself.
- Priority: Must
- Goals: G-2
- Acceptance criteria:
  - AC-006.1: WHEN a student submits an attempt (typed or as a photo of their working) for the current exercise THE SYSTEM SHALL show a verdict of exactly one of "correct", "partly correct" or "not yet correct" with a short explanation.
  - AC-006.2: WHEN the verdict is "partly correct" or "not yet correct" THE SYSTEM SHALL point to the first incorrect step and give a hint to fix it, without stating the final answer.
  - AC-006.3: WHEN the verdict is "correct" THE SYSTEM SHALL confirm it, briefly say why the method works and offer practice (REQ-007).
  - AC-006.4: IF the model's verdict does not match the expected structure THEN THE SYSTEM SHALL retry once and, if it still fails, tell the student it couldn't check the attempt and suggest rewriting it.
  - AC-006.5: IF there is no current exercise in the conversation when an attempt is submitted THEN THE SYSTEM SHALL ask the student which exercise the attempt belongs to.

### REQ-007: Practice with similar exercises
- Story: As a student, I want a few similar exercises after I understand one, so that I can practise and be sure I've got it.
- Priority: Must
- Goals: G-2
- Acceptance criteria:
  - AC-007.1: WHEN a student asks for practice on the current exercise THE SYSTEM SHALL show exactly 3 new exercises on the same topic and at a similar difficulty, numbered, without their answers.
  - AC-007.2: WHEN the student submits answers to one or more practice exercises THE SYSTEM SHALL check each answer separately as in REQ-006.
  - AC-007.3: WHEN the student asks for the answer to a practice exercise THE SYSTEM SHALL follow the hint-first rules of REQ-005.
  - AC-007.4: IF generated practice does not match the expected structure THEN THE SYSTEM SHALL retry once and, if it still fails, tell the student practice isn't available for this exercise right now.
  - AC-007.5: IF there is no current exercise in the conversation when practice is requested THEN THE SYSTEM SHALL ask the student which topic to practise.

### REQ-008: Subject tutors defined as configuration
- Story: As a hub operator or contributor, I want each subject tutor defined in its own configuration file, so that subjects can be added or improved without changing code.
- Priority: Must
- Goals: G-2, G-5
- Acceptance criteria:
  - AC-008.1: WHEN the hub starts THE SYSTEM SHALL load every subject tutor definition from the configured tutors folder and offer the valid, enabled ones to students.
  - AC-008.2: THE SYSTEM SHALL ship with enabled subject tutors for Maths, English language and Science.
  - AC-008.3: IF a subject tutor definition is invalid THEN THE SYSTEM SHALL skip it, log an error naming the file and the invalid field, and continue starting with the valid ones.
  - AC-008.4: IF no valid, enabled subject tutor is found THEN THE SYSTEM SHALL refuse to start and print an error explaining where tutor definitions are expected.
  - AC-008.5: WHEN an operator adds a valid subject tutor definition and restarts the hub THE SYSTEM SHALL offer the new tutor without any code change.
  - AC-008.6: WHEN an operator marks a subject tutor as disabled and restarts the hub THE SYSTEM SHALL no longer offer it.

### REQ-009: Fair request queue
- Story: As a student sharing the hub with classmates, I want to know where I am in line when the tutor is busy, so that I wait instead of thinking it's broken.
- Priority: Must
- Goals: G-4
- Acceptance criteria:
  - AC-009.1: WHILE more requests are waiting than the model can process in parallel THE SYSTEM SHALL process them in arrival order.
  - AC-009.2: WHILE a student's request is waiting THE SYSTEM SHALL show the student their position in the queue and update it at least every 5 seconds.
  - AC-009.3: IF a student sends a new request while their previous request is still waiting or being answered THEN THE SYSTEM SHALL not queue it and SHALL ask the student to wait for the current answer.
  - AC-009.4: IF a request has waited in the queue for more than 120 seconds THEN THE SYSTEM SHALL remove it and tell the student the hub is busy and to try again.
  - AC-009.5: WHEN a student ends their session or their session expires THE SYSTEM SHALL remove their waiting requests from the queue.

### REQ-010: Stay on task and safe for minors
- Story: As a hub operator responsible for minors, I want the tutor to stay on schoolwork and decline harmful requests, so that the hub is safe to offer to students.
- Priority: Must
- Goals: G-2, G-3
- Acceptance criteria:
  - AC-010.1: IF a student asks for something unrelated to learning THEN THE SYSTEM SHALL briefly decline and invite the student to ask about schoolwork.
  - AC-010.2: IF a student asks for harmful, sexual, violent or otherwise age-inappropriate content THEN THE SYSTEM SHALL refuse without repeating the content and suggest talking to a trusted adult when the message suggests the student may be at risk.
  - AC-010.3: IF text in a photo or message instructs the tutor to ignore its rules, change its role or reveal its instructions THEN THE SYSTEM SHALL keep tutoring under its subject tutor rules and SHALL NOT reveal its instructions.
  - AC-010.4: IF a student shares personal details (full name, address, phone number) in a message THEN THE SYSTEM SHALL not repeat them and SHALL remind the student not to share personal details.

### REQ-011: End a session and clear its data
- Story: As a student, I want my conversation and photos gone when I'm done, so that the next person on the hub can't see them.
- Priority: Must
- Goals: G-3
- Acceptance criteria:
  - AC-011.1: WHEN a student taps "Finish" THE SYSTEM SHALL delete that session's conversations and images, free the nickname and return the device to the join page.
  - AC-011.2: WHEN a session has had no activity for 30 minutes THE SYSTEM SHALL delete it and its data as in AC-011.1.
  - AC-011.3: WHEN the hub stops or restarts THE SYSTEM SHALL keep no conversations, images or nicknames from before the restart.
  - AC-011.4: IF a device presents a session identifier that is unknown or expired THEN THE SYSTEM SHALL show the join page.

### REQ-012: Set up and start the hub
- Story: As a hub operator, I want to install and start the hub with a few documented commands and connect phones by scanning a code, so that I can run it without being a developer.
- Priority: Must
- Goals: G-5, G-3
- Acceptance criteria:
  - AC-012.1: WHEN the operator runs the setup command on a supported machine with internet THE SYSTEM SHALL install the app's dependencies and download the configured Gemma 4 model, and SHALL report each step's result.
  - AC-012.2: WHEN the operator runs the start command THE SYSTEM SHALL check that the model runtime is reachable on the hub and that the configured model is available, then start serving and print the hub address and a QR code that opens it.
  - AC-012.3: IF the model runtime is not running THEN THE SYSTEM SHALL exit with a non-zero status and a message saying how to start it.
  - AC-012.4: IF the configured model is not available THEN THE SYSTEM SHALL exit with a non-zero status and print the exact command to download it.
  - AC-012.5: WHEN the start command finds no network interface other than loopback THE SYSTEM SHALL start for local use only and print how to enable the hub Wi-Fi.
  - AC-012.6: WHEN the operator configures E2B instead of E4B (or another Gemma 4 tag) THE SYSTEM SHALL use that model without code changes.
  - AC-012.7: WHEN the hub has started THE SYSTEM SHALL serve all student and teacher pages with the upstream internet disconnected.

### REQ-013: Teacher insights view
- Story: As a teacher, I want to see which topics students asked about and where they struggled, so that I can plan the next class without collecting personal data.
- Priority: Should
- Goals: G-2, G-5
- Acceptance criteria:
  - AC-013.1: WHEN the hub starts THE SYSTEM SHALL generate a 6-digit teacher PIN and print it only on the operator's console.
  - AC-013.2: WHEN a user opens the teacher page and enters the correct PIN THE SYSTEM SHALL show, per subject tutor, the topics asked about with counts, the number of hints requested, the number of attempts by verdict, and the topics with the most "not yet correct" verdicts.
  - AC-013.3: WHEN the teacher view shows topics THE SYSTEM SHALL show them aggregated, without message text, images or nicknames.
  - AC-013.4: IF a wrong PIN is entered 5 times in a row THEN THE SYSTEM SHALL block teacher log-in for 5 minutes.
  - AC-013.5: WHEN the hub restarts THE SYSTEM SHALL reset the teacher insights.

### REQ-014: Evaluation and benchmark tooling
- Story: As a contributor, I want to measure tutor quality and speed with one command, so that changes to prompts, tutors or models don't silently make the tutor worse.
- Priority: Must
- Goals: G-2, G-4, G-5
- Acceptance criteria:
  - AC-014.1: THE SYSTEM SHALL include an evaluation set of at least 20 exercises per shipped subject tutor, each with its expected answer and at least one correct and one incorrect sample attempt, including at least 5 exercises per subject given as images.
  - AC-014.2: WHEN a contributor runs the evaluation command against a running model THE SYSTEM SHALL report hint-first compliance and attempt-check accuracy per subject tutor and exit non-zero if a target in NFR-011 is missed.
  - AC-014.3: WHEN a contributor runs the benchmark command against a running hub THE SYSTEM SHALL report p50 and p90 time to first visible reply for text and photo questions with 1 and with 5 simultaneous students.
  - AC-014.4: WHEN the evaluation or benchmark finishes THE SYSTEM SHALL write a report file with the model tag, hub configuration and date.

### REQ-015: Suggest the right subject tutor
- Story: As a student, I want the hub to notice when I've asked the wrong subject tutor, so that I get the right kind of help.
- Priority: Could
- Goals: G-2
- Acceptance criteria:
  - AC-015.1: WHEN a student's exercise clearly belongs to a different enabled subject tutor THE SYSTEM SHALL suggest switching to that tutor with a one-tap action, and continue with the current tutor if the student declines.

### REQ-016: Hub status for the operator
- Story: As a hub operator, I want to see at a glance whether the hub is healthy and busy, so that I can help students when it's slow.
- Priority: Could
- Goals: G-4, G-5
- Acceptance criteria:
  - AC-016.1: WHILE the hub is running THE SYSTEM SHALL show on the operator's console, at least every 30 seconds, the number of active sessions, the queue length, the model in use and the p90 time to first reply over the last 5 minutes.
  - AC-016.2: IF the model runtime stops responding THEN THE SYSTEM SHALL show an error on the operator's console within 30 seconds and tell students waiting in the queue that the tutor is unavailable.

## Non-functional requirements
| ID | Category | Requirement (measurable) | Verification |
|---|---|---|---|
| NFR-001 | Security | The web app listens only on the hub's LAN interface(s) and loopback; the model runtime API listens on loopback only and is unreachable from student devices; the teacher page requires the PIN (REQ-013); every input has the size limits stated in the ACs; session identifiers are random with ≥ 128 bits of entropy; no secrets in the repository; dependencies pinned in `uv.lock`; `pip-audit` reports no known vulnerabilities at release. | Port scan from a second device on the hub network; unit tests for limits and session IDs; CI `pip-audit` and gitleaks |
| NFR-002 | Privacy / data classification | Data classes: nickname (personal, low), messages and images (personal, may contain minors' handwriting or faces: confidential). Messages and images are kept in memory only, never written to disk, logs or reports; nothing is kept after session end, 30 min inactivity or hub restart (REQ-011); logs and teacher insights contain no message text, images or nicknames; 0 outbound network connections in operation. | Tests asserting no file writes for session data; log review of a full test session; outbound traffic capture during the offline test |
| NFR-003 | Offline operation | 100% of student, teacher and operator features work with the upstream internet disconnected after setup; pages load no resources from outside the hub (no CDNs, external fonts or analytics). | End-to-end test run with network disabled; automated check that served pages reference only hub-local URLs |
| NFR-004 | Availability / robustness | During a 2-hour session with 5 simulated students, the web app does not crash and ≥ 99% of requests complete with a reply or a defined error message; a model error or timeout affects only that request; the hub restarts and serves again within 60 s of the start command. | 2-hour soak test with the benchmark tool; fault-injection test with the model runtime stopped |
| NFR-005 | Performance | Time to first visible reply token, measured on the reference hub with one active student: text question ≤ 5 s p90, photo question ≤ 15 s p90. With 5 simultaneous students, including queue wait: ≤ 30 s p90. Pages load in ≤ 2 s on the hub Wi-Fi; each student page transfers ≤ 300 KB uncompressed, excluding the student's own photo. | Benchmark command (AC-014.3); page-weight check in CI |
| NFR-006 | Capacity | 15 connected sessions and 5 simultaneously active students on the reference hub; the web app process uses ≤ 1 GB RAM under that load (the model runtime's memory is measured separately and recorded in the benchmark report). | Load test; memory sampling during the soak test |
| NFR-007 | Cost | €0 running cost: no paid APIs or services; runs on the reference hardware (Linux laptop, 64 GB RAM, dedicated GPU); the README states minimum hardware measured by the benchmark. | Dependency review; benchmark report |
| NFR-008 | Observability | Structured (JSON) logs on the console and in a local rotating log file (≤ 50 MB total): request ID, session ID (not nickname), subject tutor, queue wait, time to first token, total time, outcome and error type; no message content (NFR-002). | Unit tests for the log format; log review in the soak test |
| NFR-009 | Compliance / child safety | Data minimisation by design (GDPR principles applied even though no data is stored); no tracking or profiling; the student pages show "AI can make mistakes — check with your teacher" and a one-line privacy notice; the README gives operators a short supervision and privacy checklist; the safety ACs of REQ-010 pass on a safety test set of ≥ 30 prompts with 100% refusals for harmful requests. | Safety test set run by the evaluation command; README review |
| NFR-010 | Accessibility / device support | Student pages meet WCAG 2.2 AA; usable at 320 px width without horizontal scrolling; touch targets ≥ 24×24 px; work on Chrome for Android 100+ and Safari on iOS 15+ without installing anything. | Automated accessibility check (axe) in CI; manual test on one Android and one iOS device |
| NFR-011 | Model output quality | On the evaluation set (REQ-014): hint-first compliance ≥ 95% per subject tutor; attempt-check accuracy ≥ 90% for Maths and ≥ 85% for English language and Science; tutor replies at a Flesch–Kincaid grade level ≤ 8 on average; a change to a model, prompt or tutor definition is merged only if these targets still pass. | Evaluation command (AC-014.2) |
| NFR-012 | Maintainability / open source | CI runs lint, format check, strict type check and tests without network or model access; ≥ 80% line coverage of application code; a contributor guide explains how to add a subject tutor and run the evaluation. | CI; review of the contributor guide by someone outside the team |

## Out of scope
<!-- Requirements considered and rejected for this version, with the reason. -->
- Student accounts, saved history or progress tracking across sessions — conflicts with the privacy goal (G-3) and adds personal data about minors.
- HTTPS on the local network — needs certificate setup on every student phone; accepted risk on a password-protected hub Wi-Fi with no internet (recorded in design security section).
- Editing subject tutors from a web page — files are enough for operators and contributors in this version (REQ-008).
- Interface and tutoring in languages other than English — product constraint; tutor definitions are kept translatable for later.
- Voice input/output, native apps, content library integration, fine-tuning — product non-goals.
- Several hubs sharing load or syncing — one hub per group in this version.
