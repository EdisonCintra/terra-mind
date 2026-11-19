"""Ferramentas de leitura do mini-ERP (sem efeito colateral). Contratos 5, 12 e 16."""

from datetime import datetime
from typing import Any

from erp_mcp.db import conectar, para_json

_SQL_OS = """
    SELECT os.id, os.talhao_id, os.status, os.tipo_operacao, os.insumo_id,
           i.produto, i.tipo AS tipo_insumo, os.dose_por_ha, i.unidade, i.preco_unitario,
           os.inicio_previsto, os.duracao_horas, os.maquina_id, m.tipo AS maquina_tipo,
           os.operador_id, t.nome AS talhao, t.area_ha
    FROM erp.ordens_servico os
    JOIN erp.talhoes t ON t.id = os.talhao_id
    LEFT JOIN erp.insumos i ON i.id = os.insumo_id
    LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
"""


def listar_os(inicio: datetime, fim: datetime, talhao_id: int | None = None) -> list[dict[str, Any]]:
    """Lista as ordens de serviço (OS) com início previsto entre `inicio` e `fim`.

    Cada OS traz `id`, `talhao_id`, `talhao`, `area_ha`, `status`, `tipo_operacao`, `insumo_id`,
    `produto`, `tipo_insumo`, `dose_por_ha`, `unidade`, `preco_unitario`, `inicio_previsto`,
    `duracao_horas`, `maquina_id`, `maquina_tipo` e `operador_id`. Use `talhao_id` para filtrar
    um talhão. Datas em ISO 8601 com fuso.
    """
    sql = (
        _SQL_OS
        + " WHERE os.inicio_previsto BETWEEN %s AND %s"
        + " AND (%s::int IS NULL OR os.talhao_id = %s) ORDER BY os.inicio_previsto, os.id"
    )
    with conectar() as conn:
        return para_json(conn.execute(sql, (inicio, fim, talhao_id, talhao_id)).fetchall())


def detalhar_os(os_id: int) -> dict[str, Any]:
    """Devolve uma OS completa pelo `os_id`, com as mesmas chaves de `listar_os`.

    Se a OS não existir, responde `{"ok": false, "erro": "..."}`.
    """
    with conectar() as conn:
        linha = conn.execute(_SQL_OS + " WHERE os.id = %s", (os_id,)).fetchone()
    if linha is None:
        return {"ok": False, "erro": f"OS {os_id} não existe"}
    return para_json(linha)


def disponibilidade_maquinas(
    inicio: datetime, fim: datetime, tipo: str | None = None
) -> list[dict[str, Any]]:
    """Lista as máquinas livres no intervalo `inicio`–`fim`.

    Livre = `status = 'disponivel'` e sem OS `agendada` ou `reagendada` que se sobreponha ao
    intervalo (o fim de cada OS é `inicio_previsto + duracao_horas`). Use `tipo` (ex.:
    'pulverizador') para filtrar. Cada máquina traz `id`, `nome`, `tipo` e `status`.
    """
    sql = """
        SELECT m.id, m.nome, m.tipo, m.status
        FROM erp.maquinas m
        WHERE m.status = 'disponivel'
          AND (%s::text IS NULL OR m.tipo = %s)
          AND NOT EXISTS (
              SELECT 1 FROM erp.ordens_servico os
              WHERE os.maquina_id = m.id
                AND os.status IN ('agendada', 'reagendada')
                AND os.inicio_previsto < %s
                AND %s < os.inicio_previsto + os.duracao_horas * interval '1 hour'
          )
        ORDER BY m.id
    """
    with conectar() as conn:
        return para_json(conn.execute(sql, (tipo, tipo, fim, inicio)).fetchall())


def listar_operadores() -> list[dict[str, Any]]:
    """Lista os operadores com `id`, `nome` e `telegram_chat_id` (para avisá-los pelo canal)."""
    with conectar() as conn:
        return para_json(
            conn.execute("SELECT id, nome, telegram_chat_id FROM erp.operadores ORDER BY id").fetchall()
        )


