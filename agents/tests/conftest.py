"""Postgres de verdade (testcontainers) para os testes marcados com `pg`.

Aplica `db/migrations/*.sql` em ordem (sem o seed). Cada teste recebe os schemas recriados do zero.
Sem Docker, os testes `pg` são pulados.
"""

from pathlib import Path

import asyncpg
import pytest

RAIZ = Path(__file__).resolve().parents[2]
MIGRATIONS = RAIZ / "db" / "migrations"


@pytest.fixture(scope="session")
def postgres_url():
    try:
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("postgres:16", driver=None)
        container.start()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Docker indisponível para testcontainers: {e}")
    url = container.get_connection_url()
    yield url.replace("postgresql+psycopg2://", "postgresql://")
    container.stop()


@pytest.fixture
async def pg_url(postgres_url):
    conn = await asyncpg.connect(postgres_url)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS agente CASCADE; DROP SCHEMA IF EXISTS erp CASCADE;")
        await conn.execute("DROP SCHEMA IF EXISTS conhecimento CASCADE;")
        sem_o_seed = (p for p in MIGRATIONS.glob("*.sql") if not p.name.startswith("9"))
        for arquivo in sorted(sem_o_seed):
            await conn.execute(arquivo.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO erp.talhoes"
            " (id, nome, area_ha, cultura, data_plantio, estagio_fenologico, lat, lon)"
            " VALUES (1, 'Talhão 1 - Sede', 120, 'milho', current_date - 30, 'V6', -17.79, -50.92)"
        )
    finally:
        await conn.close()
    return postgres_url
