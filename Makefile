.PHONY: up down build logs test fmt lint migrate revision

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

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

fmt:
	uv run ruff check --fix .
	uv run black .

lint:
	uv run ruff check .