def consultar_talhao(talhao_id: int) -> dict[str, Any]:
    """Devolve o talhão completo: `id`, `nome`, `area_ha`, `cultura`, `data_plantio`,
    `estagio_fenologico`, `lat`, `lon` e `tipo_solo`.

    Se o talhão não existir, responde `{"ok": false, "erro": "..."}`.
    """
    with conectar() as conn:
        linha = conn.execute("SELECT * FROM erp.talhoes WHERE id = %s", (talhao_id,)).fetchone()
    if linha is None:
        return {"ok": False, "erro": f"Talhão {talhao_id} não existe"}
    return para_json(linha)


def leituras_solo(talhao_id: int, horas: int = 24) -> list[dict[str, Any]]:
    """Devolve as leituras de umidade do solo (`ts`, `umidade_pct`, em %) das últimas `horas`
    horas do talhão, da mais recente para a mais antiga."""
    sql = (
        "SELECT talhao_id, ts, umidade_pct FROM erp.leituras_solo"
        " WHERE talhao_id = %s AND ts >= now() - %s * interval '1 hour' ORDER BY ts DESC"
    )
    with conectar() as conn:
        return para_json(conn.execute(sql, (talhao_id, horas)).fetchall())


def listar_talhoes() -> list[dict[str, Any]]:
    """Lista os talhões com `id`, `nome`, `area_ha`, `cultura`, `estagio_fenologico`, `lat` e `lon`."""
    sql = "SELECT id, nome, area_ha, cultura, estagio_fenologico, lat, lon FROM erp.talhoes ORDER BY id"
    with conectar() as conn:
        return para_json(conn.execute(sql).fetchall())


def consultar_insumo(insumo_id: int) -> dict[str, Any]:
    """Devolve o insumo: `id`, `produto`, `tipo`, `unidade`, `preco_unitario`, `estoque` e `bula_id`.

    Se o insumo não existir, responde `{"ok": false, "erro": "..."}`.
    """
    with conectar() as conn:
        linha = conn.execute("SELECT * FROM erp.insumos WHERE id = %s", (insumo_id,)).fetchone()
    if linha is None:
        return {"ok": False, "erro": f"Insumo {insumo_id} não existe"}
    return para_json(linha)


def detalhar_execucao(os_id: int) -> dict[str, Any]:
    """Devolve a OS executada com tudo o que o relatório de aplicação usa (contrato 28).

    Traz as chaves de `detalhar_os` mais `cultura`, `estagio_fenologico`, `lat`, `lon`, `maquina`,
    `operador`, `bula_id` e `execucao` (a linha de `erp.execucoes_os`, com `maquina` e `operador`
    reais). Se a OS não existir ou ainda não tiver execução registrada, responde
    `{"ok": false, "erro": "..."}`.
    """
    with conectar() as conn:
        os = conn.execute(
            """
            SELECT os.id, os.talhao_id, os.status, os.tipo_operacao, os.insumo_id, os.dose_por_ha,
                   os.inicio_previsto, os.duracao_horas,
                   t.nome AS talhao, t.area_ha, t.cultura, t.estagio_fenologico, t.lat, t.lon,
                   i.produto, i.tipo AS tipo_insumo, i.unidade, i.bula_id,
                   m.nome AS maquina, o.nome AS operador
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            LEFT JOIN erp.operadores o ON o.id = os.operador_id
            WHERE os.id = %s
            """,
            (os_id,),
        ).fetchone()
        if os is None:
            return {"ok": False, "erro": f"OS {os_id} não existe"}
        execucao = conn.execute(
            """
            SELECT e.*, m.nome AS maquina, m.tipo AS maquina_tipo, o.nome AS operador
            FROM erp.execucoes_os e
            LEFT JOIN erp.maquinas m ON m.id = e.maquina_id
            LEFT JOIN erp.operadores o ON o.id = e.operador_id
            WHERE e.os_id = %s
            """,
            (os_id,),
        ).fetchone()
    if execucao is None:
        return {"ok": False, "erro": f"OS {os_id} ainda não tem execução registrada"}
    return para_json({**os, "execucao": execucao})
