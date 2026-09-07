.PHONY: install dev test lint typecheck demo benchmark sandbox-build

PYTHON ?= uv run python
UV ?= uv

install:
	$(UV) sync --all-extras

dev:
	$(UV) run uvicorn app.api.app:app --reload

test:
	$(UV) run pytest -q --cov=app

lint:
	$(UV) run ruff check app tests benchmark

typecheck:
	$(UV) run mypy app

sandbox-build:
	docker build -f docker/Dockerfile.sandbox -t ci-triage-sandbox:dev .

demo:
	$(PYTHON) -m app.cli demo

benchmark:
	$(PYTHON) -m benchmark.run
