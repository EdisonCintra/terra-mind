"""Ferramentas de escrita do mini-ERP. Contrato 5.

Três regras de ouro (plano do Felipe, seção 7.4):
1. A transição de status é checada no próprio `UPDATE` (o banco resolve a corrida).
2. Toda escrita grava uma linha em `agente.auditoria`, na mesma transação.
3. O que compromete recursos só passa com plano aprovado (`agente.planos`).

Sempre devolvem `{"ok": true, ...}` ou `{"ok": false, "erro": "<mensagem>"}`.
"""

from datetime import datetime
from typing import Any

import psycopg

from erp_mcp.db import conectar, para_json, registrar_auditoria

_NAO_APROVADO = {"ok": False, "erro": "plano não aprovado"}


def _plano(conn: psycopg.Connection, plano_id: int) -> dict[str, Any] | None:
    """Devolve o plano se estiver aprovado (`aprovado_por` e `respondido_em` preenchidos)."""
    return conn.execute(
        "SELECT id, incidente_id FROM agente.planos"
        " WHERE id = %s AND aprovado_por IS NOT NULL AND respondido_em IS NOT NULL",
        (plano_id,),
    ).fetchone()


def _os(conn: psycopg.Connection, os_id: int) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM erp.ordens_servico WHERE id = %s", (os_id,)).fetchone()


def suspender_os(os_id: int, incidente_id: int, motivo: str) -> dict[str, Any]:
    """Suspende preventivamente uma OS agendada (`agendada` → `suspensa_preventiva`).

    É a única alteração permitida sem aprovação do gerente, porque é reversível. Falha se a OS
    não estiver `agendada`. Informe em `motivo` a violação citada (documento, página e limite).
    """
    with conectar() as conn:
        if conn.execute("SELECT 1 FROM agente.incidentes WHERE id = %s", (incidente_id,)).fetchone() is None:
            return {"ok": False, "erro": f"incidente {incidente_id} não existe"}
        antes = _os(conn, os_id)
        depois = conn.execute(
            "UPDATE erp.ordens_servico SET status = 'suspensa_preventiva'"
            " WHERE id = %s AND status = 'agendada' RETURNING *",
            (os_id,),
        ).fetchone()
        if depois is None:
            atual = antes["status"] if antes else "inexistente"
            return {"ok": False, "erro": f"OS {os_id} não está agendada (status atual: {atual})"}
        registrar_auditoria(conn, incidente_id, "acao_preventiva", "suspender_os", antes, depois, motivo)
        return {"ok": True, "os": para_json(depois)}


def reagendar_os(os_id: int, novo_inicio: datetime, plano_id: int) -> dict[str, Any]:
    """Reagenda uma OS suspensa (`suspensa_preventiva` → `reagendada`) para `novo_inicio`.

    Exige plano aprovado. Falha se a OS não estiver `suspensa_preventiva`, o que também barra o
    SIM duplo ou tardio (a segunda chamada com o mesmo plano falha).
    """
    with conectar() as conn:
        plano = _plano(conn, plano_id)
        if plano is None:
            return _NAO_APROVADO
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (plano_id,))
        antes = _os(conn, os_id)
        depois = conn.execute(
            "UPDATE erp.ordens_servico SET status = 'reagendada', inicio_previsto = %s"
            " WHERE id = %s AND status = 'suspensa_preventiva' RETURNING *",
            (novo_inicio, os_id),
        ).fetchone()
        if depois is None:
            atual = antes["status"] if antes else "inexistente"
            return {"ok": False, "erro": f"OS {os_id} não está suspensa (status atual: {atual})"}
        registrar_auditoria(
            conn,
            plano["incidente_id"],
            "executor",
            "reagendar_os",
            antes,
            {**depois, "plano_id": plano_id},
            f"plano {plano_id} aprovado",
        )
        return {"ok": True, "os": para_json(depois)}


