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

    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        engine = create_engine(pg.get_connection_url())
        with engine.begin() as conn:
            for sql in sorted(MIGRATIONS.glob("*.sql")):
                # O driver trata "%" como parâmetro; os SQL têm "%" em comentários.
                conn.exec_driver_sql(sql.read_text(encoding="utf-8").replace("%", "%%"))
        yield engine
        engine.dispose()
