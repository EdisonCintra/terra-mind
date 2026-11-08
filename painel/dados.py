"""Consultas e formatação do painel (telas 1 a 3): só leitura do Postgres."""

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from erp_mcp.db import conectar

FUSO = ZoneInfo("America/Sao_Paulo")  # contrato 12

# O `agents` escreve a citação no motivo da suspensão: "<justificativa> (fonte: <doc>, p. <n>)".
CITACAO = re.compile(r"\(fonte:\s*(.+?),\s*p\.\s*(\d+)\)")


def listar_ordens() -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id, os.tipo_operacao, os.inicio_previsto, os.status,
                   t.nome AS talhao, i.produto, m.nome AS maquina, o.nome AS operador
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            LEFT JOIN erp.operadores o ON o.id = os.operador_id
            ORDER BY os.inicio_previsto, os.id
            """
        ).fetchall()


def listar_incidentes(limite: int = 8) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT i.id, i.status, i.criado_em, t.nome AS talhao
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            ORDER BY i.id DESC LIMIT %s
            """,
            (limite,),
        ).fetchall()


def auditoria_do_incidente(incidente_id: int) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, ts, no, acao, antes, depois, justificativa FROM agente.auditoria"
            " WHERE incidente_id = %s ORDER BY ts, id",
            (incidente_id,),
        ).fetchall()


def planos_pendentes() -> list[dict]:
    """Planos sem resposta (inclui os escalonados: a sessão segue pausada)."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT p.id, p.incidente_id, p.criado_em, p.escalado_em,
                   p.opcoes_json->>'mensagem' AS mensagem, t.nome AS talhao,
                   p.opcoes_json->'custos_atraso' AS custos_atraso
            FROM agente.planos p
            JOIN agente.incidentes i ON i.id = p.incidente_id
            JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE p.respondido_em IS NULL
            ORDER BY p.criado_em, p.id
            """
        ).fetchall()


def listar_produtos() -> list[str]:
    """Produtos cadastrados no ERP, para o Pergunte à bula (contrato 1: mesmo nome da bula)."""
    with conectar() as conn:
        return [
            linha["produto"] for linha in conn.execute("SELECT produto FROM erp.insumos ORDER BY produto")
        ]


def incidentes_decisao_manual() -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT i.id, t.nome AS talhao,
                   (SELECT a.justificativa FROM agente.auditoria a
                    WHERE a.incidente_id = i.id AND a.acao = 'enviar_alerta'
                    ORDER BY a.id DESC LIMIT 1) AS alerta
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE i.status = 'aguardando_decisao_manual'
            ORDER BY i.id
            """
        ).fetchall()


def separar_citacoes(texto: str | None) -> tuple[str, list[tuple[str, str]]]:
    """Tira as citações `(fonte: X, p. N)` do texto e devolve (texto limpo, [(fonte, página)])."""
    if not texto:
        return "", []
    citacoes = [(m.group(1), m.group(2)) for m in CITACAO.finditer(texto)]
    limpo = re.sub(r"\s{2,}", " ", CITACAO.sub("", texto)).strip(" ;")
    return limpo, citacoes


def _reais(valor: float) -> str:
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def _duracao(segundos: float) -> str:
    minutos = max(0, int(segundos // 60))
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"{horas} h"
    dias, resto = divmod(horas, 24)
    return f"{dias} dia{'s' if dias > 1 else ''}" + (f" e {resto} h" if resto else "")


def linhas_atraso(custos: Any, agora: datetime) -> list[str]:
    """G5: uma linha por OS com quando a janela proposta vence e quanto custa perdê-la.

    Os números vêm prontos do `agents` (`opcoes_json["custos_atraso"]`); aqui só o "vence em" é
    recalculado, a cada atualização da tela.
    """
    if isinstance(custos, str):
        custos = json.loads(custos)
    linhas = []
    for c in custos or []:
        janela = datetime.fromisoformat(c["janela_proposta"]).astimezone(FUSO)
        vence = datetime.fromisoformat(c["vence_em"])
        inicio = f"OS {c['os_id']}: janela de {janela:%d/%m %H:%M}"
        if agora >= vence:
            linhas.append(f"{inicio} já venceu; peça outra opção.")
            continue
        partes = [f"{inicio} vence em {_duracao((vence - agora).total_seconds())}"]
        if c.get("custo_perder_janela_rs") is not None:
            estimativa = " (estimativa)" if str(c.get("fonte_fator", "")).startswith("estimativa") else ""
            partes.append(f"perdê-la custa {_reais(c['custo_perder_janela_rs'])}{estimativa}")
        if c.get("proxima_janela"):
            proxima = datetime.fromisoformat(c["proxima_janela"]).astimezone(FUSO)
            partes.append(f"próxima janela: {proxima:%d/%m %H:%M}")
        else:
            partes.append("sem outra janela na previsão")
        linhas.append(" · ".join(partes))
    return linhas


def _curto(valor: Any) -> str:
    texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False, default=str)
    return texto if len(texto) <= 60 else texto[:57] + "…"


def resumo(antes: Any, depois: Any) -> list[str]:
    """Linhas "campo: antes → depois" (só o que mudou) ou "campo: valor" quando não há `antes`."""
    if isinstance(antes, dict) and isinstance(depois, dict):
        mudancas = [k for k in depois if k != "id" and antes.get(k) != depois[k]]
        return [f"{k}: {_curto(antes.get(k))} → {_curto(depois[k])}" for k in mudancas[:4]]
    if isinstance(depois, dict):
        return [f"{k}: {_curto(v)}" for k, v in list(depois.items())[:4]]
    return []
