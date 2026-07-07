.PHONY: up down build test test-api test-ui test-integration lint scan onboard seed-db logs ps metrics

up:
	docker compose up -d --wait

down:
	docker compose down

build:
	docker compose build

test: test-api

test-api:
	cd services/api && python -m pytest tests/ -v

test-ui:
	cd services/ui && npm run test:e2e

test-integration:
	cd services/api && python -m pytest tests/test_integration_e2e.py -v -m integration

lint:
	pre-commit run --all-files

scan:
	trivy config . && checkov -d .

onboard:
	bash scripts/onboard.sh

seed-db:
	bash scripts/seed-dev-users.sh

logs:
	docker compose logs -f

ps:
	docker compose ps

metrics:
	curl -s http://localhost:8001/metrics
