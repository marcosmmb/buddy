COMPOSE ?= docker compose
UV ?= uv
PYTHON ?= python3
VERSION ?= $(shell $(UV) version --short 2>/dev/null || $(PYTHON) -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
DOCKER_IMAGE ?= marcosmmb/buddy
DOCKER_PLATFORMS ?= linux/amd64,linux/arm64
DATABASE_URL ?= sqlite:///./buddy.sqlite3
ADMIN_EMAIL ?= admin@buddy.local

.PHONY: sync up down restart build docker-image publish logs ps test smoke db-shell app-shell

sync:
	$(UV) sync

up:
	APP_VERSION=$(VERSION) $(COMPOSE) up -d --build

build:
	APP_VERSION=$(VERSION) $(COMPOSE) build

docker-image:
	docker build --build-arg APP_VERSION=$(VERSION) --tag $(DOCKER_IMAGE):$(VERSION) --tag $(DOCKER_IMAGE):latest .

publish:
	docker buildx build --platform $(DOCKER_PLATFORMS) --build-arg APP_VERSION=$(VERSION) --tag $(DOCKER_IMAGE):$(VERSION) --tag $(DOCKER_IMAGE):latest --push .

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

db-shell:
	$(COMPOSE) exec app python -m sqlite3 /data/buddy.sqlite3

app-shell:
	$(COMPOSE) exec app sh
