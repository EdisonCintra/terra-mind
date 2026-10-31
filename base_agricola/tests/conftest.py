from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer

from base_agricola.main import app

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(scope="session")
def engine():
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        with engine.begin() as conn:
            for sql in sorted(MIGRATIONS.glob("*.sql")):
                # O driver trata "%" como parâmetro; os SQL têm "%" em comentários.
                conn.exec_driver_sql(sql.read_text(encoding="utf-8").replace("%", "%%"))
        yield engine
        engine.dispose()


@pytest.fixture
def repo(engine):
    from base_agricola.repositorio import RepositorioConhecimento

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE conhecimento.fatores_perda, conhecimento.documentos"))
    return RepositorioConhecimento(engine)
