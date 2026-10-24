# Hackathon minimum standard: where each item is met

Checklist shown at the hackathon ("The minimum standard", before 16:30). Each line points to
the evidence in this repository. Items marked **open** are not done yet.

## In the repo
| Item | Status | Evidence |
|---|---|---|
| An open-source LICENSE | Done | [LICENSE](../../LICENSE) (MIT) |
| Keys only in `.env`, and `.env` in `.gitignore` | Done | The app needs no API keys. `.gitignore` excludes `.env` and `.env.*` and keeps `.env.example`; gitleaks runs in [pre-commit](../../.pre-commit-config.yaml) and in [security.yml](../../.github/workflows/security.yml). |
| No real personal data in tests or examples | Done | Tests, evals and tutor examples use invented text. The only phone-number-like string is a fake one in `tests/test_evaluation.py`. Photos are never stored. |
| Pinned dependencies, no critical vulnerabilities | Partly | Versions are locked in `uv.lock`. `pip-audit` runs in CI and in the README commands. **Open:** run `uv run pip-audit` and paste the result here (it was not run when this file was written). |
| A clear error when the model API fails, and a rate limit on a public demo | Done | Model errors show "Something went wrong, please try again." (`app.py`). Join limits: 15 sessions, 2 per device, 10 attempts per minute (`sessions.py`). The online demo link is token-gated (ADR-0006). |
| A disclaimer if you give legal, tax, medical or money advice | Done | The tutor gives homework help only. Every page shows "AI can make mistakes — check with your teacher" (`base.html`); the README states that it gives no legal, tax, medical or financial advice. |

## In the README
| Item | Status | Where |
|---|---|---|
| What it is and who it's for | Done | Top of [README](../../README.md) |
| How to run it | Done | "Run a hub (operator guide)" and "Develop" |
| What expertise is inside, and where it comes from | Done | "What expertise is inside, and where it comes from" |
| The model and a link to its license | Done | "The model". **Open:** confirm the licence wording on the official Gemma 4 model card. |
| Proof that it works better with your part | **Open** | Targets and method are in "Proof that it works better with our part". The measured results with real Gemma 4 are still to be run on the hub laptop and filled in. |

## Not covered by the checklist but needed for the demo
- Demo video link: add it to the README once it is on YouTube.
- Demo script: [docs/demo.md](../demo.md).
