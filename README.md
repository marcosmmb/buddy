# Buddy

<p align="center">
  <img src="frontend/static/buddy-mascot-bee.png" alt="Buddy mascot" width="180" />
</p>

<p align="center">
  A small, self-hosted budgeting and shared-expense tracker for households, couples, roommates, and tiny teams.
</p>

<p align="center">
  <strong>Litestar</strong> | <strong>SQLite</strong> | <strong>SQLAlchemy</strong> | <strong>uv</strong> | <strong>Docker</strong>
</p>

Buddy helps a small group track expenses, split shared costs, understand who paid for what, and calculate simple monthly settlements. It is intentionally lightweight: one Python app, one SQLite database, and a dependency-light browser frontend.

## Highlights

- 🏠 Self-hosted budgeting for small shared households and groups
- 💸 Shared and individual expense tracking with settlement suggestions
- 📊 Monthly and yearly breakdowns by category, payer, member, and month
- ⚖️ Custom tracker members, categories, currencies, and monthly share splits
- 📥 CSV import preview, import, and export workflows
- 🏦 Optional Plaid-powered bank import with manual review and categorization
- 🐳 Simple Docker deployment with persistent SQLite storage

## Quick Start

Clone the repository and start Buddy from source:

```bash
cp .env.example .env
make up
```

Open:

```text
http://localhost:3088
```

Default admin credentials are controlled by `.env`:

```text
ADMIN_EMAIL=admin@buddy.local
ADMIN_PASSWORD=change-me-now
```

Change the admin password and `APP_SECRET` before exposing Buddy outside your own machine.

## Docker Compose Example

To run the published Docker image without cloning the repository, create a `compose.yml` file like this:

```yaml
services:
  app:
    image: ghcr.io/marcosmmb/buddy:latest
    pull_policy: always
    restart: unless-stopped
    environment:
      DATABASE_URL: ${BUDDY_DATABASE_URL:-sqlite:////data/buddy.sqlite3}
      ADMIN_EMAIL: ${ADMIN_EMAIL:-admin@buddy.local}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD:-change-me-now}
      ADMIN_NAME: ${ADMIN_NAME:-Buddy Admin}
      APP_SECRET: ${APP_SECRET:-replace-with-a-long-random-secret}
      PLAID_CLIENT_ID: ${PLAID_CLIENT_ID:-}
      PLAID_SECRET: ${PLAID_SECRET:-}
      PLAID_ENV: ${PLAID_ENV:-sandbox}
      PLAID_PRODUCTS: ${PLAID_PRODUCTS:-transactions}
      PLAID_COUNTRY_CODES: ${PLAID_COUNTRY_CODES:-CA}
      BANK_TOKEN_ENCRYPTION_KEY: ${BANK_TOKEN_ENCRYPTION_KEY:-replace-with-a-long-random-secret-for-bank-tokens}
    ports:
      - "${BUDDY_PORT:-3088}:3088"
    volumes:
      - buddy-data:/data

volumes:
  buddy-data:
```

Then start it:

```bash
docker compose up -d
```

You can put deployment settings in a `.env` file next to that Compose file:

