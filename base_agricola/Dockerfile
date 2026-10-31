# Build a partir da raiz do monorepo: docker build -f base_agricola/Dockerfile .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUTF8=1 PATH="/app/.venv/bin:$PATH" \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY base_agricola ./base_agricola
# Baixa o modelo de embeddings no build: a demo não depende da rede do evento.
RUN python -c "from base_agricola.embeddings import FastEmbedEmbedder; FastEmbedEmbedder()"
EXPOSE 8010
CMD ["uvicorn", "base_agricola.main:app", "--host", "0.0.0.0", "--port", "8010"]
