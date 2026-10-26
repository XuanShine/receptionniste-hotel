# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /uvx /bin/

WORKDIR /app

RUN useradd --create-home --uid 1000 app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

RUN mkdir -p record messages && chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

CMD ["python", "src/agent.py", "start"]
