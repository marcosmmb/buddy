COMPOSE ?= docker compose
PYTHON ?= .venv/bin/python
DATABASE_URL ?= postgresql+psycopg://buddy:buddy@localhost:5432/buddy
ADMIN_EMAIL ?= admin@buddy.local
SPREADSHEET ?= /Users/marcos/Downloads/Monthly Expenses (Canada).xlsx

.PHONY: up down restart build logs ps smoke import-monthly import-monthly-replace db-shell app-shell

up:
	$(COMPOSE) up -d --build

build:
	$(COMPOSE) build

down:
	$(COMPOSE) down

restart: down up

logs:
	$(COMPOSE) logs -f app

ps:
	$(COMPOSE) ps

smoke:
	DATABASE_URL="$(DATABASE_URL)" LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD=0 $(PYTHON) scripts/smoke_test.py

import-monthly:
	DATABASE_URL="$(DATABASE_URL)" ADMIN_EMAIL="$(ADMIN_EMAIL)" $(PYTHON) scripts/import_monthly_expenses_xlsx.py "$(SPREADSHEET)"

import-monthly-replace:
	DATABASE_URL="$(DATABASE_URL)" ADMIN_EMAIL="$(ADMIN_EMAIL)" $(PYTHON) scripts/import_monthly_expenses_xlsx.py "$(SPREADSHEET)" --replace

db-shell:
	$(COMPOSE) exec db psql -U buddy -d buddy

app-shell:
	$(COMPOSE) exec app sh
