FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agents ./agents
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "agents.api:app", "--host", "0.0.0.0", "--port", "8000"]
