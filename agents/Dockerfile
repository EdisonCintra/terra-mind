FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Contexto de build na raiz do monorepo (D3: um ambiente Python só): `docker compose build agents`.
# `channels` e `erp_mcp` entram porque o canal do Felipe roda dentro deste processo (contrato 7).
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY agents ./agents
COPY channels ./channels
COPY erp_mcp ./erp_mcp

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "agents.api:app", "--host", "0.0.0.0", "--port", "8000"]
