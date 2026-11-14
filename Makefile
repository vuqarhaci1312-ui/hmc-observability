.PHONY: up down logs ps seed-demo smoke test migrate

PROFILE ?= quick

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

migrate:
	docker compose exec -T api alembic upgrade head

seed-demo:
	docker compose exec -T api python -m app.cli seed --profile $(PROFILE)

smoke:
	docker compose exec -T api python -m app.cli smoke

test:
	docker compose exec -T api pytest -q
