"""Consultas e formatação do painel: só leitura do Postgres (as escritas ficam em `painel.escrita`)."""

import json
import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from erp_mcp import leitura
from erp_mcp.db import conectar

FUSO = ZoneInfo("America/Sao_Paulo")  # contrato 12

# O `agents` escreve a citação no motivo da suspensão: "<justificativa> (fonte: <doc>, p. <n>)".
CITACAO = re.compile(r"\(fonte:\s*(.+?),\s*p\.\s*(\d+)\)")


def listar_fazendas() -> list[dict]:
    """Fazendas para o seletor do painel (contrato 20), com o nome da organização."""
    with conectar() as conn:
        return conn.execute(
            "SELECT f.id, f.nome, f.demo, o.nome AS organizacao FROM erp.fazendas f"
            " JOIN erp.organizacoes o ON o.id = f.organizacao_id ORDER BY f.demo, f.nome"
        ).fetchall()


def listar_ordens(fazenda_id: int | None = None) -> list[dict]:
    """Todas as leituras do painel aceitam `fazenda_id` (contrato 20); sem ele, todas as fazendas."""
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
            WHERE (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY os.inicio_previsto, os.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def listar_incidentes(limite: int = 8, fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT i.id, i.status, i.criado_em, t.nome AS talhao
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY i.id DESC LIMIT %(limite)s
            """,
            {"faz": fazenda_id, "limite": limite},
        ).fetchall()


def auditoria_do_incidente(incidente_id: int) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, ts, no, acao, antes, depois, justificativa FROM agente.auditoria"
            " WHERE incidente_id = %s ORDER BY ts, id",
            (incidente_id,),
        ).fetchall()


def planos_pendentes(fazenda_id: int | None = None) -> list[dict]:
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
            WHERE p.respondido_em IS NULL AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY p.criado_em, p.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def listar_produtos(fazenda_id: int | None = None) -> list[str]:
    """Produtos cadastrados no ERP, para o Pergunte à bula (contrato 1: mesmo nome da bula)."""
    with conectar() as conn:
        return [
            linha["produto"]
            for linha in conn.execute(
                "SELECT DISTINCT produto FROM erp.insumos"
                " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY produto",
                {"faz": fazenda_id},
            )
        ]


def incidentes_decisao_manual(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT i.id, t.nome AS talhao,
                   (SELECT a.justificativa FROM agente.auditoria a
                    WHERE a.incidente_id = i.id AND a.acao = 'enviar_alerta'
                    ORDER BY a.id DESC LIMIT 1) AS alerta
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE i.status = 'aguardando_decisao_manual'
              AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY i.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def separar_citacoes(texto: str | None) -> tuple[str, list[tuple[str, str]]]:
    """Tira as citações `(fonte: X, p. N)` do texto e devolve (texto limpo, [(fonte, página)])."""
    if not texto:
        return "", []
    citacoes = [(m.group(1), m.group(2)) for m in CITACAO.finditer(texto)]
    limpo = re.sub(r"\s{2,}", " ", CITACAO.sub("", texto)).strip(" ;")
    return limpo, citacoes


def reais(valor: float) -> str:
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
            partes.append(f"perdê-la custa {reais(c['custo_perder_janela_rs'])}{estimativa}")
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


# --- G1: perda evitada ------------------------------------------------------------------------
SITUACOES_IMPACTO = {
    "evitada": "Perda evitada",
    "suspensa": "Aguardando decisão",
    "recusada": "Proposta recusada",
    "decisao_manual": "Decisão manual",
}


def inicio_da_safra(hoje: date) -> date:
    """`SAFRA_INICIO` (AAAA-MM-DD) ou 1º de julho do ano-safra corrente. Vira configuração por fazenda
    quando o F2 criar `erp.fazendas`."""
    configurado = os.environ.get("SAFRA_INICIO")
    if configurado:
        return date.fromisoformat(configurado)
    return date(hoje.year if hoje.month >= 7 else hoje.year - 1, 7, 1)


def perda_evitada(
    inicio: date, fim: date, incluir_replay: bool = False, fazenda_id: int | None = None
) -> dict:
    """Números do dashboard, todos de `agente.impactos` e `agente.planos` (nada é somado no navegador).

    Incidente de replay fica de fora (contrato 27), a não ser no modo demonstração.
    """
    filtro = """
        FROM agente.impactos im
        JOIN agente.incidentes i ON i.id = im.incidente_id
        JOIN erp.talhoes t ON t.id = im.talhao_id
        WHERE im.criado_em >= %(inicio)s AND im.criado_em < %(fim)s::date + 1
          AND (%(replay)s OR i.origem = 'live')
          AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
    """
    planos = """
        FROM agente.planos p JOIN agente.incidentes i ON i.id = p.incidente_id
        JOIN erp.talhoes t ON t.id = i.talhao_id
        WHERE p.criado_em >= %(inicio)s AND p.criado_em < %(fim)s::date + 1
          AND (%(replay)s OR i.origem = 'live')
          AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
    """
    params = {"inicio": inicio, "fim": fim, "replay": incluir_replay, "faz": fazenda_id}
    with conectar() as conn:
        situacoes = conn.execute(
            "SELECT im.situacao, count(*) AS os, COALESCE(sum(im.perda_rs), 0) AS perda_rs,"
            " count(*) FILTER (WHERE im.perda_rs IS NULL) AS sem_fator,"
            " bool_or(im.fonte ILIKE 'estimativa%%') AS estimativa" + filtro + " GROUP BY im.situacao",
            params,
        ).fetchall()
        por_talhao = conn.execute(
            "SELECT t.nome AS nome, count(*) AS os, COALESCE(sum(im.perda_rs), 0) AS perda_rs"
            + filtro
            + " AND im.situacao = 'evitada' GROUP BY t.nome ORDER BY perda_rs DESC, nome",
            params,
        ).fetchall()
        por_produto = conn.execute(
            "SELECT COALESCE(im.produto, 'sem produto') AS nome, count(*) AS os,"
            " COALESCE(sum(im.perda_rs), 0) AS perda_rs"
            + filtro
            + " AND im.situacao = 'evitada' GROUP BY 1 ORDER BY perda_rs DESC, nome",
            params,
        ).fetchall()
        linhas = conn.execute(
            "SELECT im.incidente_id, im.os_id, t.nome AS talhao, im.produto, im.condicao, im.fator,"
            " im.fonte, im.valor_aplicacao_rs, im.perda_rs, im.situacao, im.criado_em, i.origem"
            + filtro
            + " ORDER BY im.criado_em DESC, im.id DESC LIMIT 200",
            params,
        ).fetchall()
        decisoes = conn.execute(
            "SELECT count(*) FILTER (WHERE p.aprovado_por IS NOT NULL) AS aprovados,"
            " count(*) FILTER (WHERE p.respondido_em IS NOT NULL AND p.aprovado_por IS NULL)"
            "   AS nao_aprovados,"
            " count(*) FILTER (WHERE p.respondido_em IS NULL) AS pendentes,"
            " avg(extract(epoch FROM p.respondido_em - p.criado_em))"
            "   FILTER (WHERE p.respondido_em IS NOT NULL) AS resposta_s" + planos,
            params,
        ).fetchone()
        manuais = conn.execute(
            "SELECT count(*) AS n FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id"
            " WHERE i.status = 'aguardando_decisao_manual'"
            " AND i.criado_em >= %(inicio)s AND i.criado_em < %(fim)s::date + 1"
            " AND (%(replay)s OR i.origem = 'live')"
            " AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)",
            params,
        ).fetchone()
        area = conn.execute(
            "SELECT COALESCE(sum(area_ha), 0) AS ha FROM erp.talhoes"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)",
            params,
        ).fetchone()
    por_situacao = {s["situacao"]: s for s in situacoes}
    evitada = por_situacao.get("evitada") or {"os": 0, "perda_rs": 0, "sem_fator": 0, "estimativa": False}
    total = float(evitada["perda_rs"])
    resultado = {
        "inicio": inicio,
        "fim": fim,
        "total_rs": total,
        "total": reais(total),
        "os_protegidas": evitada["os"],
        "sem_fator": evitada["sem_fator"],
        "estimativa": bool(evitada["estimativa"]),
        "em_aberto": reais(float((por_situacao.get("suspensa") or {}).get("perda_rs", 0))),
        "situacoes": [
            {
                "rotulo": rotulo,
                "os": por_situacao[s]["os"],
                "valor": reais(float(por_situacao[s]["perda_rs"])),
            }
            for s, rotulo in SITUACOES_IMPACTO.items()
            if s in por_situacao
        ],
        "por_talhao": _com_barras(por_talhao),
        "por_produto": _com_barras(por_produto),
        "planos": {
            "aprovados": decisoes["aprovados"],
            "nao_aprovados": decisoes["nao_aprovados"],
            "pendentes": decisoes["pendentes"],
            "manuais": manuais["n"],
            "tempo_resposta": None
            if decisoes["resposta_s"] is None
            else _duracao(float(decisoes["resposta_s"])),
        },
        "linhas": [
            {
                **linha,
                "perda": "sem fator" if linha["perda_rs"] is None else reais(float(linha["perda_rs"])),
                "estimativa": str(linha["fonte"]).lower().startswith("estimativa"),
                "situacao_rotulo": SITUACOES_IMPACTO.get(linha["situacao"], linha["situacao"]),
            }
            for linha in linhas
        ],
        "retorno": None,
    }
    # Retorno sobre a assinatura: só aparece com o preço por hectare configurado (PRECO_HA_SAFRA).
    preco = float(os.environ.get("PRECO_HA_SAFRA") or 0)
    if preco > 0 and float(area["ha"]) > 0:
        custo = preco * float(area["ha"])
        resultado["retorno"] = {
            "custo": reais(custo),
            "area": f"{float(area['ha']):.0f}".replace(".", ","),
            "preco": reais(preco),
            "vezes": f"{total / custo:.1f}".replace(".", ","),
        }
    return resultado


def _com_barras(linhas: list[dict]) -> list[dict]:
    """Acrescenta o valor formatado e a largura da barra (0 a 100) em relação ao maior."""
    maior = max((float(linha["perda_rs"]) for linha in linhas), default=0)
    return [
        {
            **linha,
            "valor": reais(float(linha["perda_rs"])),
            "largura": round(100 * float(linha["perda_rs"]) / maior) if maior else 0,
        }
        for linha in linhas
    ]


# --- G2 e G3: agenda e criação de OS ----------------------------------------------------------
def listar_insumos(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, fazenda_id, produto, tipo, unidade, estoque FROM erp.insumos"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY produto, id",
            {"faz": fazenda_id},
        ).fetchall()


def listar_talhoes(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, fazenda_id, nome, area_ha, cultura FROM erp.talhoes"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY id",
            {"faz": fazenda_id},
        ).fetchall()


def maquinas_livres(inicio: datetime, fim: datetime, talhao_id: int | None = None) -> list[dict]:
    """Pulverizadores livres no intervalo, pela mesma regra do MCP (`disponibilidade_maquinas`):
    com o `talhao_id`, só os da fazenda do talhão."""
    return leitura.disponibilidade_maquinas(inicio, fim, "pulverizador", talhao_id)


def listar_operadores(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, fazenda_id, nome FROM erp.operadores"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY nome",
            {"faz": fazenda_id},
        ).fetchall()


# --- G7: conclusão da OS e relatórios ---------------------------------------------------------
def ordens_a_concluir(fazenda_id: int | None = None) -> list[dict]:
    """OS que podem ser concluídas: agendadas ou reagendadas, da mais próxima para a mais distante."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id, os.inicio_previsto, os.duracao_horas, os.dose_por_ha, os.status,
                   t.nome AS talhao, t.area_ha, i.produto, i.unidade, m.nome AS maquina, o.nome AS operador
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            LEFT JOIN erp.operadores o ON o.id = os.operador_id
            WHERE os.status IN ('agendada', 'reagendada')
              AND (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY os.inicio_previsto, os.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def ordem(os_id: int) -> dict | None:
    return next((o for o in ordens_a_concluir() if o["id"] == os_id), None)


def listar_relatorios(fazenda_id: int | None = None) -> list[dict]:
    """A última versão do relatório de cada OS executada (a OS sem relatório aparece para gerar)."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id AS os_id, t.nome AS talhao, i.produto, e.inicio_real, e.fim_real, e.modalidade,
                   e.area_aplicada_ha, r.id AS relatorio_id, r.versao, r.gerado_em, r.conforme, r.sha256
            FROM erp.execucoes_os e
            JOIN erp.ordens_servico os ON os.id = e.os_id
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN LATERAL (
                SELECT id, versao, gerado_em, conforme, sha256 FROM agente.relatorios_aplicacao
                WHERE os_id = os.id ORDER BY versao DESC LIMIT 1
            ) r ON true
            WHERE (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY e.fim_real DESC, os.id DESC
            """,
            {"faz": fazenda_id},
        ).fetchall()


def pdf_do_relatorio(relatorio_id: int) -> dict | None:
    with conectar() as conn:
        return conn.execute(
            "SELECT os_id, versao, pdf FROM agente.relatorios_aplicacao WHERE id = %s", (relatorio_id,)
        ).fetchone()
