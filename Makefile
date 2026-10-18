# Everyday commands. Run `make check` before every push: it is exactly what CI runs.
.PHONY: setup dev test lint fmt check reset-demo

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
