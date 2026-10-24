.PHONY: models models-verify synthetic synthetic-check sysmon
models:          ## download the pinned MLX weights (LOCAL_MODEL + tier1 + voice) into models/weights/hf
	uv run models/fetch.py
models-verify:   ## check downloaded weights against the pinned revisions
	uv run models/fetch.py --verify
synthetic:       ## (re)write the synthetic sudoku/accounting fixtures and their annotations
	python3 tools/synthetic/generate.py
synthetic-check: ## fail if the synthetic fixtures on disk differ from what the generator writes
	python3 tools/synthetic/generate.py --check
sysmon:          ## one-line memory/CPU check (use before loading models)
	python3 tools/sysmon.py
