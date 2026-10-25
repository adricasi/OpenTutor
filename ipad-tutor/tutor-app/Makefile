# Ink Tutor app. Spec, fixtures and model weights live in ../ink-tutor.
.PHONY: dev server web protocol test test-py test-web e2e lint models judge build sysmon demo pair ipad overlay overlay-build overlay-test
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

# ---- screen tutor: the macOS overlay helper (apps/overlay, Swift/AppKit, SwiftPM; docs/SCREEN-TUTOR.md)
OVERLAY := apps/overlay
OVERLAY_APP := $(OVERLAY)/.build/InkTutorOverlay.app
# SwiftPM's caches and config stay inside the repo (no ~/Library/Caches/org.swift.swiftpm)
SWIFTPM := --cache-path .build/swiftpm/cache --config-path .build/swiftpm/config --security-path .build/swiftpm/security
SERVER ?= 127.0.0.1:8000
SESSION ?= mac
SWIFT_JOBS ?= 2
overlay-build:   ## build the helper (release), wrap it in a .app (Info.plist: LSUIElement, mic text), sign it ad hoc
	cd $(OVERLAY) && swift build -c release -j $(SWIFT_JOBS) --product inktutor-overlay $(SWIFTPM)
	rm -rf $(OVERLAY_APP) && mkdir -p $(OVERLAY_APP)/Contents/MacOS
	cp $(OVERLAY)/Info.plist $(OVERLAY_APP)/Contents/Info.plist
	cp $(OVERLAY)/.build/release/inktutor-overlay $(OVERLAY_APP)/Contents/MacOS/inktutor-overlay
	codesign --force --sign - --identifier dev.inktutor.overlay $(OVERLAY_APP)
	@echo "built $(OVERLAY_APP)"
overlay: overlay-build   ## build, sign and run the helper here (SERVER=127.0.0.1:8033 SESSION=mac ARGS="--log-ax --record DIR"); Ctrl-C quits
	$(OVERLAY_APP)/Contents/MacOS/inktutor-overlay --server $(SERVER) --session $(SESSION) $(ARGS)
overlay-test:    ## the helper's pure-logic self-check (coordinates, layout, overlay, status, AX walk, audio DSP, wire examples); no XCTest
	cd $(OVERLAY) && swift run -j $(SWIFT_JOBS) $(SWIFTPM) overlay-selftest
