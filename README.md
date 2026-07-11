# Buddy

Buddy is a small self-hosted budgeting and expense tracker built with Litestar, Postgres, SQLAlchemy, and a dependency-light browser frontend.

## Features

- Multiple users with per-user default currency preferences
- Admin-created trackers
- Users can belong to multiple trackers
- Per-tracker categories
- Expense capture with date, category, amount, currency, payer, description, and shared/individual flag
- Overview totals by category, person, and category/person
- Monthly balance page with configurable member share percentages and settlement suggestions
- Year-to-date summary

## Quick Start

```bash
cp .env.example .env
make up
```

Open http://localhost:3088.

Default admin credentials come from `.env`:

```text
ADMIN_EMAIL=admin@buddy.local
ADMIN_PASSWORD=change-me-now
```

Change them before exposing the app anywhere outside your machine.

## Local Docker Deployment

To run Buddy from Docker Hub without building from source:

```bash
docker compose -f docker-compose.deploy.yml up -d
```

This pulls `marcosmmb/buddy:latest` and `postgres:16-alpine`, stores database data in a Docker volume, and serves Buddy on http://localhost:3088.

Set values in a local `.env` file to change credentials, secrets, or the host port:

```text
BUDDY_PORT=3088
POSTGRES_DB=buddy
POSTGRES_USER=buddy
POSTGRES_PASSWORD=change-this
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-this-too
ADMIN_NAME=Buddy Admin
APP_SECRET=replace-with-a-long-random-secret
```

## Local Development

```bash
uv sync
export DATABASE_URL=postgresql+psycopg://buddy:buddy@localhost:5432/buddy
uv run uvicorn app.main:app --reload --port 3088
```

For local development with Postgres in Docker:

```bash
docker compose up db
```

Run the API smoke test:

```bash
uv run python scripts/smoke_test.py
```

## Publishing

The app version lives in `pyproject.toml`.

```bash
make publish
```

This builds and pushes multi-architecture `linux/amd64` and `linux/arm64` images for `marcosmmb/buddy:<version>` and `marcosmmb/buddy:latest`.

## Notes

Buddy stores auth sessions as random bearer tokens in the database. It is intended for trusted self-hosted environments; put it behind HTTPS before using it remotely.
