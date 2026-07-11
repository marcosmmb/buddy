COMPOSE ?= docker compose
UV ?= uv
VERSION ?= $(shell $(UV) version --short)
DOCKER_IMAGE ?= marcosmmb/buddy
DATABASE_URL ?= postgresql+psycopg://buddy:buddy@localhost:5432/buddy
ADMIN_EMAIL ?= admin@buddy.local
SPREADSHEET ?= /tmp/monthly_expenses.xlsx

.PHONY: sync up down restart build docker-image publish logs ps test smoke import-monthly import-monthly-replace db-shell app-shell

sync:
	$(UV) sync

up:
	APP_VERSION=$(VERSION) $(COMPOSE) up -d --build

build:
	APP_VERSION=$(VERSION) $(COMPOSE) build

docker-image:
	docker build --build-arg APP_VERSION=$(VERSION) --tag $(DOCKER_IMAGE):$(VERSION) --tag $(DOCKER_IMAGE):latest .

publish: docker-image
	docker push $(DOCKER_IMAGE):$(VERSION)
	docker push $(DOCKER_IMAGE):latest

down:
	$(COMPOSE) down

restart: down up

logs:
	$(COMPOSE) logs -f app

ps:
	$(COMPOSE) ps

test:
	LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD=0 $(UV) run python -m unittest discover -s tests

smoke:
	DATABASE_URL="$(DATABASE_URL)" LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD=0 $(UV) run python scripts/smoke_test.py

import-monthly:
	DATABASE_URL="$(DATABASE_URL)" ADMIN_EMAIL="$(ADMIN_EMAIL)" $(UV) run python scripts/import_monthly_expenses_xlsx.py "$(SPREADSHEET)"

import-monthly-replace:
	DATABASE_URL="$(DATABASE_URL)" ADMIN_EMAIL="$(ADMIN_EMAIL)" $(UV) run python scripts/import_monthly_expenses_xlsx.py "$(SPREADSHEET)" --replace

db-shell:
	$(COMPOSE) exec db psql -U buddy -d buddy

app-shell:
	$(COMPOSE) exec app sh
