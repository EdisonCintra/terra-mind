"""Conexão com o Postgres do mini-ERP e auditoria compartilhada pelas ferramentas."""

import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def conectar() -> psycopg.Connection:
    """Abre uma conexão (use `with conectar() as conn:` = uma transação com commit/rollback)."""
    url = os.environ.get("DATABASE_URL", "postgresql://terramind:terramind@localhost:5432/terramind")
    return psycopg.connect(url, row_factory=dict_row)


def _padrao(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return str(valor)


def para_json(valor: Any) -> Any:
    """Converte `datetime` e `Decimal` em tipos que o JSON aceita (ISO 8601 e float)."""
    return json.loads(json.dumps(valor, default=_padrao))


def registrar_auditoria(
    conn: psycopg.Connection,
    incidente_id: int | None,
    no: str,
    acao: str,
    antes: Any = None,
    depois: Any = None,
    justificativa: str | None = None,
) -> None:
    """Grava uma linha em `agente.auditoria` na MESMA transação da escrita que ela descreve."""
    conn.execute(
        "INSERT INTO agente.auditoria (incidente_id, no, acao, antes, depois, justificativa)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            incidente_id,
            no,
            acao,
            None if antes is None else Jsonb(para_json(antes)),
            None if depois is None else Jsonb(para_json(depois)),
            justificativa,
        ),
    )
