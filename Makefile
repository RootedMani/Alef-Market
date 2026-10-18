# Everyday commands. Run `make check` before every push: it is exactly what CI runs.
.PHONY: setup dev test lint fmt check reset-demo persona synthetic-market fp-report

setup:            ## install Python 3.12 + all dependencies into .venv
	uv sync

dev:              ## run the app with auto-reload on http://127.0.0.1:8000
	uv run uvicorn --factory app.main:create_app --reload --host 127.0.0.1 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:              ## auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

check: lint test

reset-demo:       ## rebuild the local DB with Sara's seeded state
	uv run python -m scripts.reset_demo

persona:          ## regenerate Sara from the real seed files (pass DEMO_TS=<ms> for the demo moment)
	uv run python -m scripts.generate_persona $(if $(DEMO_TS),--demo-entry-ts $(DEMO_TS))

synthetic-market: ## stand-in prices for local runs (gitignored): then ALEF_REPLAY_EXCHANGE=synthetic
	uv run python -m scripts.make_synthetic_market

fp-report:        ## false-card rate on 1,000 control datasets + Sara detection rate (~3 min)
	uv run python -m scripts.measure_false_positives --control 1000 --sara 60
