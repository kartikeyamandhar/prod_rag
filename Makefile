.PHONY: setup lint test db-up db-down seed reset-corpus test-integration

setup:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

db-up:
	docker compose up -d --wait postgres

db-down:
	docker compose down

seed: reset-corpus

reset-corpus:
	uv run --env-file .env python -m probes.reset_corpus

test-integration:
	uv run --env-file .env pytest
