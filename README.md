# OpenTutor

**A free, private homework tutor for a whole classroom, running on one laptop with no internet.**

OpenTutor turns one Linux laptop running **Gemma 4** locally into a homework tutor that
students reach from any phone browser over the laptop's own Wi-Fi: no app, no account, no
mobile data. Students photograph or type an exercise and get step-by-step hints (never the
answer unless they ask), checks of their own attempts and practice exercises. Nothing
leaves the hub: messages and photos live in memory and are deleted when the student taps
**Finish**.

Built for the students who need it most: families without home internet or prepaid data,
who can't pay for tutoring. Specification: [docs/spec](docs/spec/) · decisions:
[docs/adr](docs/adr/).

> `tutor-ipad/` in this repository is a separate product (an iPad handwriting tutor for
> Apple-silicon Macs) with its own README.

## How it works
```
phones (any browser) ──hub Wi-Fi, no internet──▶ laptop: OpenTutor web app ──▶ Ollama + Gemma 4 (127.0.0.1)
```
- **Hint first:** nudge → hint → next step → full solution only on request.
- **Photos:** Gemma 4 reads the worksheet; several exercises on one page → it asks which.
- **Subject tutors:** Maths, English language and Science, each a folder under
  `src/opentutor/tutors/` (`tutor.toml` + `rubric.md`). Add a subject by adding a folder.
- **Fair queue:** students see their place in line when the tutor is busy.
- **Design:** the student and teacher screens follow the team prototype in `prototype/`
  (served as plain pages: no framework, nothing loaded from the internet).
- **Read aloud:** each tutor reply has a 🔊 button that uses the phone's own on-device voice
  (hidden if the phone has none), so students who find reading hard can listen.

## Run a hub (operator guide)
Reference machine: Linux with NetworkManager, 64 GB RAM, a dedicated GPU, ~20 GB disk.

**With internet, once:**
1. Install [Ollama](https://ollama.com/download) and make sure it listens on loopback only
   (the default; never set `OLLAMA_HOST=0.0.0.0`). Keep `OLLAMA_DEBUG` off.
2. Install [uv](https://docs.astral.sh/uv/), then in this folder:
   ```bash
   uv sync
   uv run opentutor setup        # pulls the Gemma 4 model and pins its digest in opentutor.toml
   ```
   Choose another model with `OPENTUTOR_MODEL=gemma4:e2b uv run opentutor setup`.
   Gemma 4 is released under Google's Gemma terms — read them on the model card before use.

**Before each session (no internet needed):**
1. Create the hub Wi-Fi (replace `wlan0` and pick your own password):
   ```bash
   nmcli device wifi hotspot ifname wlan0 ssid OpenTutor password "choose-a-password"
   nmcli connection modify Hotspot 802-11-wireless.ap-isolation 1   # phones can't see each other (if supported)
   nmcli connection up Hotspot
   ```
   The laptop is usually `10.42.0.1` on its hotspot (`ip addr show wlan0` to check).
2. Allow only the tutor's port on the hotspot, e.g. `sudo ufw allow in on wlan0 to any port 8000 proto tcp`.
3. Turn swap off (`sudo swapoff -a`) or use encrypted swap, so photos are never paged to disk.
4. Start the hub:
   ```bash
   uv run opentutor start --host 10.42.0.1
   ```
   It checks Ollama and the model, refuses unsafe network settings, and prints a QR code.
5. Students join the **OpenTutor** Wi-Fi and scan the QR code. On Android, tap **"Stay
   connected"** when the phone warns that the network has no internet.

**Supervision checklist:** tell students not to use their full name; remind them the AI can
make mistakes; keep the laptop supervised; tap **Finish** on shared phones; restart the hub
at the end of the day (this clears everything).

## Develop
```bash
uv sync
uv run opentutor start --dev             # fake model, http://127.0.0.1:8000
uv run pytest --cov                      # tests (no network, no model needed)
RUN_E2E=1 uv run pytest tests/test_read_aloud.py   # browser tests (node + Playwright)
uv run ruff check --fix . && uv run ruff format .   # lint + format
uv run mypy src                          # type check
uv run pip-audit                         # dependency vulnerabilities
```
Configuration: `OPENTUTOR_*` environment variables or `opentutor.toml` (see
`src/opentutor/config.py`). Workflow: spec-driven (`docs/spec/`), one task per PR.

## License
MIT — see [LICENSE](LICENSE).
