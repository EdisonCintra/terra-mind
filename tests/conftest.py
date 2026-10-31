"""Fixtures compartilhadas: todo teste recomeça do seed no Postgres real.

Suba o banco com `docker compose up -d postgres`. Se a porta 5432 do host estiver ocupada, use
`POSTGRES_PORT=5433 docker compose up -d postgres` e exporte
`DATABASE_URL=postgresql://terramind:terramind@localhost:5433/terramind`.
"""

import os
from pathlib import Path

import psycopg
import pytest

DATABASE_URL = os.environ.setdefault(
    "DATABASE_URL", "postgresql://terramind:terramind@localhost:5432/terramind"
)
SEED = Path(__file__).resolve().parents[1] / "db" / "migrations" / "900_seed.sql"


@pytest.fixture(autouse=True)
def banco_limpo():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE erp.leituras_solo, erp.ordens_servico, erp.operadores, erp.maquinas,"
            " erp.insumos, erp.talhoes, agente.auditoria, agente.planos, agente.incidentes"
            " RESTART IDENTITY CASCADE"
        )
        conn.execute(SEED.read_text(encoding="utf-8"))
