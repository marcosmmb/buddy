FROM python:3.12-slim

ARG APP_VERSION=dev

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

LABEL org.opencontainers.image.title="Buddy" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.description="Self-hosted budgeting and expense tracker"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL=sqlite:////data/buddy.sqlite3 \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY frontend ./frontend

VOLUME ["/data"]

EXPOSE 3088

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3088"]
