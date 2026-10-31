import os

from sqlalchemy import Engine, create_engine


def criar_engine(url: str | None = None) -> Engine:
    return create_engine(url or os.environ["DATABASE_URL"])
