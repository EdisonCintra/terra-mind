FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATABASE_URL=postgresql://terramind:terramind@postgres:5432/terramind

# Contexto de build na raiz do monorepo (D3): `docker compose build painel`.
# `erp_mcp` entra junto porque o painel reaproveita `erp_mcp.db` (conexão e auditoria).
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY erp_mcp ./erp_mcp
COPY painel ./painel

EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "painel.main:app", "--host", "0.0.0.0", "--port", "8080"]
