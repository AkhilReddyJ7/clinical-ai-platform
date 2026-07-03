.PHONY: up down build logs test fmt lint

up:
	docker compose up --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f api

test:
	uv run pytest

fmt:
	uv run ruff check --fix .
	uv run black .

lint:
	uv run ruff check .
