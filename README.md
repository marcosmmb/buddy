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
docker compose up --build
```

Open http://localhost:8000.

Default admin credentials come from `.env`:

```text
ADMIN_EMAIL=admin@buddy.local
ADMIN_PASSWORD=change-me-now
```

Change them before exposing the app anywhere outside your machine.

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://buddy:buddy@localhost:5432/buddy
uvicorn app.main:app --reload
```

For local development with Postgres in Docker:

```bash
docker compose up db
```

Run the API smoke test:

```bash
python scripts/smoke_test.py
```

## Notes

Buddy stores auth sessions as random bearer tokens in the database. It is intended for trusted self-hosted environments; put it behind HTTPS before using it remotely.
