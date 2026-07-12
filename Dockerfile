FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev && \
    find /app/.venv -type d -name "__pycache__" -prune -exec rm -rf {} + && \
    find /app/.venv -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete


FROM python:3.12-slim AS runtime

ARG APP_VERSION=dev

LABEL org.opencontainers.image.title="Buddy" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.description="Self-hosted budgeting and expense tracker"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL=sqlite:////data/buddy.sqlite3 \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY frontend ./frontend

VOLUME ["/data"]

EXPOSE 3088

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3088"]
