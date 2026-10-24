# Ink Tutor app. Spec, fixtures and model weights live in ../ink-tutor.
.PHONY: dev server web protocol test test-py test-web e2e lint models judge build sysmon demo pair ipad
SPEC := ../ink-tutor

dev:             ## server (uvicorn --reload, :8000) + web (vite, :5173); the server manages the local model server
	@trap 'kill 0' INT TERM EXIT; \
	uv run uvicorn inktutor_server.app:create_app --factory --host 0.0.0.0 --port 8000 --reload --reload-dir apps/server/src --reload-dir packages/protocol/src & \
	(cd apps/web && npm run dev) & \
	wait
server:          ## server only, serving the built web app from apps/web/dist on :8000
	uv run uvicorn inktutor_server.app:create_app --factory --host 0.0.0.0 --port 8000
web:             ## vite dev server only
	cd apps/web && npm run dev
build:           ## production web build into apps/web/dist (served by `make server`)
	cd apps/web && npm run build
protocol:        ## pydantic → schema.json → TS types (run after changing packages/protocol)
	uv run python -m inktutor_protocol.export
	cd apps/web && npm run --silent gen:protocol
test: test-py test-web   ## pytest (server, protocol, fixture replays) + vitest
test-py:
	uv run pytest -q
test-web:
	cd apps/web && npm test --silent
e2e:             ## Playwright: draw a fixture into the canvas, compare with what the server stored
	cd apps/web && PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/../.tools/ms-playwright npx playwright test
lint:            ## ruff + mypy --strict (protocol, agent) + eslint + tsc
	uv run ruff check . && uv run ruff format --check .
	uv run mypy --strict packages/protocol/src apps/server/src/inktutor_server/agent
	cd apps/web && npm run --silent lint
models:          ## download/verify the pinned MLX weights (see ../ink-tutor/models/manifest.toml)
	cd $(SPEC) && uv run models/fetch.py
judge:           ## fixture judge for a local tier-2 model: make judge MODEL=gemma4-26b-a4b
	uv run python -m inktutor_server.judge --model $(MODEL)
sysmon:
	python3 $(SPEC)/tools/sysmon.py
demo: build      ## scripted session: server + a fixture replayed live into the canvas (ARGS="--speed 2 --pair")
	uv run python -m inktutor_server.demo $(ARGS)
ipad: build      ## the usual way in: server + HTTPS tunnel + QR in one terminal; Ctrl-C stops both
	@trap 'kill 0' INT TERM EXIT; \
	uv run uvicorn inktutor_server.app:create_app --factory --host 0.0.0.0 --port 8000 & \
	until curl -sf http://127.0.0.1:8000/healthz >/dev/null; do sleep 1; done; \
	uv run python -m inktutor_server.pair $(ARGS)
pair:            ## HTTPS Cloudflare quick tunnel + QR for the iPad (Safari's mic needs HTTPS); next to make server
	uv run python -m inktutor_server.pair $(ARGS)

