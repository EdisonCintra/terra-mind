FROM python:3.12-slim

ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=postgresql://terramind:terramind@postgres:5432/terramind

# Só o driver do Postgres: o runner não precisa do ambiente Python inteiro do monorepo.
# Contexto de build na raiz: `docker compose build migrar`.
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]>=3.3.6"
COPY db ./db

CMD ["python", "-m", "db.migrar"]