```text
BUDDY_PORT=3088
BUDDY_DATABASE_URL=sqlite:////data/buddy.sqlite3
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=replace-this-password
ADMIN_NAME=Buddy Admin
APP_SECRET=replace-with-a-long-random-secret
PLAID_CLIENT_ID=
PLAID_SECRET=
PLAID_ENV=sandbox
PLAID_PRODUCTS=transactions
PLAID_COUNTRY_CODES=CA
BANK_TOKEN_ENCRYPTION_KEY=replace-with-a-long-random-secret-for-bank-tokens
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `BUDDY_PORT` | `3088` | Host port used by the published-image Compose example. |
| `BUDDY_DATABASE_URL` | `sqlite:////data/buddy.sqlite3` | SQLite URL passed to the container as `DATABASE_URL`. |
| `ADMIN_EMAIL` | `admin@buddy.local` | Email for the bootstrap admin user. |
| `ADMIN_PASSWORD` | `change-me-now` | Password for the bootstrap admin user. |
| `ADMIN_NAME` | `Buddy Admin` | Display name for the bootstrap admin user. |
| `APP_SECRET` | `dev-secret-change-me` | Application secret. Set this to a long random value in real deployments. |
| `PLAID_CLIENT_ID` | empty | Plaid client ID. Required for bank import. |
| `PLAID_SECRET` | empty | Plaid secret for the configured environment. Required for bank import. |
| `PLAID_ENV` | `sandbox` | Plaid environment: `sandbox`, `development`, or `production`. |
| `PLAID_PRODUCTS` | `transactions` | Comma-separated Plaid products. Buddy expects `transactions`. |
| `PLAID_COUNTRY_CODES` | `CA` | Comma-separated Plaid country codes. |
| `BANK_TOKEN_ENCRYPTION_KEY` | empty | Secret used to encrypt Plaid access tokens at rest. Set this in real deployments. |

## Bank Import

Buddy can optionally connect to Plaid for bank transaction import.

The bank import flow is intentionally review-first:

1. A user connects their bank account through Plaid Link.
2. Buddy syncs outgoing transactions into a review queue.
3. The connected user is selected as the default payer.
4. The user manually chooses the Buddy category for each transaction.
5. The user imports selected rows into the tracker as regular expenses.

Buddy does not automatically match categories from Plaid. Plaid category data is kept only in the raw transaction payload for debugging. Syncing is manual from the Bank Import tab, so private LAN deployments do not need a public callback URL.

## Data Storage

Buddy stores its data in SQLite.

Local development defaults to:

```text
./buddy.sqlite3
```

Docker deployments default to:

```text
/data/buddy.sqlite3
```

In Docker Compose, `/data` is backed by the `buddy-data` volume.

To back up a Docker deployment:

```bash
docker compose exec app python -m sqlite3 /data/buddy.sqlite3 ".backup '/data/buddy-backup.sqlite3'"
```

Then copy the backup from the volume or container using your preferred Docker workflow.

## Local Development

Buddy uses `uv` for Python dependency management.

Install dependencies:

```bash
uv sync
```

Run the app locally:

```bash
uv run uvicorn app.main:app --reload --port 3088
```

Run the available Makefile helper:

```bash
make
```

Run tests:

```bash
make test
```

Run the smoke test:

```bash
make smoke
```

If your environment blocks uv from using its global cache, keep the cache inside the repo:

```bash
UV_CACHE_DIR=.uv-cache make test
UV_CACHE_DIR=.uv-cache make smoke
```

## Project Structure

```text
app/
  main.py              Litestar app wiring
  routes/              Class-based route controllers
  models.py            SQLAlchemy models
  schemas.py           Request payload schemas
  services.py          Domain calculations and serialization
  utils.py             Shared route helpers and CSV utilities
  db.py                SQLite engine, sessions, bootstrap admin
frontend/
  index.html           Browser entry point
  static/app.js        Dependency-light frontend app
  static/styles.css    Application styling
  static/              Mascot, icon, and visual assets
scripts/
  smoke_test.py        End-to-end API smoke test
```

## Security Notes

Buddy is intended for small trusted self-hosted environments.

Before remote use:

- Change the default admin password.
- Set a long random `APP_SECRET`.
- Put Buddy behind HTTPS.
- Restrict access at your reverse proxy or network edge if appropriate.
- Back up the SQLite database regularly.

Buddy stores session tokens in the database as bearer tokens. Treat database backups as sensitive.

## Contributing

Contributions are welcome.

Good first areas:

- UI polish and accessibility improvements
- More import formats
- Better backup and restore documentation
- More tests around settlement and monthly share behavior
- Documentation improvements

Development flow:

```bash
uv sync
make test
make smoke
```

Please keep changes focused, include tests for behavior changes, and avoid committing local databases or secrets.

## License

Buddy is released under the MIT License. See [LICENSE](LICENSE).
