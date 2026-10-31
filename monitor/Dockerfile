# Build a partir da raiz do monorepo: docker build -f monitor/Dockerfile .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUTF8=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY monitor ./monitor
EXPOSE 8020
CMD ["uvicorn", "monitor.main:app", "--host", "0.0.0.0", "--port", "8020"]