def alocar_maquina(os_id: int, maquina_id: int, plano_id: int) -> dict[str, Any]:
    """Troca a máquina de uma OS suspensa ou reagendada.

    Exige plano aprovado e máquina livre: `disponivel` e sem outra OS `agendada` ou
    `reagendada` sobreposta ao horário da OS (início + `duracao_horas`).
    """
    with conectar() as conn:
        plano = _plano(conn, plano_id)
        if plano is None:
            return _NAO_APROVADO
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (plano_id,))
        antes = _os(conn, os_id)
        if antes is None:
            return {"ok": False, "erro": f"OS {os_id} não existe"}
        if antes["status"] not in ("suspensa_preventiva", "reagendada"):
            return {
                "ok": False,
                "erro": f"OS {os_id} não aceita troca de máquina (status: {antes['status']})",
            }
        if antes["maquina_id"] == maquina_id:
            return {"ok": False, "erro": f"máquina {maquina_id} já está alocada à OS {os_id}"}
        livre = conn.execute(
            """
            SELECT 1 FROM erp.maquinas m
            WHERE m.id = %(maq)s AND m.status = 'disponivel'
              AND NOT EXISTS (
                  SELECT 1 FROM erp.ordens_servico o
                  WHERE o.maquina_id = m.id AND o.id <> %(os)s
                    AND o.status IN ('agendada', 'reagendada')
                    AND o.inicio_previsto < %(ini)s::timestamptz + %(dur)s * interval '1 hour'
                    AND %(ini)s::timestamptz < o.inicio_previsto + o.duracao_horas * interval '1 hour'
              )
            """,
            {
                "maq": maquina_id,
                "os": os_id,
                "ini": antes["inicio_previsto"],
                "dur": antes["duracao_horas"],
            },
        ).fetchone()
        if livre is None:
            return {"ok": False, "erro": f"máquina {maquina_id} não está livre para a OS {os_id}"}
        depois = conn.execute(
            "UPDATE erp.ordens_servico SET maquina_id = %s WHERE id = %s RETURNING *",
            (maquina_id, os_id),
        ).fetchone()
        registrar_auditoria(
            conn,
            plano["incidente_id"],
            "executor",
            "alocar_maquina",
            antes,
            {**depois, "plano_id": plano_id},
            f"plano {plano_id} aprovado",
        )
        return {"ok": True, "os": para_json(depois)}


def reservar_estoque(insumo_id: int, quantidade: float, plano_id: int) -> dict[str, Any]:
    """Baixa `quantidade` (na unidade do insumo) do estoque.

    Exige plano aprovado e estoque suficiente. Cada plano reserva cada insumo uma única vez:
    a segunda chamada com o mesmo `plano_id` e `insumo_id` falha.
    """
    if quantidade <= 0:
        return {"ok": False, "erro": "quantidade deve ser maior que zero"}
    with conectar() as conn:
        plano = _plano(conn, plano_id)
        if plano is None:
            return _NAO_APROVADO
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (plano_id,))
        repetida = conn.execute(
            "SELECT 1 FROM agente.auditoria WHERE acao = 'reservar_estoque'"
            " AND depois->>'plano_id' = %s AND depois->>'insumo_id' = %s",
            (str(plano_id), str(insumo_id)),
        ).fetchone()
        if repetida:
            return {"ok": False, "erro": f"estoque do insumo {insumo_id} já reservado pelo plano {plano_id}"}
        antes = conn.execute("SELECT * FROM erp.insumos WHERE id = %s", (insumo_id,)).fetchone()
        depois = conn.execute(
            "UPDATE erp.insumos SET estoque = estoque - %s WHERE id = %s AND estoque >= %s RETURNING *",
            (quantidade, insumo_id, quantidade),
        ).fetchone()
        if depois is None:
            if antes is None:
                return {"ok": False, "erro": f"insumo {insumo_id} não existe"}
            tem = f"{antes['estoque']} {antes['unidade']}"
            return {"ok": False, "erro": f"estoque insuficiente: há {tem}, pedido {quantidade}"}
        registrar_auditoria(
            conn,
            plano["incidente_id"],
            "executor",
            "reservar_estoque",
            antes,
            {**depois, "plano_id": plano_id, "insumo_id": insumo_id, "quantidade": quantidade},
            f"plano {plano_id} aprovado",
        )
        return {"ok": True, "insumo": para_json(depois)}
