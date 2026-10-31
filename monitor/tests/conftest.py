import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SP = ZoneInfo("America/Sao_Paulo")
FIXTURES = Path(__file__).parent / "fixtures"
AGORA = datetime(2026, 10, 30, 8, 0, tzinfo=SP)


@pytest.fixture
def payload_open_meteo():
    return json.loads((FIXTURES / "open_meteo.json").read_text(encoding="utf-8"))


MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@pytest.fixture(scope="session")
def engine_pg():
    from sqlalchemy import create_engine
    from testcontainers.community.postgres import PostgresContainer

    felipe = (MIGRATIONS / "002_agente.sql").exists()
    minimo = Path(__file__).parent / "sql" / "contrato_minimo.sql"
    scripts = sorted(MIGRATIONS.glob("*.sql")) if felipe else [minimo]
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        with engine.begin() as conn:
            for sql in scripts:
                conn.exec_driver_sql(sql.read_text(encoding="utf-8"))
        yield engine
        engine.dispose()
