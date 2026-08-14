.PHONY: setup lint test db-up db-down

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
